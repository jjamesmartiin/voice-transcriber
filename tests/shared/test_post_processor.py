#!/usr/bin/env python3
"""
Focused unit tests for post_processor formatting helpers.

Covers spoken Unix paths / IP / CIDR conversion (the ``clean_spoken_paths``
feature) and punctuation-mode formatting.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

# Keep these tests offline and deterministic: never hit a local SLM endpoint.
os.environ.setdefault("VT_ENABLE_SLM", "0")

import post_processor as pp  # noqa: E402


# ---------------------------------------------------------------------------
# Spoken Unix paths / IP / CIDR
# ---------------------------------------------------------------------------

def test_spoken_path_does_not_swallow_following_word():
    # Regression: the old regex consumed the trailing space -> "/etc/nixosnow".
    assert pp.clean_spoken_paths("the config is in slash etc slash nixos now") == \
        "the config is in /etc/nixos now"


def test_spoken_path_at_end_of_text():
    assert pp.clean_spoken_paths("open slash etc slash nixos") == "open /etc/nixos"


def test_spoken_path_segments_are_lowercased():
    # The dictionary runs first and capitalises "nixos" -> "NixOS".
    assert pp.clean_spoken_paths("edit slash etc slash NixOS") == "edit /etc/nixos"


def test_spoken_ip_and_cidr():
    assert pp.clean_spoken_paths("net is 10 dot 0 dot 0 dot 0 slash 24 today") == \
        "net is 10.0.0.0/24 today"


def test_backslash_is_not_treated_as_a_path():
    assert pp.clean_spoken_paths("a backslash in text") == "a backslash in text"


def test_full_pipeline_dictionary_then_path():
    """Dictionary substitution runs first, then path conversion must survive it."""
    saved = pp.get_custom_dictionary()
    pp.set_custom_dictionary({"nixos": "NixOS", "configuration dot nix": "configuration.nix"})
    try:
        out = pp.clean_speech_transcription(
            "Update the configuration file at slash etc slash nixos slash configuration dot nix.",
            skip_slm=True,
        )
    finally:
        pp.set_custom_dictionary(saved if saved else None)
    assert out == "Update the configuration file at /etc/nixos/configuration.nix."


# ---------------------------------------------------------------------------
# Punctuation modes
# ---------------------------------------------------------------------------

def test_no_punctuation_preserves_decimals_ips_paths_and_contractions():
    # Regression: the old implementation turned these into 314 / 192168110 /
    # etcnixos / dont.
    out = pp.apply_punctuation_mode(
        "Version 3.14, don't use 192.168.1.10 or /etc/nixos;", mode="no_punctuation"
    )
    assert out == "Version 3.14 don't use 192.168.1.10 or /etc/nixos"


def test_lowercase_no_punctuation_lowercases_but_keeps_separators():
    out = pp.apply_punctuation_mode("Don't use 3.14 at /Etc/NixOS!", mode="lowercase_no_punctuation")
    assert out == "don't use 3.14 at /etc/nixos"


def test_no_terminal_period_keeps_internal_punctuation():
    assert pp.apply_punctuation_mode("Hello, world.", mode="no_terminal_period") == "Hello, world"
    assert pp.apply_punctuation_mode("Really?", mode="no_terminal_period") == "Really?"
    assert pp.apply_punctuation_mode("Hi.", mode="semi-formal") == "Hi"
    assert pp.apply_punctuation_mode("Hi.", mode="casual") == "Hi"


def test_autocorrect_mode_capitalizes_sentence_and_i_without_punctuation():
    out = pp.apply_punctuation_mode("hello, world! i think i'm ready, don't you?", mode="autocorrect")
    assert out == "Hello world I think I'm ready don't you"


def test_aesthetic_lowercase_mode_keeps_punctuation_lowercased():
    out = pp.apply_punctuation_mode("Hello, World! I think I'm ready, don't you?", mode="aesthetic_lowercase")
    assert out == "hello, world! i think i'm ready, don't you?"
    # Strips trailing period
    out_period = pp.apply_punctuation_mode("Hello, World. I am here.", mode="aesthetic_lowercase")
    assert out_period == "hello, world. i am here"


def test_pure_gen_z_mode_no_caps_no_punctuation():
    out = pp.apply_punctuation_mode("Hello, World! I think I'm ready, don't you?", mode="gen_z")
    assert out == "hello world i think i'm ready don't you"


# ---------------------------------------------------------------------------
# SLM pass defaults / back-off
# ---------------------------------------------------------------------------

def test_slm_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VT_ENABLE_SLM", raising=False)
    # With SLM off, the pass must be a no-op and must not touch the network.
    text = "this is a long enough sentence"
    assert pp.process_slm_llm_rewrite(text) == text


def test_slm_offline_backoff_escalates():
    pp._SLM_OFFLINE_STREAK = 1
    pp._SLM_LAST_OFFLINE_CHECK = time.time()
    first = pp._slm_cooldown_remaining()
    pp._SLM_OFFLINE_STREAK = 4
    pp._SLM_LAST_OFFLINE_CHECK = time.time()
    fourth = pp._slm_cooldown_remaining()
    pp._SLM_OFFLINE_STREAK = 0
    assert first > 0
    assert fourth > first


# ---------------------------------------------------------------------------
# Number word -> digit formatting modes (auto / digits / words)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture
def number_mode():
    """Isolate the process-wide number mode + VT_NUMBER_DIGITS env var per test."""
    saved_env = os.environ.get("VT_NUMBER_DIGITS")
    os.environ.pop("VT_NUMBER_DIGITS", None)
    saved_mode = pp.get_number_digits_mode()
    try:
        yield pp
    finally:
        if saved_env is None:
            os.environ.pop("VT_NUMBER_DIGITS", None)
        else:
            os.environ["VT_NUMBER_DIGITS"] = saved_env
        pp.set_number_digits_mode(saved_mode)


def test_number_auto_converts_consecutive_digits_only(number_mode):
    """Auto mode collapses phone/serial number runs of 2+ consecutive digit words."""
    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits("five five five one two one two") == "5551212"
    assert pp.convert_number_words_to_digits(
        "call six seven zero six seven zero six nine nine six now"
    ) == "call 6706706996 now"
    assert pp.convert_number_words_to_digits("double oh seven") == "007"
    assert pp.convert_number_words_to_digits("the code is one two three four") == "the code is 1234"
    assert pp.convert_number_words_to_digits("serial nine eight seven six five") == "serial 98765"


def test_number_auto_keeps_isolated_numbers_as_words(number_mode):
    """Auto mode must not touch isolated numbers in natural conversation."""
    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits(
        "bring one of them over here"
    ) == "bring one of them over here"
    assert pp.convert_number_words_to_digits("I have two dogs") == "I have two dogs"
    assert pp.convert_number_words_to_digits("give me three reasons") == "give me three reasons"
    assert pp.convert_number_words_to_digits(
        "there are twenty five servers"
    ) == "there are twenty five servers"
    assert pp.convert_number_words_to_digits("wait a second") == "wait a second"
    assert pp.convert_number_words_to_digits("the first item") == "the first item"


def test_number_digits_mode_converts_everything(number_mode):
    """Digits mode preserves the full legacy conversion behaviour."""
    pp.set_number_digits_mode("digits")
    assert pp.convert_number_words_to_digits("twenty five people") == "25 people"
    assert pp.convert_number_words_to_digits("one hundred and fifty") == "150"
    assert pp.convert_number_words_to_digits("two thousand twenty four") == "2024"
    assert pp.convert_number_words_to_digits("three point one four") == "3.14"
    assert pp.convert_number_words_to_digits("fifty percent") == "50%"
    assert pp.convert_number_words_to_digits("five pm") == "5 PM"
    assert pp.convert_number_words_to_digits("nineteen eighty five") == "1985"
    assert pp.convert_number_words_to_digits("the fifth item") == "the 5th item"
    assert pp.convert_number_words_to_digits("I want one") == "I want 1"
    # Ambiguous pronoun "one" is still protected in digits mode.
    assert pp.convert_number_words_to_digits("no one is here") == "no one is here"


def test_number_words_mode_keeps_all_words(number_mode):
    """Words mode is a hard off switch - nothing converts."""
    pp.set_number_digits_mode("words")
    assert pp.convert_number_words_to_digits(
        "five five five one two one two"
    ) == "five five five one two one two"
    assert pp.convert_number_words_to_digits("twenty five people") == "twenty five people"
    assert pp.convert_number_words_to_digits("double oh seven") == "double oh seven"
    assert pp.convert_number_words_to_digits("call one two three now") == "call one two three now"


def test_number_mode_transitions(number_mode):
    """Runtime mode transitions update the active mode and conversion behaviour."""
    pp.set_number_digits_mode("auto")
    assert pp.get_number_digits_mode() == "auto"
    assert pp.is_number_digits_enabled() is True

    pp.set_number_digits_mode("digits")
    assert pp.get_number_digits_mode() == "digits"
    assert pp.convert_number_words_to_digits("twenty five") == "25"

    pp.set_number_digits_mode("words")
    assert pp.get_number_digits_mode() == "words"
    assert pp.is_number_digits_enabled() is False
    assert pp.convert_number_words_to_digits("twenty five") == "twenty five"

    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits("twenty five") == "twenty five"
    assert pp.convert_number_words_to_digits("five five five") == "555"


def test_number_mode_backwards_compatible_values(number_mode):
    """Booleans and "1"/"0" still work and map onto the new modes."""
    assert pp.normalize_number_mode(True) == "digits"
    assert pp.normalize_number_mode(False) == "words"
    assert pp.normalize_number_mode("1") == "digits"
    assert pp.normalize_number_mode("0") == "words"
    assert pp.normalize_number_mode("auto") == "auto"
    assert pp.normalize_number_mode("digits") == "digits"
    assert pp.normalize_number_mode("words") == "words"
    assert pp.normalize_number_mode("nonsense") == "auto"

    # Legacy enable/disable shim: True -> digit conversion, False -> words.
    pp.set_number_digits_enabled(True)
    assert pp.get_number_digits_mode() == "digits"
    assert pp.convert_number_words_to_digits("twenty five") == "25"

    pp.set_number_digits_enabled(False)
    assert pp.get_number_digits_mode() == "words"
    assert pp.convert_number_words_to_digits("twenty five") == "twenty five"


def test_number_mode_env_var_override(number_mode):
    """VT_NUMBER_DIGITS overrides the runtime mode, accepting 1/0 and mode names."""
    pp.set_number_digits_mode("auto")

    os.environ["VT_NUMBER_DIGITS"] = "1"
    assert pp.convert_number_words_to_digits("twenty five") == "25"

    os.environ["VT_NUMBER_DIGITS"] = "0"
    assert pp.convert_number_words_to_digits("five five five") == "five five five"

    os.environ["VT_NUMBER_DIGITS"] = "digits"
    assert pp.convert_number_words_to_digits("twenty five") == "25"

    os.environ["VT_NUMBER_DIGITS"] = "words"
    assert pp.convert_number_words_to_digits("five five five") == "five five five"

    os.environ["VT_NUMBER_DIGITS"] = "auto"
    assert pp.convert_number_words_to_digits("five five five") == "555"
    assert pp.convert_number_words_to_digits("twenty five") == "twenty five"


def test_full_pipeline_auto_number_mode(number_mode):
    """End-to-end: auto mode keeps natural numbers as words but collapses phone numbers."""
    pp.set_number_digits_mode("auto")
    assert pp.clean_speech_transcription(
        "bring one of them over here", skip_slm=True
    ) == "bring one of them over here."
    assert pp.clean_speech_transcription(
        "I have two dogs", skip_slm=True
    ) == "I have two dogs."
    converted = pp.clean_speech_transcription(
        "call five five five one two one two", skip_slm=True
    )
    assert "5551212" in converted
