"""A deterministic diarizer with no models, for tests.

This backend exists so the entire meeting-mode pipeline - capture lifecycle,
turn slicing, per-turn ASR, per-turn post-processing, rendering, progress
reporting, the single-speaker suppression rule - can be built and tested before
the ~50 MB of sherpa-onnx weights are downloaded (``plan-diarization.md`` sec 9,
"fixture" tier).

It is also a fault injector. Every way a real backend can misbehave is a module
attribute here, so the fail-safe paths in :mod:`voice_transcriber.diarize` are
tested against behaviour rather than against a mock's assumptions:

    diarizers.fake.SCRIPT = [Turn(0.0, 2.0, 0), Turn(2.0, 4.0, 1)]
    diarizers.fake.FAILURE = RuntimeError("model exploded")
    diarizers.fake.DELAY_S = 5.0

Default output is a deterministic alternation, so a test that does not care
about the exact turns still gets a stable, non-trivial result.
"""

from __future__ import annotations

import time

from voice_transcriber.diarize import Turn

#: Length of each synthesized turn when no SCRIPT is provided.
DEFAULT_TURN_S = 2.5

#: Exact turns to return. Set by a test; ``None`` means "synthesize".
SCRIPT: list[Turn] | None = None

#: If set, ``available()`` reports False.
AVAILABLE: bool = True

#: If set, ``diarize()`` raises it. Note that :func:`diarize` catches
#: ``Exception``, not ``BaseException``: a meeting capture can run for twenty
#: minutes, so Ctrl-C (``KeyboardInterrupt``) must still interrupt it.
FAILURE: BaseException | None = None

#: If set, ``warm()`` raises it.
WARM_FAILURE: BaseException | None = None

#: Seconds to sleep inside ``diarize()``, for exercising cancellation/latency.
DELAY_S: float = 0.0

#: If True, the progress callback raises on its first call.
PROGRESS_FAILURE: bool = False

#: Speaker count used by the synthesizer when the caller does not specify one.
DEFAULT_SPEAKERS = 2

#: Number of times ``warm()`` has been called, so tests can assert preloading.
WARM_CALLS = 0


def reset() -> None:
    """Return every knob to its default. Called between tests."""
    global SCRIPT, AVAILABLE, FAILURE, WARM_FAILURE, DELAY_S, PROGRESS_FAILURE
    global WARM_CALLS
    SCRIPT = None
    AVAILABLE = True
    FAILURE = None
    WARM_FAILURE = None
    DELAY_S = 0.0
    PROGRESS_FAILURE = False
    WARM_CALLS = 0


def available() -> bool:
    return AVAILABLE


def warm() -> None:
    global WARM_CALLS
    WARM_CALLS += 1
    if WARM_FAILURE is not None:
        raise WARM_FAILURE


def _synthesize(duration: float, speakers: int) -> list[Turn]:
    """Alternate speakers across ``duration`` in fixed-length turns."""
    turns: list[Turn] = []
    start = 0.0
    index = 0
    while start < duration:
        end = min(start + DEFAULT_TURN_S, duration)
        if end - start > 0:
            turns.append(Turn(start=round(start, 3), end=round(end, 3),
                              speaker=index % max(1, speakers)))
        start = end
        index += 1
    return turns


def diarize(
    audio,
    sample_rate,
    *,
    num_speakers=None,
    threshold=0.5,
    progress=None,
) -> list[Turn]:
    if FAILURE is not None:
        raise FAILURE
    if DELAY_S:
        time.sleep(DELAY_S)

    duration = len(audio) / float(sample_rate)
    turns = list(SCRIPT) if SCRIPT is not None else _synthesize(
        duration, num_speakers or DEFAULT_SPEAKERS)

    if progress is not None:
        total = max(1, len(turns))
        for index in range(len(turns)):
            if PROGRESS_FAILURE:
                raise RuntimeError("progress callback exploded")
            progress(index + 1, total)

    return turns
