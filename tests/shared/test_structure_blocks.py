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


# ---------------------------------------------------------------------------
# Microphone pauses (segment sentinels)
# ---------------------------------------------------------------------------
# A pause is a stronger boundary cue than any word, so the micro-batcher marks
# clean silence cuts (and forced energy-trough cuts) when it stitches chunks.
# The marker is an in-band control character, which is why no mode may ever let
# one reach the output.

HARD = pp.SEGMENT_SENTINEL
SOFT = pp.SOFT_SEGMENT_SENTINEL


@pytest.mark.parametrize("mode", ["off", "inline", "blocks"])
def test_a_segment_sentinel_never_reaches_the_output(mode):
    for text in [f"Thing one.{HARD}Thing two.", f"We shipped it.{SOFT}Then we rested."]:
        out = render(text, mode)
        assert HARD not in out and SOFT not in out, out


def test_pauses_are_flattened_when_structure_is_off():
    """Off stays byte-identical to the pre-boundary behaviour: a pause is a space."""
    assert render(f"Thing one.{HARD}Thing two.", "off") == "Thing one. Thing two."
    assert render(f"We shipped it.{SOFT}Then we rested.", "off") == "We shipped it. Then we rested."


def test_a_hard_pause_is_a_paragraph_and_a_soft_cut_is_a_break():
    assert render(f"We shipped it.{HARD}Then we rested.", "blocks") == (
        "We shipped it.\n\nThen we rested."
    )
    assert render(f"We shipped it.{SOFT}Then we rested.", "blocks") == (
        "We shipped it.\nThen we rested."
    )


def test_pause_separated_items_become_a_list():
    """The user's case without an introducer: the pauses alone mark the items."""
    assert render(f"Thing one.{HARD}Thing two.{HARD}Thing three.", "blocks") == (
        "- Thing one\n- Thing two\n- Thing three"
    )


def test_a_live_intermediate_chunk_never_carries_a_sentinel():
    """The TUI shows intermediate text, so it must never see a control character."""
    out = pp.clean_speech_transcription(
        f"partial{HARD}text", skip_slm=True, is_intermediate=True, structure_mode="blocks"
    )
    assert HARD not in out and SOFT not in out
    assert out == "partial text"


# ---------------------------------------------------------------------------
# A pause is a boundary only at a clause boundary (A3)
# ---------------------------------------------------------------------------
# The gap's *kind* still decides paragraph versus line break, but a pause that
# lands mid-clause joins with a space. Before this guard, a 250 ms breath in the
# middle of a sentence produced a blank line there:
#     "I was going to the [pause] store"  ->  "I was going to the\n\nstore"
# See _pause_is_a_boundary() for why the answer is linguistic rather than a
# larger millisecond threshold.

@pytest.mark.parametrize("text", [
    # Mid-phrase: the pause follows a function word.
    f"I was going to the{HARD}store and then home.",
    f"so the plan is{HARD}we ship on Friday.",
    f"give it to{HARD}me please.",
    f"it costs about{HARD}twenty pounds.",
    # Mid-clause: the pause follows punctuation that opens a continuation.
    f"First we plan,{HARD}then we ship.",
    f"milk,{HARD}eggs,{HARD}bread.",
    # Same for the weaker, forced cut.
    f"I was going to the{SOFT}store and then home.",
    f"the plan is{SOFT}we ship on Friday.",
])
def test_a_pause_mid_clause_joins_with_a_space(text):
    out = render(text, "blocks")
    assert "\n" not in out, out
    assert HARD not in out and SOFT not in out, out


def test_a_pause_after_a_content_word_is_still_a_boundary():
    """The guard must not undo the pause-separated list.

    Items end in content words rather than sentences, so a rule keyed only on
    terminal punctuation would have flattened these. Both shapes are pinned,
    because both occur: real dictation rarely supplies the full stops.
    """
    assert render(f"milk{HARD}eggs{HARD}bread", "blocks") == "milk\n\neggs\n\nbread."
    assert render(f"Thing one.{HARD}Thing two.{HARD}Thing three.", "blocks") == (
        "- Thing one\n- Thing two\n- Thing three"
    )
    assert render(f"first{HARD}second{HARD}third", "blocks") == (
        "- first\n- second\n- third"
    )


def test_the_clause_guard_never_touches_off_or_inline():
    """Whatever the guard decides, only `blocks` may change - `off` is a promise.

    `off` and `inline` flatten every pause to a space, exactly as they did
    before the guard existed, so no newline and no control character can appear
    there regardless of what the pause follows. (Casing and the punctuation
    preset are not cleanup concerns and still apply, hence the full stops.)
    """
    for mode in ("off", "inline"):
        for text in (f"I was going to the{HARD}store", f"milk{HARD}eggs",
                     f"We shipped it.{SOFT}Then we rested."):
            out = render(text, mode)
            assert "\n" not in out, out
            assert HARD not in out and SOFT not in out, out

    assert render(f"I was going to the{HARD}store", "off") == "I was going to the store."
    assert render(f"milk{HARD}eggs", "inline") == "milk eggs"


def test_the_clause_vocabulary_stays_a_grammar_list():
    """Guard against the list becoming an ad-hoc dumping ground.

    It is English function words only. Words that are *not* function words are
    exactly the ones that may end a clause, so letting content words in would
    silently disable the boundary in the cases that matter.
    """
    for word in ("milk", "eggs", "bread", "friday", "store", "plan"):
        assert word not in pp._CLAUSE_CONTINUATION_WORDS
    for word in ("the", "a", "and", "is", "to", "of"):
        assert word in pp._CLAUSE_CONTINUATION_WORDS


def test_pause_is_a_boundary_directly():
    assert pp._pause_is_a_boundary("We shipped it.") is True
    assert pp._pause_is_a_boundary("milk") is True
    assert pp._pause_is_a_boundary("going to the") is False
    assert pp._pause_is_a_boundary("First we plan,") is False
    assert pp._pause_is_a_boundary("") is False
    assert pp._pause_is_a_boundary("   ") is False


# ---------------------------------------------------------------------------
# A list that already carries markers must not be bulleted twice
# ---------------------------------------------------------------------------
# The on-device formatter emits finished list markup when it was asked for lists,
# and this stage then renders it again. Before the fix the result was
# "- - Milk for the cake": found by the real-model e2e run, which is why the
# `list` fixture is listed as failing there.

@pytest.mark.parametrize("text,expected", [
    ("- milk\n- eggs", "- milk\n- eggs"),
    ("1. milk\n2. eggs", "1. milk\n2. eggs"),
])
def test_an_already_marked_list_is_not_bulleted_twice(text, expected):
    out = pp.process_structure_blocks(text, mode="blocks")
    assert out == expected
    assert "- -" not in out


def test_stripping_the_marker_spares_a_real_hyphenated_value():
    """The marker only counts when whitespace follows it, so -5 is not a bullet."""
    assert pp.process_structure_blocks("-5 degrees", mode="blocks") == "-5 degrees"
