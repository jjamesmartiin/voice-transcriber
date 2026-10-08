#!/usr/bin/env python3
"""The three cleanup modes: how much of the pass may change the words.

Spec: ``docs/cleanup_modes.md``.

  ``off``        keeps every word — nothing is deleted.
  ``artifacts``  deletes noise: hallucinations on silence, filler, stutters, a
                 clipped onset consonant, trailing mutterings.
  ``full``       also resolves speaker self-corrections (the shipped default).

Number/date formatting, the custom dictionary, casing, the punctuation preset and
structured output are *not* gated here: they are separate settings, and they
change how text looks rather than which words survive.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

# Keep these tests offline and deterministic.
os.environ.setdefault("VT_ENABLE_SLM", "0")

import post_processor as pp  # noqa: E402


@pytest.fixture(autouse=True)
def _number_words():
    """Keep spoken numbers as words, so the expectations below are about cleaning."""
    previous = pp.get_number_digits_mode()
    pp.set_number_digits_mode("auto")
    yield
    pp.set_number_digits_mode(previous)


def render(text, mode):
    return pp.clean_speech_transcription(
        text, skip_slm=True, punctuation_mode="full", cleanup_mode=mode
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (None, "full"),
    ("", "full"),
    ("OFF", "off"),
    ("artifacts", "artifacts"),
    ("noise", "artifacts"),
    ("safe", "artifacts"),
    ("full", "full"),
    ("corrections", "full"),
    # Unknown means "full", not "off": this setting defaults to the shipped
    # behaviour, so a typo must not silently disable cleaning.
    ("nonsense", "full"),
])
def test_cleanup_mode_normalisation(value, expected):
    assert pp.normalize_cleanup_mode(value) == expected


def test_set_and_get_cleanup_mode_round_trip():
    previous = pp.get_cleanup_mode()
    try:
        assert pp.set_cleanup_mode("artifacts") == "artifacts"
        assert pp.get_cleanup_mode() == "artifacts"
        assert pp.set_cleanup_mode("bogus") == "full"
    finally:
        pp.set_cleanup_mode(previous)


def test_the_default_is_the_shipped_behaviour():
    """Whatever the default is, it must be "full" — that is what v1.3.1 did."""
    previous = pp.get_cleanup_mode()
    try:
        pp.set_cleanup_mode(previous)
        assert pp.get_cleanup_mode() == "full"
    finally:
        pp.set_cleanup_mode(previous)


# ---------------------------------------------------------------------------
# Noise: kept in "off", removed in "artifacts" and "full"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spoken,kept,cleaned", [
    # A silence hallucination, the case that motivated "artifacts": a 1s clip
    # really does come back as "Thank you." and must be droppable without
    # enabling self-correction rewriting.
    ("Thank you.", "Thank you.", ""),
    ("you", "you", ""),
    ("So uh I think this is good", "So uh I think this is good.", "So I think this is good."),
    ("I think we should cancel the deploy, never mind",
     "I think we should cancel the deploy, never mind.",
     "I think we should cancel the deploy."),
    ("t needs to be fixed today", "t needs to be fixed today.", "needs to be fixed today."),
])
def test_noise_is_kept_in_off_and_removed_above_it(spoken, kept, cleaned):
    assert render(spoken, "off") == kept
    assert render(spoken, "artifacts") == cleaned
    assert render(spoken, "full") == cleaned


# ---------------------------------------------------------------------------
# Self-corrections: verbatim until "full"
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spoken,verbatim,resolved", [
    ("Let's schedule it for Tuesday no sorry Wednesday",
     "Let's schedule it for Tuesday no sorry Wednesday.",
     "Let's schedule it for Wednesday."),
    ("Remind me Tuesday no wait make that Wednesday",
     "Remind me Tuesday no wait make that Wednesday.",
     "Remind me Wednesday."),
    ("Add the class definition, scratch that",
     "Add the class definition, scratch that.",
     "Add the class definition."),
])
def test_corrections_are_verbatim_until_full(spoken, verbatim, resolved):
    """A retraction is a rewrite of what was said, so only "full" may resolve it."""
    assert render(spoken, "off") == verbatim
    assert render(spoken, "artifacts") == verbatim
    assert render(spoken, "full") == resolved


def test_the_apology_case_that_started_this():
    """Real dictation: "Tuesday no sorry Wednesday" must not leave "sorry" behind."""
    spoken = ("Okay this should be a good test here let's schedule it for "
              "Tuesday no sorry Wednesday")
    assert render(spoken, "artifacts") == (
        "Okay this should be a good test here let's schedule it for Tuesday no sorry Wednesday."
    )
    assert render(spoken, "full") == (
        "Okay this should be a good test here let's schedule it for Wednesday."
    )


# ---------------------------------------------------------------------------
# What the modes deliberately do *not* govern
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["off", "artifacts", "full"])
def test_formatting_settings_are_not_gated_by_cleanup_mode(mode):
    """Numbers, dates and the dictionary are their own settings, not "cleanup"."""
    assert render("there are twenty five servers on nixos", mode) == (
        "there are twenty five servers on NixOS."
    )
    assert render("the meeting is on october twentieth", mode) == (
        "the meeting is on October 20th."
    )


def test_structured_output_is_not_gated_by_cleanup_mode():
    assert pp.clean_speech_transcription(
        "I can list them like: Thing one. Thing two.",
        skip_slm=True, punctuation_mode="full", structure_mode="blocks", cleanup_mode="off",
    ) == "I can list them like:\n\n- Thing one\n- Thing two"


def test_the_punctuation_preset_is_not_gated_by_cleanup_mode():
    """A preset is formatting, so it still applies with cleaning switched off."""
    assert pp.clean_speech_transcription(
        "So uh this is fine", skip_slm=True, punctuation_mode="no_punctuation", cleanup_mode="off",
    ) == "So uh this is fine"
