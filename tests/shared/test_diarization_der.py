#!/usr/bin/env python3
"""
Model-free unit tests for the self-contained DER in ``eval/score_diarization.py``
(milestone D7).

The metric is pinned with hand-computable REF/SYS turn lists -- no weights, no
audio, and no ``sherpa_onnx`` import at module import time (the same property
the ASR and diarization backends pin with a subprocess test). ``score_diarization``
is a script rather than an installable module, so its directory is added to
``sys.path`` here.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
EVAL_DIR = REPO_ROOT / "eval"
for path in (SRC_DIR, EVAL_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import score_diarization as sd  # noqa: E402


# ---------------------------------------------------------------------------
# The import-free rule
# ---------------------------------------------------------------------------
def test_importing_the_scorer_does_not_import_sherpa_onnx_or_the_backend():
    code = (
        "import sys; "
        "sys.path.insert(0, %r); sys.path.insert(0, %r); "
        "import score_diarization; "
        "assert 'sherpa_onnx' not in sys.modules, 'sherpa_onnx leaked at import'; "
        "assert 'voice_transcriber' not in sys.modules, 'backend leak at import'; "
        "print('OK')" % (str(EVAL_DIR), str(SRC_DIR))
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# RTTM loading
# ---------------------------------------------------------------------------
def test_load_rttm_reads_speaker_lines_and_ignores_comments(tmp_path):
    rttm = tmp_path / "ref.rttm"
    rttm.write_text(
        "; a comment\n"
        "SPEAKER rec 1 0.000 1.500 <NA> <NA> A <NA> <NA>\n"
        "SPEAKER rec 1 2.000 1.000 <NA> <NA> B <NA> <NA>\n"
        "SPEAKER rec 1 3.000 0.000 <NA> <NA> C <NA> <NA>\n"  # zero duration: dropped
        "LEXEME rec 1 0.000 1.000 hello <NA> <NA> <NA>\n"     # not SPEAKER: ignored
    )
    assert sd.load_rttm(str(rttm)) == [(0.0, 1.5, "A"), (2.0, 3.0, "B")]


# ---------------------------------------------------------------------------
# Optimal assignment
# ---------------------------------------------------------------------------
def test_max_weight_assignment_picks_the_best_square_pairing():
    # 0->0 (3) + 1->1 (4) beats 0->1 (1) + 1->0 (1).
    assert sd._max_weight_assignment([[3.0, 1.0], [1.0, 4.0]]) == {0: 0, 1: 1}


def test_max_weight_assignment_handles_rectangular_matrices():
    assert sd._max_weight_assignment([[10.0, 1.0, 1.0]]) == {0: 0}
    assert sd._max_weight_assignment([[1.0], [10.0]]) == {1: 0}


def test_max_weight_assignment_handles_empty():
    assert sd._max_weight_assignment([]) == {}
    assert sd._max_weight_assignment([[]]) == {}


def test_optimal_speaker_mapping_prefers_the_larger_overlap():
    overlap = {"A": {"X": 5.0, "Y": 1.0}, "B": {"X": 1.0, "Y": 4.0}}
    assert sd.optimal_speaker_mapping(overlap) == {"A": "X", "B": "Y"}
    assert sd.optimal_speaker_mapping({}) == {}


# ---------------------------------------------------------------------------
# DER: the four error terms
# ---------------------------------------------------------------------------
def test_perfect_match_is_zero_error():
    ref = [(0.0, 1.0, "A"), (2.0, 3.0, "B")]
    sys = [(0.0, 1.0, "X"), (2.0, 3.0, "Y")]
    metrics = sd.compute_der(ref, sys, collar=0.0, duration=3.0)
    assert metrics["der"] == pytest.approx(0.0)
    assert metrics["missed"] == pytest.approx(0.0)
    assert metrics["false_alarm"] == pytest.approx(0.0)
    assert metrics["speaker_confusion"] == pytest.approx(0.0)
    assert metrics["ref_speech_s"] == pytest.approx(2.0)
    assert metrics["mapping"] == {"A": "X", "B": "Y"}


def test_missed_speech_is_its_share_of_reference_speech():
    ref = [(0.0, 1.0, "A"), (2.0, 3.0, "B")]
    sys = [(0.0, 1.0, "X")]
    metrics = sd.compute_der(ref, sys, collar=0.0, duration=3.0)
    assert metrics["missed"] == pytest.approx(0.5)          # 1.0 s of 2.0 s
    assert metrics["false_alarm"] == pytest.approx(0.0)
    assert metrics["speaker_confusion"] == pytest.approx(0.0)
    assert metrics["der"] == pytest.approx(0.5)


def test_false_alarm_speech_is_its_share_of_reference_speech():
    ref = [(0.0, 1.0, "A")]
    sys = [(0.0, 1.0, "X"), (2.0, 3.0, "Y")]
    metrics = sd.compute_der(ref, sys, collar=0.0, duration=3.0)
    assert metrics["false_alarm"] == pytest.approx(1.0)     # 1.0 s of 1.0 s
    assert metrics["missed"] == pytest.approx(0.0)
    assert metrics["der"] == pytest.approx(1.0)


def test_speaker_confusion_when_one_system_speaker_covers_two_voices():
    ref = [(0.0, 2.0, "A"), (2.0, 4.0, "B")]
    sys = [(0.0, 4.0, "X")]
    metrics = sd.compute_der(ref, sys, collar=0.0, duration=4.0)
    # One of the two 2 s reference speakers must be confused with the other.
    assert metrics["speaker_confusion"] == pytest.approx(0.5)
    assert metrics["missed"] == pytest.approx(0.0)
    assert metrics["false_alarm"] == pytest.approx(0.0)
    assert metrics["der"] == pytest.approx(0.5)


def test_der_is_the_sum_of_its_three_components():
    ref = [(0.0, 2.0, "A"), (2.0, 4.0, "B")]
    sys = [(0.0, 1.0, "X"), (3.0, 4.0, "X")]
    metrics = sd.compute_der(ref, sys, collar=0.0, duration=4.0)
    assert metrics["der"] == pytest.approx(
        metrics["missed"] + metrics["false_alarm"] + metrics["speaker_confusion"]
    )


# ---------------------------------------------------------------------------
# Collar
# ---------------------------------------------------------------------------
def test_collar_ignores_error_within_the_no_score_zone():
    # The system boundary is 0.1 s inside the collar, so it must not be scored.
    ref = [(0.0, 2.0, "A")]
    sys = [(0.0, 1.9, "X")]
    within = sd.compute_der(ref, sys, collar=0.25, duration=2.0)
    assert within["der"] == pytest.approx(0.0)
    # With no collar the same 0.1 s tail is a real miss.
    bare = sd.compute_der(ref, sys, collar=0.0, duration=2.0)
    assert bare["missed"] == pytest.approx(0.05)   # 0.1 s of 2.0 s


def test_collar_shrinks_the_reference_denominator():
    ref = [(0.0, 2.0, "A")]
    sys = [(0.0, 2.0, "X")]
    bare = sd.compute_der(ref, sys, collar=0.0, duration=2.0)
    collared = sd.compute_der(ref, sys, collar=0.25, duration=2.0)
    assert bare["ref_speech_s"] == pytest.approx(2.0)
    assert collared["ref_speech_s"] == pytest.approx(1.5)   # 0.25 s off each edge


# ---------------------------------------------------------------------------
# Ordering (the acceptance's A,B,A,B check)
# ---------------------------------------------------------------------------
def test_relabel_sequence_maps_system_speakers_onto_reference_order():
    ref = [(0.0, 1.0, "A"), (1.0, 2.0, "B"), (2.0, 3.0, "A"), (3.0, 4.0, "B")]
    sys = [(0.0, 1.0, "7"), (1.0, 2.0, "3"), (2.0, 3.0, "7"), (3.0, 4.0, "3")]
    ref_order, sys_order = sd.relabel_sequence(ref, sys, {"A": "7", "B": "3"})
    assert ref_order == ["A", "B", "A", "B"]
    assert sys_order == ["A", "B", "A", "B"]


def test_relabel_sequence_reports_none_for_unmapped_speakers():
    ref = [(0.0, 1.0, "A")]
    sys = [(0.0, 1.0, "9"), (2.0, 3.0, "8")]
    _ref_order, sys_order = sd.relabel_sequence(ref, sys, {"A": "9"})
    assert sys_order == ["A", None]
