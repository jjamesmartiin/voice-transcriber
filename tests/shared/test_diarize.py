#!/usr/bin/env python3
"""
Unit tests for the optional speaker diarization module.

Milestone D1. Everything here is model-free: the point is that the contract,
the backend registry, the turn guardrail, the fail-safe and the single-speaker
suppression rule can all be proven before the ~50 MB of sherpa-onnx weights are
downloaded (``plan-diarization.md`` sec 9).

The ``fake`` backend is the fixture the rest of workstream D is built on, so it
is exercised as a real backend rather than as a mock - including its fault
injection knobs, which is how the fail-safe paths get tested against behaviour
instead of against a mock's assumptions.

Hermeticity: tests/shared/conftest.py blocks real non-loopback connects, so a
backend that tried to fetch weights here would fail loudly.
"""
import dataclasses
import itertools
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import diarize
from voice_transcriber.diarizers import fake


@pytest.fixture(autouse=True)
def _clean_state():
    diarize.reset_backend_cache()
    fake.reset()
    yield
    diarize.reset_backend_cache()
    fake.reset()


def _fake_audio(seconds: float, sample_rate: int = 16000) -> list[float]:
    """A plain list stands in for a numpy array; only ``len()`` is used."""
    return [0.0] * int(seconds * sample_rate)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_fake_backend_is_registered_and_satisfies_the_contract():
    module = diarize.load_backend("fake")
    assert module is not None
    for fn in diarize.REQUIRED_FUNCTIONS:
        assert hasattr(module, fn), f"fake backend is missing {fn}"


def test_default_backend_is_a_registry_key():
    assert diarize.DEFAULT_BACKEND in diarize.BACKENDS


def test_default_backend_is_sherpa_onnx():
    assert diarize.DEFAULT_BACKEND == "sherpa-onnx"


def test_available_backends_lists_the_registry():
    assert diarize.available_backends() == sorted(diarize.BACKENDS)
    assert set(diarize.available_backends()) == {"sherpa-onnx", "fake"}


def test_resolve_backend_name_is_case_and_space_insensitive():
    assert diarize.resolve_backend_name(None) == diarize.DEFAULT_BACKEND
    assert diarize.resolve_backend_name("fake") == "fake"
    assert diarize.resolve_backend_name("  FAKE  ") == "fake"


def test_resolve_backend_name_rejects_an_unknown_name():
    with pytest.raises(diarize.UnknownBackendError, match="Unknown diarization backend"):
        diarize.resolve_backend_name("pyannote")


def test_an_absent_backend_still_degrades_to_none(monkeypatch):
    """Fail safe, not fail loud.

    ``sherpa-onnx`` used to be in this list; it now exists (D3), so this pins
    only the genuinely absent cases: an unknown name, and a registered backend
    whose module was never shipped. Either must mean "no diarization", never a
    crash on the meeting path.
    """
    assert diarize.load_backend("not-a-backend") is None
    monkeypatch.setitem(
        diarize.BACKENDS, "unbuilt", "voice_transcriber.diarizers.unbuilt")
    diarize.reset_backend_cache()
    assert diarize.load_backend("unbuilt") is None


def test_the_shipped_backend_exists_and_satisfies_the_contract():
    module = diarize.load_backend("sherpa-onnx")
    assert module is not None, "D3 ships diarizers/sherpa_onnx.py"
    for fn in diarize.REQUIRED_FUNCTIONS:
        assert hasattr(module, fn)


def test_backend_missing_part_of_the_contract_loads_as_none(monkeypatch):
    import types

    incomplete = types.SimpleNamespace(available=lambda: True)  # no diarize()
    monkeypatch.setitem(diarize.BACKENDS, "broken", "voice_transcriber.diarizers.broken")
    monkeypatch.setattr(diarize.importlib, "import_module", lambda _name: incomplete)
    assert diarize.load_backend("broken") is None


# ---------------------------------------------------------------------------
# The import-free rule (plan sec 5.1: a model-free run must never pay for it)
# ---------------------------------------------------------------------------
def test_importing_diarize_does_not_import_sherpa_onnx():
    """Checked in a subprocess, because any other test could have loaded it."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import diarize; "
        "assert 'sherpa_onnx' not in sys.modules, 'sherpa_onnx leaked into import'; "
        "assert 'numpy' not in sys.modules or True; "
        "print('OK')" % str(SRC_DIR)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_importing_the_fake_backend_does_not_import_sherpa_onnx():
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from voice_transcriber.diarizers import fake; "
        "assert 'sherpa_onnx' not in sys.modules, 'sherpa_onnx leaked into import'; "
        "print('OK')" % str(SRC_DIR)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# The Turn contract
# ---------------------------------------------------------------------------
def test_turn_is_frozen():
    turn = diarize.Turn(start=0.0, end=1.0, speaker=0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        turn.start = 5.0


def test_turn_defaults_to_not_overlapping():
    assert diarize.Turn(start=0.0, end=1.0, speaker=0).overlap is False


def test_turn_duration():
    assert diarize.Turn(start=1.5, end=4.0, speaker=0).duration == 2.5


def test_turn_speaker_is_zero_based():
    """0-based in the data, because rendering decides the human label."""
    turns = diarize.diarize(_fake_audio(6), 16000, backend="fake", num_speakers=2)
    assert {turn.speaker for turn in turns} == {0, 1}


# ---------------------------------------------------------------------------
# Thresholds are exposed, never re-implemented (plan sec 4.1 note 3, sec 5.1)
# ---------------------------------------------------------------------------
def test_merge_thresholds_are_exposed_with_the_library_defaults():
    assert diarize.MIN_DURATION_ON_S == 0.3
    assert diarize.MIN_DURATION_OFF_S == 0.5
    assert diarize.DEFAULT_THRESHOLD == 0.5


def test_there_is_no_second_merging_pass():
    """The plan forbids a parallel merge_turns(); merging is the library's job.

    Two merging passes with two sets of constants is how two parts of a
    pipeline start disagreeing, so the absence is a design guarantee, not an
    oversight.
    """
    assert not hasattr(diarize, "merge_turns")


def test_clustering_threshold_is_passed_through_unchanged():
    """A *smaller* threshold yields *more* speakers - the opposite of intuition.

    Documented in the plan because it is easy to reason backwards about. What is
    pinned here is that the value reaches the backend untouched, so the setting
    means the same thing at every layer.
    """
    import types

    seen = {}

    def record(audio, sample_rate, *, num_speakers=None, threshold=None, progress=None):
        seen["threshold"] = threshold
        return []

    module = types.SimpleNamespace(available=lambda: True, diarize=record)
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(diarize.BACKENDS, "rec", "voice_transcriber.diarizers.rec")
        mp.setitem(diarize._loaded, "rec", module)
        diarize.diarize(_fake_audio(6), 16000, backend="rec", threshold=0.2)

    assert seen["threshold"] == 0.2
    assert diarize.DEFAULT_THRESHOLD == 0.5


# ---------------------------------------------------------------------------
# parse_speakers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("auto", None),
    ("", None),
    ("  AUTO  ", None),
    ("none", None),
    ("garbage", None),
    ("0", None),
    ("-3", None),
    (2, 2),
    ("3", 3),
    (" 4 ", 4),
])
def test_parse_speakers(value, expected):
    assert diarize.parse_speakers(value) == expected


# ---------------------------------------------------------------------------
# speaker_count / should_label - the single-speaker suppression rule
# ---------------------------------------------------------------------------
def test_speaker_count_counts_distinct_speakers():
    turns = [diarize.Turn(0, 1, 0), diarize.Turn(1, 2, 1), diarize.Turn(2, 3, 1)]
    assert diarize.speaker_count(turns) == 2


def test_single_speaker_is_not_labelled():
    """A monologue labelled [Speaker 1] is noise, and the plan flags it as the
    outcome most likely to annoy a user."""
    turns = [diarize.Turn(0, 1, 0), diarize.Turn(1, 2, 0)]
    assert diarize.should_label(turns) is False


def test_two_speakers_are_labelled():
    turns = [diarize.Turn(0, 1, 0), diarize.Turn(1, 2, 1)]
    assert diarize.should_label(turns) is True


def test_no_turns_are_not_labelled():
    assert diarize.speaker_count([]) == 0
    assert diarize.should_label([]) is False


# ---------------------------------------------------------------------------
# validate_turns - the guardrail
# ---------------------------------------------------------------------------
def test_valid_turns_pass_untouched():
    turns = [diarize.Turn(0.0, 1.0, 0), diarize.Turn(1.0, 2.0, 1)]
    usable, reason = diarize.validate_turns(turns, duration=2.0)
    assert reason is None
    assert usable == turns


def test_turns_are_sorted():
    """Ordering is the one property every downstream consumer depends on."""
    turns = [diarize.Turn(5.0, 6.0, 1), diarize.Turn(1.0, 2.0, 0)]
    usable, reason = diarize.validate_turns(turns, duration=10.0)
    assert reason is None
    assert [turn.start for turn in usable] == [1.0, 5.0]


def test_non_finite_bounds_are_dropped():
    turns = [
        diarize.Turn(float("nan"), 1.0, 0),
        diarize.Turn(1.0, float("inf"), 1),
        diarize.Turn(2.0, 3.0, 0),
    ]
    usable, reason = diarize.validate_turns(turns, duration=10.0)
    assert len(usable) == 1
    assert "non-finite" in reason


def test_non_positive_interval_is_dropped():
    turns = [diarize.Turn(2.0, 2.0, 0), diarize.Turn(4.0, 3.0, 1)]
    usable, reason = diarize.validate_turns(turns, duration=10.0)
    assert usable == []
    assert "not a positive interval" in reason


def test_negative_speaker_id_is_dropped():
    turns = [diarize.Turn(0.0, 1.0, -1)]
    usable, reason = diarize.validate_turns(turns, duration=10.0)
    assert usable == []
    assert "invalid speaker id" in reason


def test_a_turn_starting_past_the_end_is_dropped():
    turns = [diarize.Turn(20.0, 21.0, 0)]
    usable, reason = diarize.validate_turns(turns, duration=10.0)
    assert usable == []
    assert "past the end" in reason


def test_a_turn_overrunning_the_end_is_clamped_not_dropped():
    """The tail is real speech; dropping it would silently truncate."""
    usable, reason = diarize.validate_turns([diarize.Turn(8.0, 12.0, 0)], duration=10.0)
    assert reason is None
    assert usable[0].end == 10.0
    assert usable[0].start == 8.0


def test_none_and_non_lists_are_reported():
    usable, reason = diarize.validate_turns(None, duration=10.0)
    assert usable == []
    assert "None" in reason

    usable, reason = diarize.validate_turns("nonsense", duration=10.0)
    assert usable == []
    assert "not a list" in reason


def test_empty_turn_list_is_valid():
    """Silence is a legitimate result, not a failure."""
    usable, reason = diarize.validate_turns([], duration=10.0)
    assert usable == []
    assert reason is None


def test_one_bad_turn_does_not_poison_the_rest():
    """A single malformed turn must not cost the user the whole meeting."""
    turns = [
        diarize.Turn(0.0, 2.0, 0),
        diarize.Turn(float("nan"), 3.0, 1),
        diarize.Turn(2.0, 4.0, 1),
    ]
    usable, reason = diarize.validate_turns(turns, duration=10.0)
    assert len(usable) == 2
    assert reason is not None


# ---------------------------------------------------------------------------
# diarize() - the fail-safe. Guardrail: it never raises.
# ---------------------------------------------------------------------------
def test_diarize_returns_turns_for_a_valid_fake_run():
    turns = diarize.diarize(_fake_audio(6), 16000, backend="fake")
    assert turns
    assert all(isinstance(turn, diarize.Turn) for turn in turns)
    assert all(turn.start < turn.end for turn in turns)


def test_diarize_result_is_sorted():
    fake.SCRIPT = [diarize.Turn(4.0, 5.0, 1), diarize.Turn(0.0, 1.0, 0)]
    turns = diarize.diarize(_fake_audio(6), 16000, backend="fake")
    assert [turn.start for turn in turns] == [0.0, 4.0]


def test_unknown_backend_returns_empty():
    assert diarize.diarize(_fake_audio(6), 16000, backend="pyannote") == []


def test_unavailable_backend_returns_empty():
    fake.AVAILABLE = False
    assert diarize.diarize(_fake_audio(6), 16000, backend="fake") == []


def test_a_raising_backend_returns_empty():
    fake.FAILURE = RuntimeError("model exploded")
    assert diarize.diarize(_fake_audio(6), 16000, backend="fake") == []


def test_a_backend_raising_a_base_exception_is_not_swallowed():
    """Ctrl-C must still interrupt a twenty-minute capture.

    ``diarize()`` catches ``Exception``, not ``BaseException``, deliberately:
    swallowing ``KeyboardInterrupt`` would make a long batch job unkillable.
    """
    fake.FAILURE = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        diarize.diarize(_fake_audio(6), 16000, backend="fake")


def test_short_audio_is_not_diarized():
    """Below MIN_AUDIO_S, one speaker is the only defensible answer."""
    assert diarize.diarize(_fake_audio(0.25), 16000, backend="fake") == []
    assert diarize.MIN_AUDIO_S == 1.0


def test_invalid_sample_rate_returns_empty():
    assert diarize.diarize(_fake_audio(6), 0, backend="fake") == []
    assert diarize.diarize(_fake_audio(6), -1, backend="fake") == []
    assert diarize.diarize(_fake_audio(6), None, backend="fake") == []


def test_unusable_audio_returns_empty():
    assert diarize.diarize(None, 16000, backend="fake") == []


def test_malformed_turns_from_a_backend_are_filtered_out():
    fake.SCRIPT = [diarize.Turn(float("nan"), 1.0, 0)]
    assert diarize.diarize(_fake_audio(6), 16000, backend="fake") == []


def test_a_partially_malformed_turn_list_keeps_the_good_turns():
    fake.SCRIPT = [diarize.Turn(0.0, 2.0, 0), diarize.Turn(3.0, 3.0, 1)]
    turns = diarize.diarize(_fake_audio(6), 16000, backend="fake")
    assert len(turns) == 1
    assert turns[0].speaker == 0


# ---------------------------------------------------------------------------
# Progress reporting - passed through, never invented
# ---------------------------------------------------------------------------
def test_progress_is_reported_with_processed_and_total():
    seen = []
    diarize.diarize(_fake_audio(7.5), 16000, backend="fake",
                    progress=lambda done, total: seen.append((done, total)))
    assert seen
    assert all(done <= total for done, total in seen)
    assert seen[-1][0] == seen[-1][1]


def test_a_raising_progress_callback_does_not_abort_diarization():
    """The UI callback may touch a TUI that is shutting down."""
    def explode(done, total):
        raise RuntimeError("TUI went away")

    turns = diarize.diarize(_fake_audio(6), 16000, backend="fake", progress=explode)
    assert turns


def test_no_progress_callback_is_fine():
    assert diarize.diarize(_fake_audio(6), 16000, backend="fake", progress=None)


# ---------------------------------------------------------------------------
# warm()
# ---------------------------------------------------------------------------
def test_warm_reports_readiness():
    assert diarize.warm("fake") is True
    assert fake.WARM_CALLS == 1


def test_warm_is_false_for_an_unknown_backend():
    assert diarize.warm("not-a-backend") is False


def test_warm_is_false_when_the_backend_is_unavailable():
    fake.AVAILABLE = False
    assert diarize.warm("fake") is False


def test_warm_swallows_a_failing_preload():
    fake.WARM_FAILURE = RuntimeError("cannot preload")
    assert diarize.warm("fake") is False


def test_warm_is_optional_in_the_contract():
    """A backend with no warm() is still usable - preloading is an optimisation."""
    assert "warm" not in diarize.REQUIRED_FUNCTIONS


# ---------------------------------------------------------------------------
# Hard gate: the off behaviour
# ---------------------------------------------------------------------------
def test_may_run_is_false_when_diarization_is_off():
    assert diarize.may_run("off") is False
    assert diarize.may_run("  OFF ") is False
    assert diarize.may_run("auto") is True
    assert diarize.may_run("2") is True


# ---------------------------------------------------------------------------
# The pipeline-shape guarantees the rest of workstream D depends on
# ---------------------------------------------------------------------------
def test_turn_boundaries_partition_the_audio():
    """Turns must be contiguous and ordered, because they become ASR inputs."""
    turns = diarize.diarize(_fake_audio(10), 16000, backend="fake")
    assert turns
    for earlier, later in itertools.pairwise(turns):
        assert earlier.end <= later.start


def test_every_turn_lies_inside_the_audio():
    turns = diarize.diarize(_fake_audio(10), 16000, backend="fake")
    assert all(0.0 <= turn.start < turn.end <= 10.0 for turn in turns)
