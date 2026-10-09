"""Optional on-device speaker diarization (meeting mode).

Diarization answers "who spoke when" and returns ``(start, end, speaker)``
turns. It is deliberately **not** part of the dictation path: it runs as a batch
job over a whole recording after the user stops, and its output is a document
rather than typed text. Meeting mode does not participate in the
release-to-clipboard SLA (``docs/plan-diarization.md`` sec 7).

Two invariants, mirroring :mod:`formatter`:

* :func:`diarize` **never raises**. A missing backend, absent weights, a
  malformed turn list or an exploding progress callback all return ``[]``, which
  the caller reads as "one speaker, one transcript" - the off behaviour.
* Importing this module imports no backend and no ``sherpa_onnx``. A model-free
  test run never pays for the runtime, and the app can start on a machine where
  diarization was never enabled.

The backend registry mirrors :mod:`transcribe2` and :mod:`formatter` on purpose;
hardwiring a single backend caused problems there the first time.

Design and rationale: ``docs/plan-diarization.md``.
"""

from __future__ import annotations

import importlib
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------

#: Every diarization backend: a name, and the module implementing it. Swapping
#: the runtime behind the feature is a config value plus one entry here.
BACKENDS: dict[str, str] = {
    "sherpa-onnx": "voice_transcriber.diarizers.sherpa_onnx",  # default
    "fake": "voice_transcriber.diarizers.fake",                # tests, no weights
}

#: Backend used when none is named. Must be a key of :data:`BACKENDS`.
#: Until ``sherpa_onnx`` lands (milestone D3) this resolves to a module that does
#: not exist yet, which :func:`load_backend` reads as "diarization unavailable" -
#: the correct fail-safe, not a crash. The same property holds on a machine
#: where the optional dependency was never installed.
DEFAULT_BACKEND = "sherpa-onnx"

#: What a backend module must expose. ``warm`` is intentionally absent:
#: preloading is an optimisation, and a backend that cannot preload is still
#: perfectly usable. Checked on first load, so declaring a backend is free and a
#: half-added one degrades instead of breaking the app.
REQUIRED_FUNCTIONS = ("available", "diarize")

_loaded: dict[str, Any] = {}


class UnknownBackendError(ValueError):
    """A config named a diarization backend that is not in :data:`BACKENDS`."""


def available_backends() -> list[str]:
    """Every backend name a config may use, sorted."""
    return sorted(BACKENDS)


def resolve_backend_name(name: str | None = None) -> str:
    """Validate ``name`` against the registry without importing anything."""
    resolved = str(name or DEFAULT_BACKEND).strip().lower()
    if resolved not in BACKENDS:
        raise UnknownBackendError(
            f"Unknown diarization backend {resolved!r}. "
            f"Available: {', '.join(available_backends())}"
        )
    return resolved


def load_backend(name: str | None = None) -> Any | None:
    """Import and validate a backend module, or ``None`` if it is unusable.

    Never raises, for the same reason as the formatter: on a machine that never
    enabled the feature, ``sherpa_onnx`` is simply not installed, and that must
    read as "no diarization" rather than as an error at startup.
    """
    try:
        resolved = resolve_backend_name(name)
    except UnknownBackendError:
        return None
    if resolved in _loaded:
        return _loaded[resolved]
    try:
        module = importlib.import_module(BACKENDS[resolved])
    except Exception:
        module = None
    if module is not None:
        missing = [fn for fn in REQUIRED_FUNCTIONS if not hasattr(module, fn)]
        if missing:
            module = None
    _loaded[resolved] = module
    return module


def reset_backend_cache() -> None:
    """Forget loaded backends. For tests, and for a config change at runtime."""
    _loaded.clear()


def warm(backend: str | None = None) -> bool:
    """Preload models so the first real meeting is not the slow one.

    Returns whether a usable backend is ready. Never raises, and returns
    ``False`` for a backend with no ``warm`` - meeting mode is started by hand,
    so preloading is genuinely optional here.
    """
    module = load_backend(backend)
    if module is None:
        return False
    try:
        if not module.available():
            return False
        preload = getattr(module, "warm", None)
        if preload is not None:
            preload()
        return True
    except Exception:
        return False


def may_run(diarization: str) -> bool:
    """False when diarization is switched off.

    ``diarization: off`` is the default and promises the dictation engine is
    byte-identical to a build without this feature. This is a hard gate, not a
    preference: it is what keeps that promise true.
    """
    return str(diarization or "").strip().lower() != "off"


# ---------------------------------------------------------------------------
# The data contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Turn:
    """One uninterrupted stretch of speech by one speaker.

    ``speaker`` is a **0-based** index with no meaning beyond this recording:
    labels are re-derived per meeting and never persisted, so "Speaker 2" is not
    a voiceprint and does not survive into the next meeting. Rendering decides
    the label, not the backend.
    """

    start: float
    end: float
    speaker: int
    overlap: bool = False

    @property
    def duration(self) -> float:
        return self.end - self.start


# ---------------------------------------------------------------------------
# Thresholds - exposed, never re-implemented
# ---------------------------------------------------------------------------
#
# sherpa-onnx already merges adjacent same-speaker turns and drops fragments,
# using these two numbers. The plan's rule is explicit: expose the thresholds
# and do **not** add a second merging pass, because two passes with two sets of
# constants is exactly how a pipeline starts disagreeing with itself. There is
# therefore no merge_turns() here, and :func:`validate_turns` rejects malformed
# turns without ever deciding that two good turns are one.

#: Turns shorter than this are dropped by the backend.
MIN_DURATION_ON_S = 0.3

#: Gaps shorter than this are merged across by the backend.
MIN_DURATION_OFF_S = 0.5

#: Clustering threshold, used only when the speaker count is unknown.
#:
#: COUNTER-INTUITIVE, and documented because of it: a *smaller* threshold yields
#: **more** speakers, not fewer. 0.5 is the library default.
DEFAULT_THRESHOLD = 0.5

#: Below this, a single speaker is the only defensible answer, so diarizing is
#: not even attempted.
MIN_AUDIO_S = 1.0

#: Slack for float rounding when checking a turn against the audio duration.
_TOLERANCE_S = 1e-3


def parse_speakers(value: Any) -> int | None:
    """Map the ``diarization_speakers`` setting onto ``num_speakers``.

    ``None`` means "auto", which the backend passes through as
    ``num_clusters=-1``. Anything unparseable also means auto: an unknown value
    must degrade to the safe behaviour, never crash a meeting capture.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "auto", "none", "unknown"):
        return None
    try:
        count = int(text)
    except (TypeError, ValueError):
        return None
    return count if count > 0 else None


def speaker_count(turns: list[Turn]) -> int:
    """How many distinct speakers the turns mention."""
    return len({turn.speaker for turn in turns})


def should_label(turns: list[Turn]) -> bool:
    """Whether turn labels convey anything.

    A single-speaker recording labelled ``[Speaker 1]`` is pure noise, and it is
    the outcome most likely to annoy a user who recorded a monologue. Labels are
    suppressed unless the recording genuinely has more than one voice.
    """
    return speaker_count(turns) > 1


# ---------------------------------------------------------------------------
# Turn validation - the guardrail
# ---------------------------------------------------------------------------

def validate_turns(
    turns: Any, duration: float
) -> tuple[list[Turn], str | None]:
    """Clean a backend's turn list, or explain why it is unusable.

    Returns ``(usable_turns, reason)``. ``reason`` is ``None`` when nothing had
    to be dropped; otherwise it is a bug report about the backend, meant for the
    log and never for the user. Individual malformed turns are dropped rather
    than poisoning the whole result, because one bad turn should not cost the
    user a whole meeting.

    This deliberately does **not** merge or threshold - see the note above. It
    only enforces that a turn is a well-formed interval inside the audio.
    """
    if turns is None:
        return [], "backend returned None instead of a turn list"
    if not isinstance(turns, (list, tuple)):
        return [], f"backend returned {type(turns).__name__}, not a list"

    usable: list[Turn] = []
    dropped: list[str] = []

    for index, turn in enumerate(turns):
        start = getattr(turn, "start", None)
        end = getattr(turn, "end", None)
        speaker = getattr(turn, "speaker", None)

        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            dropped.append(f"turn {index} has non-numeric bounds")
            continue
        start, end = float(start), float(end)
        if not (math.isfinite(start) and math.isfinite(end)):
            dropped.append(f"turn {index} has non-finite bounds")
            continue
        if not isinstance(speaker, int) or isinstance(speaker, bool) or speaker < 0:
            dropped.append(f"turn {index} has an invalid speaker id")
            continue
        if start < -_TOLERANCE_S:
            dropped.append(f"turn {index} starts before the audio")
            continue
        if start >= duration + _TOLERANCE_S:
            dropped.append(f"turn {index} starts past the end of the audio")
            continue
        if end <= start:
            dropped.append(f"turn {index} is not a positive interval")
            continue

        # A turn that runs past the end is clamped, not dropped: the tail is
        # real speech and losing it would silently truncate the transcript.
        clamped = min(end, duration)
        if clamped <= start:
            dropped.append(f"turn {index} is empty after clamping")
            continue
        usable.append(Turn(
            start=max(0.0, start),
            end=clamped,
            speaker=speaker,
            overlap=bool(getattr(turn, "overlap", False)),
        ))

    # Sorted here rather than trusting the backend. Ordering is the one
    # property every downstream consumer depends on.
    usable.sort(key=lambda turn: (turn.start, turn.end, turn.speaker))

    if dropped:
        return usable, "; ".join(dropped)
    return usable, None


# ---------------------------------------------------------------------------
# The only entry point
# ---------------------------------------------------------------------------

def _guarded_progress(
    progress: Callable[[int, int], None] | None
) -> Callable[[int, int], None] | None:
    """Wrap a progress callback so it cannot abort a long capture.

    The UI callback runs on the diarization thread and may touch a TUI that is
    shutting down. A raised exception there would otherwise kill a job that has
    been running for twenty minutes.
    """
    if progress is None:
        return None

    def report(processed: int, total: int) -> None:
        try:
            progress(processed, total)
        except Exception:
            logger.debug("diarization progress callback failed", exc_info=True)

    return report


def diarize(
    audio: Any,
    sample_rate: int,
    *,
    num_speakers: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    progress: Callable[[int, int], None] | None = None,
    backend: str | None = None,
) -> list[Turn]:
    """Label who spoke when. Never raises; returns ``[]`` when unavailable.

    An empty list is not an error condition - it is the documented fail-safe,
    meaning "one speaker, one transcript", which is exactly what the feature
    does when it is switched off. The caller does not need to distinguish
    "disabled" from "failed", because the required behaviour is identical.
    """
    try:
        if sample_rate is None or sample_rate <= 0:
            return []
        try:
            duration = len(audio) / float(sample_rate)
        except TypeError:
            return []
        if duration < MIN_AUDIO_S:
            return []

        module = load_backend(backend)
        if module is None or not module.available():
            return []

        turns = module.diarize(
            audio,
            sample_rate,
            num_speakers=parse_speakers(num_speakers),
            threshold=float(threshold),
            progress=_guarded_progress(progress),
        )

        usable, reason = validate_turns(turns, duration)
        if reason:
            logger.warning("diarization backend %r returned bad turns: %s",
                           resolve_backend_name(backend), reason)
        return usable
    except Exception:
        logger.warning("diarization failed", exc_info=True)
        return []
