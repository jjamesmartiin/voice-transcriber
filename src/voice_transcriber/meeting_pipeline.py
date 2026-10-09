"""Meeting-mode batch pipeline: diarize, then transcribe each turn.

This is the join between the two halves of meeting mode. :mod:`voice_transcriber.meeting`
captures a long recording and hands the read-back audio to an ``on_audio``
consumer; this module is that consumer. It runs **after the user stops**, as a
background batch job, and it must never block dictation
(``docs/plan-diarization.md`` sec 7; ``docs/agent_testing_workflow.md``).

The central design decision (``docs/plan-diarization.md`` sec 3) is **diarize
first, then transcribe each turn**:

1. resample the recording to 16 kHz mono;
2. ``diarize.diarize()`` over the whole recording;
3. slice the audio at the diarizer's turn boundaries and call the existing ASR
   **once per turn** -- the turn boundaries *are* the ASR inputs, so no
   timestamp alignment is ever needed;
4. post-process each turn with the turn's own text. A turn boundary is a
   **hard** boundary: the whole meeting is never handed to the post-processor as
   one string, or retraction/list logic would run across a speaker change;
5. render and write the transcript.

Two properties are load-bearing and are tested rather than assumed:

* **Diarization is an enhancement, not a prerequisite.** ``diarize.diarize()``
  returns ``[]`` when unavailable, and an empty list means "one speaker, one
  transcript" -- the meeting is still transcribed.
* **Dictation has priority.** Before starting each turn the pipeline yields
  while a dictation is live, and it holds the shared ASR ``inference_lock`` only
  for the duration of one turn. The worst-case added dictation latency is the
  remainder of one in-flight turn's ASR, never the whole meeting.

The pipeline is cancellable (the engine's quit path sets the session's cancel
event) and runs on a daemon thread, so it can never block app exit.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from voice_transcriber import diarize as _diarize_mod
from voice_transcriber import meeting as _meeting_mod

logger = logging.getLogger(__name__)

#: Everything downstream of the diarizer assumes 16 kHz mono float32.
TARGET_SAMPLE_RATE = 16000

#: Environment override for the transcript directory (``models_dir``-style).
OUTPUT_DIR_ENV = "VT_MEETING_OUTPUT_DIR"

#: Subdirectory of the per-user data dir used when nothing overrides it.
OUTPUT_DIR_NAME = "meetings"

#: Relative weight of the diarization stage in the reported percentage. The
#: remaining ``100 - DIARIZE_SHARE`` belongs to the per-turn ASR fan-out.
DIARIZE_SHARE = 40

#: Denominator for the reported work units. ``meeting.progress_percent`` turns
#: ``(processed, total)`` into ``0..100``; using 100 here keeps the numbers a
#: user sees in ``status`` and the percentage identical.
_TOTAL_UNITS = 100

#: Never hand the ASR more than this in one call. Cohere chunks internally at
#: ``max_audio_clip_s: 35``; a turn is already a natural boundary, so this is a
#: defensive cap, not the normal path. Bounding it bounds the residual dictation
#: latency (the remainder of one in-flight turn).
MAX_TURN_S = 35.0

#: How long to sleep between checks of the dictation flag. Never a busy-wait.
DEFAULT_POLL_S = 0.1


# ---------------------------------------------------------------------------
# Output location (models_dir-style resolution, no settings key required)
# ---------------------------------------------------------------------------

def _repo_root() -> str | None:
    """The checkout root when running from one; ``None`` for an installed app."""
    try:
        from voice_transcriber import model_download
        return model_download.find_repo_root()
    except Exception:  # pragma: no cover - defensive
        return None


def meeting_output_dir(
    explicit: str | os.PathLike | None = None,
    *,
    setting: str | os.PathLike | None = None,
) -> str:
    """Directory the transcript artifact is written to.

    Resolution order:

    1. an explicit argument (the seam tests and the pipeline's own override use);
    2. ``$VT_MEETING_OUTPUT_DIR`` (an env override always beats config, as
       everywhere else);
    3. the ``meeting_output_dir`` setting (repo-relative by default: ``meetings``);
    4. ``<repo checkout>/meetings``, or ``<per-user data dir>/meetings`` for a
       read-only install with no checkout (Nix/AppImage).

    A relative setting is resolved against the repo root when there is one, so
    the shipped default (``meetings``) lands inside the project and is
    gitignored, rather than in a user data dir the user has to go looking for.
    """
    if explicit:
        return os.path.abspath(os.path.expanduser(str(explicit)))
    override = os.environ.get(OUTPUT_DIR_ENV, "").strip()
    if override:
        return os.path.abspath(os.path.expanduser(override))
    configured = "" if setting is None else str(setting).strip()
    if configured:
        expanded = os.path.expanduser(configured)
        if os.path.isabs(expanded):
            return os.path.abspath(expanded)
        return os.path.abspath(os.path.join(_repo_root() or os.getcwd(), expanded))
    root = _repo_root()
    if root:
        return os.path.join(root, OUTPUT_DIR_NAME)
    from voice_transcriber import model_download

    return os.path.join(model_download.get_data_dir(), OUTPUT_DIR_NAME)


# ---------------------------------------------------------------------------
# The transcript value object
# ---------------------------------------------------------------------------

@dataclass
class TranscriptTurn:
    """One diarized turn and the text produced for it."""

    speaker: int
    start: float
    end: float
    text: str
    overlap: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class MeetingTranscript:
    """The finished (or partially finished) meeting document.

    Kept in memory even when the file write fails, so the control API ``status``
    and a test can see the result without touching the filesystem.
    """

    turns: list[TranscriptTurn] = field(default_factory=list)
    text: str = ""
    path: str | None = None
    duration_s: float = 0.0
    speaker_count: int = 0
    created_at: str = ""
    labelled: bool = False
    diarized: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.cancelled


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------

def _prepare_audio(audio: Any, sample_rate: Any) -> np.ndarray:
    """Return mono float32 16 kHz samples, resampling only when needed."""
    if audio is None:
        raise ValueError("no audio to process")
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.reshape(samples.shape[0], -1).mean(axis=1)
    if not sample_rate or int(sample_rate) <= 0:
        raise ValueError(f"invalid sample rate {sample_rate!r}")
    rate = int(sample_rate)
    if rate == TARGET_SAMPLE_RATE:
        return np.ascontiguousarray(samples, dtype=np.float32)

    from math import gcd

    from scipy.signal import resample_poly

    divisor = gcd(TARGET_SAMPLE_RATE, rate)
    resampled = resample_poly(
        samples, TARGET_SAMPLE_RATE // divisor, rate // divisor
    )
    return np.ascontiguousarray(resampled, dtype=np.float32)


def _slice_samples(samples: np.ndarray, start: float, end: float) -> np.ndarray:
    """The samples belonging to ``[start, end)`` seconds."""
    first = max(0, int(round(float(start) * TARGET_SAMPLE_RATE)))
    last = min(len(samples), int(round(float(end) * TARGET_SAMPLE_RATE)))
    if last <= first:
        return samples[0:0]
    return samples[first:last]


def format_duration(seconds: float) -> str:
    """``MM:SS``, or ``H:MM:SS`` past an hour."""
    total = max(0, int(round(float(seconds or 0.0))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------

class MeetingPipeline:
    """Diarize -> per-turn ASR -> per-turn post-processing -> transcript.

    ``process`` is the ``on_audio`` seam: it is called on the meeting session's
    finalize daemon thread with the read-back audio and its sample rate. It
    never raises; a failure is reported through the returned
    :class:`MeetingTranscript`.

    Injection seams (all optional, all default to the real thing) keep the
    pipeline model-free to test: ``diarizer``, ``transcriber`` and
    ``post_processor``. ``is_dictating`` is the engine's live recording flag;
    ``progress`` and ``stage_callback`` drive the existing meeting status seams.
    """

    def __init__(
        self,
        *,
        is_dictating: Callable[[], bool] | None = None,
        progress: Callable[[int, int], None] | None = None,
        stage_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
        output_dir: str | os.PathLike | None = None,
        output_dir_getter: Callable[[], Any] | None = None,
        output_format_getter: Callable[[], Any] | None = None,
        sample_rate: int = TARGET_SAMPLE_RATE,
        diarizer: Callable[..., list] | None = None,
        transcriber: Callable[[np.ndarray], str] | None = None,
        post_processor: Callable[[str], str] | None = None,
        diarization_gate: Callable[[], bool] | None = None,
        speakers_hint: Callable[[], Any] | None = None,
        speaker_names_getter: Callable[[], Any] | None = None,
        on_done: Callable[[MeetingTranscript], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_s: float = DEFAULT_POLL_S,
        max_turn_s: float = MAX_TURN_S,
        label: bool | None = None,
        now: Callable[[], _dt.datetime] | None = None,
    ) -> None:
        self._is_dictating = is_dictating or (lambda: False)
        self._progress = progress
        self._stage_callback = stage_callback
        self._cancel = cancel_event if cancel_event is not None else threading.Event()
        self._output_dir = output_dir
        # D5: the output location and form are settings read at write time, and
        # the speakers map is live metadata read at render time. ``None`` keeps
        # the pre-D5 behaviour (data-dir location, text form, numeric labels).
        self._output_dir_getter = output_dir_getter
        self._output_format_getter = output_format_getter
        self._speaker_names_getter = speaker_names_getter
        self._sample_rate = int(sample_rate)
        self._diarizer = diarizer
        self._transcriber = transcriber
        self._post_processor = post_processor
        # Optional live gates: the engine injects the diarization on/off setting
        # and the speaker-count hint so D6 can change them without a restart.
        # ``None`` keeps the pre-D6 behaviour (always diarize, auto count), which
        # is what the model-free tests rely on.
        self._diarization_gate = diarization_gate
        self._speakers_hint = speakers_hint
        self._on_done = on_done
        self._sleep = sleep
        self._poll_s = max(0.0, float(poll_s))
        self._max_turn_s = max(1.0, float(max_turn_s))
        self._label_override = label
        self._now = now or _dt.datetime.now

        self._stage = "idle"
        self._last: MeetingTranscript | None = None
        self._lock = threading.Lock()

    # -- introspection -----------------------------------------------------
    @property
    def stage(self) -> str:
        return self._stage

    @property
    def last_transcript(self) -> MeetingTranscript | None:
        with self._lock:
            return self._last

    @property
    def cancel_event(self) -> threading.Event:
        return self._cancel

    def cancel(self) -> None:
        """Ask the pipeline to stop at the next turn boundary."""
        self._cancel.set()

    # -- the on_audio seam -------------------------------------------------
    def process(self, audio: Any, sample_rate: int | None = None) -> MeetingTranscript:
        """Run the whole batch pipeline. Never raises."""
        created = self._now()
        try:
            samples = _prepare_audio(audio, sample_rate or self._sample_rate)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("meeting pipeline could not read the audio: %s", exc)
            return self._finish(MeetingTranscript(
                created_at=created.strftime("%Y-%m-%d %H:%M:%S"),
                error=f"{type(exc).__name__}: {exc}",
            ))

        try:
            return self._finish(self._run(samples, created))
        except Exception as exc:  # a batch failure must never escape the thread
            logger.warning("meeting pipeline failed", exc_info=True)
            return self._finish(MeetingTranscript(
                created_at=created.strftime("%Y-%m-%d %H:%M:%S"),
                error=f"{type(exc).__name__}: {exc}",
            ))

    def _run(self, samples: np.ndarray, created: _dt.datetime) -> MeetingTranscript:
        duration = len(samples) / float(TARGET_SAMPLE_RATE)
        created_text = created.strftime("%Y-%m-%d %H:%M:%S")

        if self._cancel.is_set():
            return MeetingTranscript(
                duration_s=duration, created_at=created_text, cancelled=True
            )

        self._set_stage("diarizing")
        self._report(0, _TOTAL_UNITS)
        diarized_turns = self._diarize(samples)
        diarized = bool(diarized_turns)
        if not diarized_turns:
            # "one speaker, one transcript": the documented fail-safe.
            diarized_turns = [_diarize_mod.Turn(0.0, duration, 0)]
        labelled = self._should_label(diarized_turns)
        # A turn longer than the ASR cap is split into same-speaker segments,
        # never truncated: the cap bounds the worst-case dictation latency, and
        # dropping a turn's tail would silently lose real speech.
        segments: list = []
        for turn in diarized_turns:
            segments.extend(_expand_turn(turn, duration, self._max_turn_s))
        total_turns = len(segments)

        self._set_stage("transcribing")
        produced: list[TranscriptTurn] = []
        cancelled = False
        for index, turn in enumerate(segments):
            if self._cancel.is_set():
                cancelled = True
                break
            start, end = float(turn.start), float(turn.end)
            segment = _slice_samples(samples, start, end)
            if segment.size == 0:
                continue
            try:
                text = self._transcribe_turn(segment)
            except Exception:
                # Failures are local: one bad turn must not cost the meeting.
                logger.warning(
                    "meeting turn %d failed to transcribe", index, exc_info=True
                )
                text = ""
            if text is None:  # cancelled while waiting for the ASR lock
                cancelled = True
                break
            try:
                cleaned = self._post_process_turn(text)
            except Exception:
                logger.warning(
                    "meeting turn %d failed to post-process", index, exc_info=True
                )
                cleaned = text
            produced.append(TranscriptTurn(
                speaker=int(turn.speaker),
                start=start,
                end=end,
                text=cleaned,
                overlap=bool(getattr(turn, "overlap", False)),
            ))
            fraction = (index + 1) / float(total_turns)
            self._report(
                DIARIZE_SHARE + int(fraction * (_TOTAL_UNITS - DIARIZE_SHARE)),
                _TOTAL_UNITS,
            )

        speakers = len({item.speaker for item in produced})
        speaker_names = self._speaker_names()
        if cancelled:
            # A cancelled run leaves no artifact; the partial transcript stays in
            # memory so ``status`` can still show what got done.
            body = self._render_body(produced, labelled, speaker_names)
            return MeetingTranscript(
                turns=produced,
                text=body,
                path=None,
                duration_s=duration,
                speaker_count=speakers,
                created_at=created_text,
                labelled=labelled,
                diarized=diarized,
                cancelled=True,
            )

        self._set_stage("rendering")
        fmt = self._output_format()
        rendered = self._render(
            produced, labelled, duration, created_text, speaker_names, fmt
        )
        path: str | None = None
        error: str | None = None
        try:
            path = self._write(rendered, created, fmt)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("could not write the meeting transcript: %s", exc)
        self._report(_TOTAL_UNITS, _TOTAL_UNITS)
        return MeetingTranscript(
            turns=produced,
            text=rendered,
            path=path,
            duration_s=duration,
            speaker_count=speakers,
            created_at=created_text,
            labelled=labelled,
            diarized=diarized,
            error=error,
        )

    # -- stages ------------------------------------------------------------
    def _diarize(self, samples: np.ndarray) -> list:
        # The setting is hot: read it at run time, not construction time, so a
        # user can switch speaker labels off without restarting. ``None`` means
        # no gate was injected and the pass always runs (pre-D6 behaviour).
        if self._diarization_gate is not None:
            try:
                if not self._diarization_gate():
                    return []
            except Exception:
                logger.warning("diarization gate failed; treating as off", exc_info=True)
                return []
        num_speakers = None
        if self._speakers_hint is not None:
            try:
                num_speakers = _diarize_mod.parse_speakers(self._speakers_hint())
            except Exception:
                num_speakers = None
        kwargs: dict = {"progress": self._on_diarize_progress}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        try:
            if self._diarizer is not None:
                return list(self._diarizer(
                    samples, TARGET_SAMPLE_RATE, **kwargs,
                ))
            return list(_diarize_mod.diarize(
                samples, TARGET_SAMPLE_RATE, **kwargs,
            ))
        except Exception:
            # Diarization is an enhancement: a broken backend must still leave a
            # usable one-speaker transcript, never cost the user the meeting.
            logger.warning(
                "meeting diarization failed; transcribing as one speaker",
                exc_info=True,
            )
            return []

    def _on_diarize_progress(self, processed: int, total: int) -> None:
        percent = _meeting_mod.progress_percent(processed, total)
        self._report(int(percent * DIARIZE_SHARE / 100.0), _TOTAL_UNITS)

    def _transcribe_turn(self, segment: np.ndarray) -> str | None:
        """Transcribe one turn, yielding to a live dictation first.

        Returns ``None`` when the run was cancelled. The dictation flag is
        checked **outside** the lock, then re-checked **inside** it, so a
        dictation that began while this turn waited is never overtaken: if it
        wins the race for the lock, the lock is released and this turn retries.
        """
        from voice_transcriber import transcribe2

        while not self._cancel.is_set():
            while self._is_dictating():
                if self._cancel.is_set():
                    return None
                self._sleep(self._poll_s)
            if self._cancel.is_set():
                return None
            with transcribe2.inference_lock:
                if self._is_dictating():
                    continue
                return self._call_transcriber(segment)
        return None

    def _call_transcriber(self, segment: np.ndarray) -> str:
        if self._transcriber is not None:
            return self._transcriber(segment)
        from voice_transcriber import transcribe2

        return transcribe2.transcribe_audio(
            audio_data=segment, sample_rate=TARGET_SAMPLE_RATE
        )

    def _post_process_turn(self, text: str) -> str:
        if not text:
            return ""
        if self._post_processor is not None:
            return self._post_processor(text)
        from voice_transcriber import post_processor

        return post_processor.clean_speech_transcription(text, skip_slm=True)

    def _should_label(self, turns: list) -> bool:
        if self._label_override is not None:
            return bool(self._label_override)
        return _diarize_mod.should_label(turns)

    # -- rendering ---------------------------------------------------------
    # -- live settings, read at render/write time --------------------------
    def _speaker_names(self) -> list:
        if self._speaker_names_getter is None:
            return []
        try:
            return list(self._speaker_names_getter() or [])
        except Exception:
            logger.debug("speaker-name getter failed", exc_info=True)
            return []

    def _output_format(self) -> str:
        fmt = "text"
        if self._output_format_getter is not None:
            try:
                fmt = str(self._output_format_getter() or "text").strip().lower()
            except Exception:
                fmt = "text"
        return fmt if fmt in ("text", "json", "markdown") else "text"

    def _output_setting(self):
        if self._output_dir_getter is None:
            return None
        try:
            return self._output_dir_getter()
        except Exception:
            logger.debug("output-dir getter failed", exc_info=True)
            return None

    # -- rendering ---------------------------------------------------------
    @staticmethod
    def speaker_label(index: int, names: list) -> str:
        """The label for a speaker: a name when one is set, else ``Speaker N``.

        The speakers map is an ordered list of names; index *i* names speaker
        *i*. A slot left blank (or an index past the end of the list) falls back
        to the readable numeric label, so a partial map still renders.
        """
        try:
            idx = int(index)
        except (TypeError, ValueError):
            idx = 0
        if 0 <= idx < len(names):
            name = str(names[idx]).strip()
            if name:
                return name
        return f"Speaker {idx + 1}"

    @classmethod
    def _render_body(cls, turns: list, labelled: bool, speaker_names=None) -> str:
        names = list(speaker_names or [])
        lines: list[str] = []
        for turn in turns:
            text = (turn.text or "").strip()
            if labelled:
                lines.append(f"[{cls.speaker_label(turn.speaker, names)}] {text}".rstrip())
            else:
                lines.append(text)
        return "\n".join(lines)

    def _render(
        self,
        turns: list,
        labelled: bool,
        duration: float,
        created_text: str,
        speaker_names=None,
        fmt: str = "text",
    ) -> str:
        names = list(speaker_names or [])
        if fmt == "json":
            return self._render_json(turns, labelled, duration, created_text, names)
        if fmt == "markdown":
            return self._render_markdown(turns, labelled, duration, created_text, names)
        return self._render_text(turns, labelled, duration, created_text, names)

    @classmethod
    def _render_text(cls, turns, labelled, duration, created_text, names) -> str:
        speakers = len({turn.speaker for turn in turns})
        header = (
            "Meeting transcript\n"
            f"Date: {created_text}\n"
            f"Duration: {format_duration(duration)}\n"
            f"Speakers: {speakers}\n"
        )
        return header + "\n" + cls._render_body(turns, labelled, names) + "\n"

    @classmethod
    def _render_markdown(cls, turns, labelled, duration, created_text, names) -> str:
        speakers = len({turn.speaker for turn in turns})
        lines = [
            "# Meeting transcript",
            "",
            f"- **Date:** {created_text}",
            f"- **Duration:** {format_duration(duration)}",
            f"- **Speakers:** {speakers}",
            "",
        ]
        for turn in turns:
            text = (turn.text or "").strip()
            if labelled:
                lines.append(
                    f"**{cls.speaker_label(turn.speaker, names)}:** {text}".rstrip()
                )
            else:
                lines.append(text)
        return "\n".join(lines) + "\n"

    @classmethod
    def _render_json(cls, turns, labelled, duration, created_text, names) -> str:
        speakers = len({turn.speaker for turn in turns})
        payload = {
            "date": created_text,
            "duration": format_duration(duration),
            "duration_s": round(float(duration), 3),
            "speakers": speakers,
            "labelled": bool(labelled),
            "turns": [
                {
                    "speaker": int(turn.speaker),
                    "name": cls.speaker_label(turn.speaker, names) if labelled else None,
                    "start": round(float(turn.start), 3),
                    "end": round(float(turn.end), 3),
                    "text": (turn.text or "").strip(),
                    "overlap": bool(getattr(turn, "overlap", False)),
                }
                for turn in turns
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    _EXTENSIONS = {"text": ".txt", "json": ".json", "markdown": ".md"}

    def _write(self, rendered: str, created: _dt.datetime, fmt: str = "text") -> str:
        """Write the transcript atomically. Leaves no temp file on any path."""
        directory = meeting_output_dir(self._output_dir, setting=self._output_setting())
        os.makedirs(directory, exist_ok=True)
        base = "meeting-" + created.strftime("%Y%m%d-%H%M%S")
        ext = self._EXTENSIONS.get(fmt, ".txt")
        path = os.path.join(directory, base + ext)
        counter = 1
        while os.path.exists(path):
            path = os.path.join(directory, f"{base}-{counter}{ext}")
            counter += 1
        temp = f"{path}.{os.getpid()}.tmp"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                try:
                    os.unlink(temp)
                except OSError:  # pragma: no cover - already gone
                    pass
        return path

    # -- bookkeeping -------------------------------------------------------
    def _set_stage(self, stage: str) -> None:
        if stage == self._stage:
            return
        self._stage = stage
        callback = self._stage_callback
        if callback is None:
            return
        try:
            callback(stage)
        except Exception:
            logger.debug("meeting stage callback failed", exc_info=True)

    def _report(self, processed: int, total: int) -> None:
        callback = self._progress
        if callback is None:
            return
        try:
            callback(int(processed), int(total))
        except Exception:
            logger.debug("meeting progress callback failed", exc_info=True)

    def _finish(self, transcript: MeetingTranscript) -> MeetingTranscript:
        with self._lock:
            self._last = transcript
        self._set_stage("idle")
        callback = self._on_done
        if callback is not None:
            try:
                callback(transcript)
            except Exception:
                logger.debug("meeting completion callback failed", exc_info=True)
        return transcript


def _expand_turn(turn: Any, duration: float, max_turn_s: float) -> list:
    """Clamp a turn to the audio, splitting it at ``max_turn_s`` if needed.

    The cap exists only to bound the worst-case dictation latency (the
    remainder of one in-flight ASR call) and to match Cohere's own internal
    35 s chunking. It never truncates: a longer turn becomes consecutive
    same-speaker segments, so no speech is lost and the sum of the segments is
    the original turn.
    """
    start = max(0.0, float(getattr(turn, "start", 0.0)))
    end = min(duration, float(getattr(turn, "end", duration)))
    if end < start:
        end = start
    speaker = int(turn.speaker)
    overlap = bool(getattr(turn, "overlap", False))
    if end - start <= max_turn_s:
        return [_diarize_mod.Turn(start, end, speaker, overlap)]
    segments: list = []
    cursor = start
    while cursor < end:
        stop = min(cursor + max_turn_s, end)
        segments.append(_diarize_mod.Turn(cursor, stop, speaker, overlap))
        cursor = stop
    return segments
