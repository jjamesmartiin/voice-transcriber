#!/usr/bin/env python3
"""
Model-free tests for the review report's aggregations (``eval/review_report.py``).

The report is the human-review half of the ASR baseline, so a wrong number in it
is read by a person rather than caught by a threshold. Its silence-hallucination
rate counted EMPTY hypotheses and reported that fraction -- inverting the metric
``eval/score.py`` computes on the same data (1.0 there against score.py's 0.0),
so a reader would conclude every silence clip hallucinated when none did.

No weights, no audio, no model.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = REPO_ROOT / "eval"
if str(EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(EVAL_DIR))

import review_report as rr  # noqa: E402


def _clip(slice_name: str, hyp: str) -> dict:
    return {
        "slice": slice_name,
        "hyp": hyp,
        "word_edits": 0,
        "ref_words": 1,
        "char_edits": 0,
        "ref_chars": 1,
        "exact": False,
        "empty": not hyp.strip(),
    }


def test_a_hallucination_is_the_non_empty_hypothesis():
    # 1 of 4 silence clips produced text, so the rate is 0.25 -- not 0.75.
    clips = [_clip("silence", "hello")] + [_clip("silence", "") for _ in range(3)]
    assert rr._slice_summary(clips)["silence"]["silence_halluc_rate"] == 0.25


def test_no_hallucination_reads_as_zero_not_one():
    clips = [_clip("silence", "") for _ in range(12)]
    assert rr._slice_summary(clips)["silence"]["silence_halluc_rate"] == 0.0


def test_every_silence_clip_hallucinating_reads_as_one():
    clips = [_clip("silence", "text") for _ in range(12)]
    assert rr._slice_summary(clips)["silence"]["silence_halluc_rate"] == 1.0


def test_speech_slices_get_no_silence_rate():
    summary = rr._slice_summary([_clip("clean", "some words")])
    assert summary["clean"]["silence_halluc_rate"] is None


def test_reported_slice_counts_match_the_recorded_aggregate():
    """Two aggregations of the same clips must agree.

    ``review_report`` re-derives per-slice counts from the clip list while
    ``score.py`` records its own in ``results["per_slice"]``. Both feed numbers a
    human reads, so a drift in either would be invisible -- this compares them on
    the real recorded baseline instead of trusting them to match.
    """
    import json

    results = json.loads((EVAL_DIR / "results.json").read_text())
    derived = rr._slice_summary(results["results"])
    stored = results["per_slice"]
    assert set(derived) == set(stored)
    for name, got in derived.items():
        for key in ("n", "we", "wr", "ce", "cr", "exact", "empty"):
            assert got[key] == stored[name][key], (
                f"{name}.{key}: report says {got[key]}, recorded says "
                f"{stored[name][key]}")
