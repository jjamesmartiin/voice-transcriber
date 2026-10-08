#!/usr/bin/env python3
"""Structured output: spoken lists, bullets and paragraph breaks.

This file is the enforcement for ``docs/formatting.md``. The stage is
deliberately conservative: "off" (the default) must be byte-identical to the
behaviour before it existed, and anything ambiguous is left as prose.

Only "blocks" may emit a newline. A newline is an Enter keypress in every typing
sink, so "inline" is the only safe mode for auto-typed text.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

# Keep these tests offline and deterministic.
os.environ.setdefault("VT_ENABLE_SLM", "0")

import post_processor as pp  # noqa: E402

#: The utterance this feature was built for: a spoken list with an introducer.
MOTIVATING = (
    "Okay I can start pressing and holding to record here and it'll just work. "
    "I can say I'm going to do three things here and then it'll do three "
    "different things. I can list them like: Thing one. Thing two. Thing three."
)


@pytest.fixture(autouse=True)
def _number_words(autouse=True):
    """Keep spoken numbers as words, so the expectations below are about layout."""
    previous = pp.get_number_digits_mode()
    pp.set_number_digits_mode("auto")
    yield
    pp.set_number_digits_mode(previous)


def render(text, mode, punctuation_mode="full"):
    return pp.clean_speech_transcription(
        text, skip_slm=True, punctuation_mode=punctuation_mode, structure_mode=mode
    )


# ---------------------------------------------------------------------------
# "off" is the identity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["off", "none", "flat", "nonsense", ""])
def test_off_and_unknown_modes_change_nothing(mode):
    """An unknown value must never reformat: the safe default is no structure.

    Asserted against the stage itself rather than the whole pipeline, so the
    claim does not depend on whatever preset another test left configured.
    """
    for text in [MOTIVATING, "Deploy alpha. Deploy beta. Deploy gamma.",
                 "Things to do. Bullet point. Install it."]:
        assert pp.process_structure_blocks(text, mode) == text
        assert render(text, mode) == render(text, "off")


@pytest.mark.parametrize("value,expected", [
    (None, "off"),
    ("", "off"),
    ("OFF", "off"),
    ("inline", "inline"),
    ("single-line", "inline"),
    ("blocks", "blocks"),
    ("bullets", "blocks"),
    ("BULLETS", "blocks"),
    ("nonsense", "off"),
])
def test_structure_mode_normalisation(value, expected):
    assert pp.normalize_structure_mode(value) == expected


def test_set_and_get_structure_mode_round_trip():
    previous = pp.get_structure_mode()
    try:
        assert pp.set_structure_mode("bullets") == "blocks"
        assert pp.get_structure_mode() == "blocks"
        assert pp.set_structure_mode("bogus") == "off"
    finally:
        pp.set_structure_mode(previous)


# ---------------------------------------------------------------------------
# The motivating utterance
# ---------------------------------------------------------------------------

def test_spoken_list_becomes_bullets_in_blocks_mode():
    assert render(MOTIVATING, "blocks") == (
        "Okay I can start pressing and holding to record here and it'll just "
        "work. I can say I'm going to do three things here and then it'll do "
        "three different things. I can list them like:\n"
        "\n"
        "- Thing one\n"
        "- Thing two\n"
        "- Thing three"
    )


def test_spoken_list_stays_one_line_in_inline_mode():
    out = render(MOTIVATING, "inline")
    assert "\n" not in out
    assert out.endswith("I can list them like: - Thing one. - Thing two. - Thing three.")


@pytest.mark.parametrize("text", [
    MOTIVATING,
    "Deploy alpha. Deploy beta. Deploy gamma.",
    "Things to do. Bullet point. Install it. Bullet point. Configure it.",
    "Here is the plan. New paragraph. Then we ship.",
    "First, update the config. Second, restart the service.",
])
def test_inline_mode_never_emits_a_newline(text):
    """A newline in auto-typed text is an Enter keypress, so inline must not."""
    assert "\n" not in render(text, "inline")


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_parallel_short_sentences_without_an_introducer_become_a_list():
    assert render("Deploy alpha. Deploy beta. Deploy gamma.", "blocks") == (
        "- Deploy alpha\n- Deploy beta\n- Deploy gamma"
    )


def test_ordinal_labels_without_an_introducer_become_a_list():
    assert render("I need three things. Thing one. Thing two. Thing three.", "blocks") == (
        "I need three things.\n\n- Thing one\n- Thing two\n- Thing three"
    )


def test_explicit_numbering_labels_are_renumbered():
    assert render("Step one: install the package. Step two: configure it.", "blocks") == (
        "1. install the package\n2. configure it"
    )


def test_spoken_bullet_point_starts_a_bullet():
    assert render("Things to do. Bullet point. Install it. Bullet point. Configure it.",
                  "blocks") == "Things to do.\n- Install it.\n- Configure it."


def test_spoken_new_paragraph_starts_a_paragraph():
    assert render("Here is the plan. New paragraph. Then we ship.", "blocks") == (
        "Here is the plan.\n\nThen we ship."
    )


@pytest.mark.parametrize("text", [
    "Note: Remember this.",                          # a cue with one item is not a list
    "I like the new line of GPUs.",                  # "new line" inside a sentence
    "The build passed. The tests passed.",           # two sentences, no list cue
    "We shipped it. We tested it. We documented it.",  # parallel, but only a pronoun head
    "I have no idea what happened yesterday.",
    "Send the report to Alice and then go home.",
])
def test_prose_is_never_reformatted(text):
    """The stage is the identity on prose, whatever the punctuation preset is."""
    assert pp.process_structure_blocks(text, "blocks") == text


# ---------------------------------------------------------------------------
# Interaction with the punctuation presets
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("preset", ["full", "no_punctuation", "aesthetic_lowercase", "gen_z"])
def test_presets_do_not_flatten_a_list(preset):
    """The markers and breaks survive every preset — the presets strip
    punctuation, which would otherwise delete the bullets and the newlines."""
    out = render("I can list them like: Thing one. Thing two.", "blocks", punctuation_mode=preset)
    assert "\n- " in out, out
    assert "thing one" in out.lower() or "Thing one" in out


def test_pure_gen_z_keeps_structure_while_lowercasing():
    assert render("I can list them like: Thing one. Thing two.", "blocks",
                  punctuation_mode="gen_z") == (
        "i can list them like\n\n- thing one\n- thing two"
    )
