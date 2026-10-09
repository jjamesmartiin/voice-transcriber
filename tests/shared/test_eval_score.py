#!/usr/bin/env python3
"""Regression tests for the eval scoring harness.

The scorer must be a **pure observer**: it configures the app once at startup
(the recorded configuration), but scoring a clip must never mutate product
state that affects the next clip's transcription.

A previous bug had ``score.normalize()`` call
``post_processor.set_number_digits_enabled(True)`` and never restore it, so
every clip after the first was transcribed in digits mode even when the run
began with ``number_digits=false``. The committed baseline therefore
misdescribed 153 of 154 clips. These tests pin the fix.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCORE_PATH = REPO_ROOT / "eval" / "score.py"


def _load_score():
    spec = importlib.util.spec_from_file_location("eval_score", SCORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def score():
    return _load_score()


@pytest.fixture
def restore_number_mode():
    """Save/restore the product number mode (and the env override) around a test."""
    import post_processor

    saved_mode = post_processor.get_number_digits_mode()
    saved_env = os.environ.get("VT_NUMBER_DIGITS")
    try:
        yield
    finally:
        post_processor.set_number_digits_mode(saved_mode)
        if saved_env is None:
            os.environ.pop("VT_NUMBER_DIGITS", None)
        else:
            os.environ["VT_NUMBER_DIGITS"] = saved_env


def test_normalize_still_compares_words_and_digits(score, restore_number_mode):
    """Scoring must still treat "two" and "2" as equal (local digits conversion)."""
    import post_processor

    post_processor.set_number_digits_mode("words")
    assert score.normalize("I have two dogs") == "i have 2 dogs"
    edits, ref_words = score.word_edits("I have two dogs", "I have 2 dogs")
    assert (edits, ref_words) == (0, 4)


def test_scoring_does_not_change_number_mode(score, restore_number_mode):
    """A scoring call must leave the product number mode exactly as it found it."""
    import post_processor

    post_processor.set_number_digits_mode("words")
    before = post_processor.get_number_digits_mode()
    assert before == "words"

    # Exercise every scoring entry point that normalises text.
    score.normalize("I have two dogs")
    score.normalize_clean("um I have two dogs")
    score.word_edits("I have two dogs", "I have two dogs")
    score.char_edits("I have two dogs", "I have two dogs")
    score.word_edits_clean("um I have two dogs", "I have two dogs")

    assert post_processor.get_number_digits_mode() == before


def test_scoring_preserves_auto_mode(score, restore_number_mode):
    """The leak also clobbered a real 'auto' run; it must be left untouched too."""
    import post_processor

    post_processor.set_number_digits_mode("auto")
    score.normalize("room one oh one")
    assert post_processor.get_number_digits_mode() == "auto"
