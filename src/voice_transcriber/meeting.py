"""Meeting-mode capture: a second, non-injecting recording lifecycle.

Dictation is push-to-talk and seconds long: it captures, transcribes and
**injects** typed text. Meeting mode is start/stop and minutes-to-hours long: it
captures, *holds* the audio, and produces a document (a speaker-labelled
transcript) after the user stops. The two lifecycles share no state on purpose,
so a long meeting capture can never clear the dictation stop flag or otherwise
disturb push-to-talk.

Milestone D2 builds only the capture side:

* the start/stop state machine (:class:`MeetingSession`, :class:`MeetingState`),
* long capture retention with a temp-file spill past a threshold
  (:class:`AudioSpillBuffer`), and
* progress plumbing that the batch phase (diarization, D4) will drive.

It deliberately does **not** diarize, slice turns or transcribe. See
``docs/meeting_mode.md`` and ``docs/plan-diarization.md`` sec 5.2.

Two hard requirements, both here:

1. **Memory.** 16 kHz mono float32 is 64 000 bytes/s, so an hour is ~230 MB and
   the capture path copies the array several times. Past
   :data:`DEFAULT_SPILL_MINUTES` the in-memory tail is written to a temp file
   and read back once, after stop.
2. **Progress.** :meth:`MeetingSession.status` carries a percentage in the same
   configured/effective reporting style the rest of the engine uses, so the
   control API ``status`` and the TUI can both show that a long job is running.
"""

from __future__ import annotations

import enum
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spill threshold - the arithmetic, stated once
# ---------------------------------------------------------------------------
#
# Dictation audio is 16 kHz mono float32: 16 000 frames/s x 4 bytes = 64 000
# bytes/s (62.5 KiB/s). An hour is therefore 3600 x 64 000 = 230.4 MB, and that
# is before the copies the capture path makes (the in-memory chunk list, the
# concatenation on read-back, and the diarizer's own working array). Two hours
# is 460 MB, which is where a 32-bit or memory-pressured host starts to hurt.
#
# So the in-memory tail is capped at DEFAULT_SPILL_MINUTES = 10 minutes:
# 600 s x 64 000 B/s = 38.4 MB, and everything older lives in a temp file that
# is read back exactly once, after stop. Memory is therefore bounded by the
# threshold regardless of how long the meeting runs.

#: Bytes per float32 sample.
_BYTES_PER_SAMPLE = 4

#: Shipped spill threshold, in minutes. The plan suggests ~10 minutes.
DEFAULT_SPILL_MINUTES = 10

#: Values the settings modal cycles through (the config accepts any 1..240).
SPILL_MINUTE_CHOICES = (5, 10, 20, 30, 60)

#: Accepted range for the spill threshold, so a typo cannot disable the spill.
MIN_SPILL_MINUTES = 1
MAX_SPILL_MINUTES = 240


def normalize_spill_minutes(value: Any) -> int:
    """Map the ``meeting_spill_minutes`` setting onto a safe integer.

    Unknown/unparseable values fall back to the shipped default rather than to
    a large number: the whole point of the knob is to *bound* memory, so a typo
    must not be the one thing that lets a capture grow without limit.
    """
    if isinstance(value, bool):
        return DEFAULT_SPILL_MINUTES
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_SPILL_MINUTES
    if minutes < MIN_SPILL_MINUTES or minutes > MAX_SPILL_MINUTES:
        return DEFAULT_SPILL_MINUTES
    return minutes


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class MeetingState(str, enum.Enum):
    """The three states a meeting capture can be in."""

    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"


class MeetingStateError(RuntimeError):
    """An illegal lifecycle transition (start twice, stop when not started)."""


class MeetingDisabledError(MeetingStateError):
    """Meeting mode is switched off, so a capture may not start."""


def progress_percent(processed: Any, total: Any) -> int:
    """Percentage of a batch job complete, clamped to ``0..100``.

    The denominator comes from the diarizer's own ``(processed, total)``
    callback (``docs/plan-diarization.md`` sec 4.1 note 2), never from an
    invented counter. A missing or non-positive total reads as 0 - "not started"
    - rather than as a division error.
    """
    try:
        total_i = int(total)
        processed_i = int(processed)
    except (TypeError, ValueError):
        return 0
    if total_i <= 0 or processed_i <= 0:
        return 0
    if processed_i >= total_i:
        return 100
    return int(round(100.0 * processed_i / total_i))


@dataclass(frozen=True)
class MeetingStatus:
    """A snapshot of the capture lifecycle, safe to serialise into ``status``."""

    state: str
    elapsed_s: float
    progress_pct: int
    processed: int
    total: int
    duration_s: float
    spill_minutes: float
    spilled: bool
    error: str | None = None

    @property
    def recording(self) -> bool:
        return self.state == MeetingState.RECORDING.value

    @property
    def processing(self) -> bool:
        return self.state == MeetingState.PROCESSING.value


# ---------------------------------------------------------------------------
# Retention: an in-memory tail with a temp-file spill
# ---------------------------------------------------------------------------

class AudioSpillBuffer:
    """Collect a long mono float32 recording without growing memory forever.

    Chunks are appended to a list until the in-memory tail reaches
    ``spill_after_frames``; then the list is flushed to a temp file and every
    later chunk is appended there instead. :meth:`read_all` concatenates the two
    back into one array, in order, once - so the caller pays the full-meeting
    allocation exactly once, after stop.

    The temp file is removed by :meth:`close`, which the session calls in a
    ``finally`` on every exit path (success, capture failure, finalize failure)
    and which also runs from ``__del__`` as a backstop for a caller that never
    closes. Nothing from the meeting is left on disk afterwards.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        spill_after_s: float = DEFAULT_SPILL_MINUTES * 60,
        directory: str | None = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.spill_after_frames = max(1, int(spill_after_s * self.sample_rate))
        self._directory = directory
        self._chunks: list[np.ndarray] = []
        self._memory_frames = 0
        self._total_frames = 0
        self._spilled_frames = 0
        self._handle: Any = None
        self._path: str | None = None
        self._closed = False

    # -- introspection -----------------------------------------------------
    @property
    def total_frames(self) -> int:
        return self._total_frames

    @property
    def memory_frames(self) -> int:
        return self._memory_frames

    @property
    def spilled(self) -> bool:
        return self._path is not None

    @property
    def temp_path(self) -> str | None:
        return self._path

    @property
    def duration_s(self) -> float:
        return self._total_frames / float(self.sample_rate)

    @property
    def closed(self) -> bool:
        return self._closed

    # -- writing -----------------------------------------------------------
    def append(self, chunk: Any) -> None:
        """Add one mono chunk. Spills to disk once the tail crosses the cap."""
        if self._closed:
            raise MeetingStateError("audio buffer is closed")
        array = np.asarray(chunk, dtype=np.float32)
        if array.ndim > 1:
            array = array.ravel()
        if array.size == 0:
            return
        self._chunks.append(array)
        self._memory_frames += int(array.size)
        self._total_frames += int(array.size)
        if self._memory_frames >= self.spill_after_frames:
            self._spill()

    def _open_temp_file(self) -> None:
        handle = tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="vt-meeting-",
            suffix=".f32",
            dir=self._directory,
            delete=False,
        )
        self._handle = handle
        self._path = handle.name

    def _spill(self) -> None:
        """Flush the in-memory tail to the temp file and free it."""
        if not self._chunks:
            return
        if self._handle is None:
            self._open_temp_file()
        for chunk in self._chunks:
            self._handle.write(chunk.tobytes())
            self._spilled_frames += int(chunk.size)
        self._chunks.clear()
        self._memory_frames = 0
        self._handle.flush()

    # -- reading -----------------------------------------------------------
    def read_all(
        self, progress: Callable[[int, int], None] | None = None
    ) -> np.ndarray:
        """Return the whole recording as one ``float32`` array, in order.

        Any tail still in memory is spilled first, so the temp file (when
        present) is the entire recording and the read is a straight
        ``frombuffer`` per block into the output. Progress is reported per block
        as ``(frames_read, total_frames)`` - the same shape the diarizer uses.
        """
        if self._closed:
            raise MeetingStateError("audio buffer is closed")

        total = self._total_frames
        output = np.empty(total, dtype=np.float32)
        if self._path is None:
            # Never spilled: a short recording lives entirely in memory, so no
            # temp file is created just to read it back.
            position = 0
            for chunk in self._chunks:
                take = int(chunk.size)
                output[position:position + take] = chunk
                position += take
                if progress is not None:
                    progress(position, total)
            return output

        # Spilled: flush the tail so the file is the whole recording.
        if self._chunks:
            self._spill()
        # Capture is over by the time anything reads the buffer back, so release
        # the write handle before reopening for read. Windows is strict about
        # concurrent access to a file opened for writing.
        if self._handle is not None:
            try:
                self._handle.flush()
            except OSError:  # pragma: no cover - defensive
                pass
            try:
                self._handle.close()
            except OSError:  # pragma: no cover - defensive
                pass
            self._handle = None

        # One second of audio per read: enough to be cheap, small enough that
        # the callback can drive a live percentage on a multi-minute read.
        frames_per_read = max(1, self.sample_rate)
        bytes_per_read = frames_per_read * _BYTES_PER_SAMPLE
        written = 0
        with open(self._path, "rb") as handle:
            while written < total:
                raw = handle.read(bytes_per_read)
                if not raw:
                    break
                block = np.frombuffer(raw, dtype=np.float32)
                take = min(int(block.size), total - written)
                output[written:written + take] = block[:take]
                written += take
                if progress is not None:
                    progress(written, total)
        if written < total:
            # A short read means a truncated spill file; return what is real
            # rather than a tail of uninitialised memory.
            logger.warning(
                "meeting spill file %s is short: %d of %d frames",
                self._path, written, total,
            )
            output = output[:written]
        return output

    # -- cleanup -----------------------------------------------------------
    def close(self) -> None:
        """Close the temp file and delete it. Idempotent, never raises."""
        if self._closed:
            return
        self._closed = True
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:  # pragma: no cover - defensive
                pass
        path = self._path
        self._path = None
        if path is not None:
            try:
                os.unlink(path)
            except OSError:  # pragma: no cover - already gone
                pass
        self._chunks.clear()
        self._memory_frames = 0

    def __enter__(self) -> "AudioSpillBuffer":
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - GC backstop
        try:
            self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Capture source
# ---------------------------------------------------------------------------

#: Signature a capture source must satisfy. ``on_chunk`` receives a mono
#: float32 numpy array or something ``np.asarray`` accepts; ``stop`` is the
#: session's own event (never ``t2.stop_recording``).
CaptureFn = Callable[[Callable[[Any], None], threading.Event, int, Any], None]


def capture_microphone(
    on_chunk: Callable[[Any], None],
    stop: threading.Event,
    sample_rate: int = 16000,
    device: Any = None,
) -> None:
    """Default capture source: stream a mono float32 mic into ``on_chunk``.

    ``sounddevice`` is imported here, not at module import, so importing
    :mod:`meeting` never touches PortAudio and a model-free test run never pays
    for the audio stack. Tests inject their own capture source instead of
    opening a device.
    """
    import queue

    import sounddevice as sd

    audio_queue: "queue.Queue[np.ndarray]" = queue.Queue()

    def callback(indata, frames, time_info, status) -> None:  # noqa: ARG001
        audio_queue.put(np.array(indata, dtype=np.float32, copy=True))

    stream = sd.InputStream(
        samplerate=int(sample_rate),
        channels=1,
        callback=callback,
        device=device,
        blocksize=1024,
        latency="low",
    )
    with stream:
        while not stop.is_set():
            try:
                chunk = audio_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            on_chunk(chunk.ravel() if chunk.ndim > 1 else chunk)
        # Drain whatever was captured between the last poll and the stop flag.
        while not audio_queue.empty():
            chunk = audio_queue.get_nowait()
            on_chunk(chunk.ravel() if chunk.ndim > 1 else chunk)


# ---------------------------------------------------------------------------
# The session
# ---------------------------------------------------------------------------

class MeetingSession:
    """Owns the meeting capture lifecycle: idle -> recording -> processing.

    The session is deliberately independent of the dictation engine. It uses its
    own stop event, its own capture thread and its own finalize thread, so
    starting or stopping a meeting cannot touch ``t2.stop_recording`` or the
    hotkey path.

    ``capture`` and ``on_audio`` are injection seams: a test (or a future host)
    supplies a synthetic capture source and receives the read-back audio.
    ``on_change`` is called on state/progress changes, throttled to ~1 Hz while
    recording, and is how the TUI gets its elapsed timer.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16000,
        capture: CaptureFn | None = None,
        directory: str | None = None,
        on_audio: Callable[[np.ndarray, int], None] | None = None,
        on_change: Callable[[MeetingStatus], None] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._sample_rate = int(sample_rate)
        self._capture = capture or capture_microphone
        self._directory = directory
        self._on_audio = on_audio
        self._on_change = on_change

        self._buffer: AudioSpillBuffer | None = None
        self._state = MeetingState.IDLE
        self._stop = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._finalize_thread: threading.Thread | None = None
        self._ticker_thread: threading.Thread | None = None
        self._started_at = 0.0
        self._elapsed_s = 0.0
        self._progress_pct = 0
        self._processed = 0
        self._total = 0
        self._duration_s = 0.0
        self._spill_minutes = float(DEFAULT_SPILL_MINUTES)
        self._device: Any = None
        self._error: str | None = None

    # -- introspection -----------------------------------------------------
    @property
    def state(self) -> MeetingState:
        with self._lock:
            return self._state

    @property
    def recording(self) -> bool:
        return self.state is MeetingState.RECORDING

    @property
    def processing(self) -> bool:
        return self.state is MeetingState.PROCESSING

    @property
    def spilled(self) -> bool:
        with self._lock:
            return bool(self._buffer is not None and self._buffer.spilled)

    @property
    def temp_path(self) -> str | None:
        with self._lock:
            return self._buffer.temp_path if self._buffer is not None else None

    def status(self) -> MeetingStatus:
        with self._lock:
            elapsed = self._elapsed_s
            if self._state is MeetingState.RECORDING:
                elapsed = time.monotonic() - self._started_at
            return MeetingStatus(
                state=self._state.value,
                elapsed_s=round(elapsed, 3),
                progress_pct=int(self._progress_pct),
                processed=int(self._processed),
                total=int(self._total),
                duration_s=round(self._duration_s, 3),
                spill_minutes=float(self._spill_minutes),
                spilled=bool(self._buffer is not None and self._buffer.spilled),
                error=self._error,
            )

    # -- lifecycle ---------------------------------------------------------
    def start(
        self,
        *,
        device: Any = None,
        spill_minutes: Any = None,
        sample_rate: int | None = None,
    ) -> MeetingStatus:
        """Begin a capture. Raises unless the session is idle."""
        with self._lock:
            if self._state is not MeetingState.IDLE:
                raise MeetingStateError(
                    f"meeting capture is already {self._state.value}"
                )
            minutes = normalize_spill_minutes(
                DEFAULT_SPILL_MINUTES if spill_minutes is None else spill_minutes
            )
            rate = int(sample_rate or self._sample_rate)
            self._spill_minutes = float(minutes)
            self._buffer = AudioSpillBuffer(
                sample_rate=rate,
                spill_after_s=minutes * 60,
                directory=self._directory,
            )
            self._stop.clear()
            self._error = None
            self._elapsed_s = 0.0
            self._progress_pct = 0
            self._processed = 0
            self._total = 0
            self._duration_s = 0.0
            self._device = device
            self._started_at = time.monotonic()
            self._state = MeetingState.RECORDING
            self._capture_thread = threading.Thread(
                target=self._run_capture, name="vt-meeting-capture", daemon=True
            )
            self._ticker_thread = threading.Thread(
                target=self._tick, name="vt-meeting-tick", daemon=True
            )
            self._capture_thread.start()
            self._ticker_thread.start()
        self._notify()
        return self.status()

    def stop(self) -> MeetingStatus:
        """Signal the capture to end. Raises unless the session is recording.

        Returns immediately; the finalize runs on its own thread so the caller
        (a control-API worker or the TUI) is never blocked by the read-back.
        """
        with self._lock:
            if self._state is not MeetingState.RECORDING:
                raise MeetingStateError(
                    f"meeting capture is not recording (state: {self._state.value})"
                )
            self._stop.set()
        self._notify()
        return self.status()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the finalize finishes. Returns whether the session is idle.

        Polls the state rather than joining ``_finalize_thread`` directly: after
        ``stop()`` the capture thread has not necessarily handed over to the
        finalize thread yet, so the thread may still be ``None`` on entry.
        """
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        while self.state is not MeetingState.IDLE:
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.005)
        return self.state is MeetingState.IDLE

    def close(self) -> None:
        """Stop a running capture and make sure the temp file is gone."""
        with self._lock:
            if self._state is MeetingState.RECORDING:
                self._stop.set()
        for thread in (self._ticker_thread, self._capture_thread, self._finalize_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=5.0)
        with self._lock:
            if self._buffer is not None and self._state is not MeetingState.IDLE:
                # The finalize never ran (or is stuck); free the spill file here.
                self._buffer.close()
                self._buffer = None
                self._state = MeetingState.IDLE

    # -- progress ----------------------------------------------------------
    def set_progress(self, processed: Any, total: Any) -> int:
        """Record batch progress and return the resulting percentage."""
        percent = progress_percent(processed, total)
        with self._lock:
            try:
                self._processed = max(0, int(processed))
            except (TypeError, ValueError):
                self._processed = 0
            try:
                self._total = max(0, int(total))
            except (TypeError, ValueError):
                self._total = 0
            self._progress_pct = percent
        self._notify()
        return percent

    # -- workers -----------------------------------------------------------
    def _append_chunk(self, chunk: Any) -> None:
        with self._lock:
            buffer = self._buffer
        if buffer is not None:
            buffer.append(chunk)

    def _tick(self) -> None:
        """Nudge the UI about once a second so the elapsed timer moves."""
        while not self._stop.wait(1.0):
            self._notify()

    def _run_capture(self) -> None:
        buffer = self._buffer
        if buffer is None:  # pragma: no cover - defensive
            return
        try:
            self._capture(self._append_chunk, self._stop, buffer.sample_rate, self._device)
        except Exception as exc:  # a device failure must not wedge the session
            logger.warning("meeting capture failed: %s", exc)
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            # Whether the stop flag ended us or the device did, the capture is
            # over; move to processing (never back to idle with audio unread).
            self._begin_finalize()

    def _begin_finalize(self) -> None:
        with self._lock:
            if self._state is not MeetingState.RECORDING:
                return
            self._state = MeetingState.PROCESSING
            self._elapsed_s = time.monotonic() - self._started_at
            self._finalize_thread = threading.Thread(
                target=self._finalize, name="vt-meeting-finalize", daemon=True
            )
            self._finalize_thread.start()
        self._notify()

    def _finalize(self) -> None:
        """Read the audio back and hand it to the consumer (D4's entry point).

        The buffer is closed in a ``finally``, so the temp file is deleted on
        every path - success, capture failure, finalize failure.
        """
        with self._lock:
            buffer = self._buffer
        try:
            capture_thread = self._capture_thread
            if capture_thread is not None and capture_thread is not threading.current_thread():
                capture_thread.join()
            if buffer is None:  # pragma: no cover - defensive
                return
            audio = buffer.read_all(progress=self._on_read_progress)
            self._duration_s = len(audio) / float(buffer.sample_rate)
            if self._on_audio is not None:
                try:
                    self._on_audio(audio, buffer.sample_rate)
                except Exception:
                    logger.warning("meeting audio consumer failed", exc_info=True)
            del audio
            with self._lock:
                if self._total > 0:
                    self._progress_pct = 100
        except Exception as exc:
            logger.warning("meeting finalize failed: %s", exc)
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if buffer is not None:
                    buffer.close()
            finally:
                with self._lock:
                    self._buffer = None
                    self._state = MeetingState.IDLE
                self._notify()

    def _on_read_progress(self, processed: int, total: int) -> None:
        self.set_progress(processed, total)

    def _notify(self) -> None:
        callback = self._on_change
        if callback is None:
            return
        try:
            callback(self.status())
        except Exception:
            logger.debug("meeting status callback failed", exc_info=True)
