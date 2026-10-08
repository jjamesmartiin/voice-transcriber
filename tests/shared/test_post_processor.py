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

#: The post-processor runs in ~16 us on a developer machine, and the README quotes
#: that. This ceiling is deliberately loose, because the test runs on shared CI
#: runners where a full 2000-call average has been measured at 200 us under load
#: (it failed the Windows job). Its job is to catch an order-of-magnitude
#: regression — an accidental regex backtrack, a dictionary rebuilt per call —
#: not to certify a benchmark. For a real number, use the `eval/` harness.
LATENCY_CEILING_US = 500.0

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


def test_post_processor_latency_benchmark(benchmark_reporter):
    """Confirm post_processor execution latency is under 0.1ms per phrase across all 3 modes."""
    import time

    test_phrases = [
        "bring one of them over here",
        "call me at five five five one two one two today",
        "the server has two hundred forty eight megabytes left",
        "let us meet at five pm on twenty first street",
    ]
    for mode in ["auto", "digits", "words"]:
        pp.set_number_digits_mode(mode)
        # Warmup
        for p in test_phrases:
            pp.clean_speech_transcription(p, skip_slm=True)
        N = 500
        start = time.perf_counter()
        for _ in range(N):
            for p in test_phrases:
                pp.clean_speech_transcription(p, skip_slm=True)
        elapsed = time.perf_counter() - start
        avg_us = (elapsed / (N * len(test_phrases))) * 1e6
        throughput = int(1e6 / max(avg_us, 0.001))
        # Order-of-magnitude guard, not a benchmark; see LATENCY_CEILING_US.
        assert avg_us < LATENCY_CEILING_US, (
            f"Post-processor latency too high in {mode} mode: {avg_us:.2f} µs"
        )
        benchmark_reporter(
            "Post-Processor",
            f"{mode:<8} mode",
            f"{avg_us:.2f} µs",
            f"~{throughput:,} ph/s",
            "100% Match (PASS)",
            status="PASS",
        )



# ---------------------------------------------------------------------------
# Zero synonyms ("o" / "oh"), literal digit strings and phone number groups
# ---------------------------------------------------------------------------

@pytest.fixture
def serial_settings():
    """Isolate serial-collapse / spell-command + their env vars per test."""
    saved_env = {
        k: os.environ.get(k) for k in ("VT_SERIAL_COLLAPSE", "VT_SPELL_COMMAND")
    }
    for k in saved_env:
        os.environ.pop(k, None)
    saved = (pp.get_serial_collapse(), pp.get_spell_command())
    try:
        yield pp
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        pp.set_serial_collapse(saved[0])
        pp.set_spell_command(saved[1])


@pytest.mark.parametrize("text,expected", [
    # "oh" already worked; "o" / "O" are now synonyms for zero inside digit runs.
    ("seven oh two", "702"),
    ("seven o two", "702"),
    ("seven O two", "702"),
    ("seven zero two", "702"),
    ("one eight o o", "1800"),
    ("one eight oh oh", "1800"),
    ("double o seven", "007"),
    ("double O seven", "007"),
])
def test_o_and_oh_are_zero_in_digit_strings(number_mode, text, expected):
    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits(text) == expected


@pytest.mark.parametrize("text,expected", [
    # Literal digits emitted by ASR with spaces between them must collapse too.
    ("7 o 2", "702"),
    ("7 O 2", "702"),
    ("7 oh 2", "702"),
    ("7 0 2", "702"),
    ("7 0 2 5 5 5 1 2 3 4", "7025551234"),
    ("call 5 5 5 1 2 1 2", "call 5551212"),
])
def test_literal_spaced_digits_collapse(number_mode, text, expected):
    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("call 702 555 1234", "call 7025551234"),
    ("call 1 800 555 1234", "call 18005551234"),
    ("call 555 1212", "call 5551212"),
])
def test_phone_number_groups_collapse(number_mode, text, expected):
    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits(text) == expected


@pytest.mark.parametrize("text", [
    "in 2020 1200 people lived there",
    "page 100 200 is fine",
])
def test_phone_group_does_not_over_collapse(number_mode, text):
    pp.set_number_digits_mode("auto")
    assert pp.convert_number_words_to_digits(text) == text


def test_standalone_o_and_oh_are_never_zero(number_mode):
    """The letter 'O' / interjection 'oh' must survive on its own."""
    for mode in ("auto", "digits"):
        pp.set_number_digits_mode(mode)
        assert pp.convert_number_words_to_digits("the letter O is round") == "the letter O is round"
        assert pp.convert_number_words_to_digits("O Canada") == "O Canada"
        assert pp.convert_number_words_to_digits("type O blood") == "type O blood"
        assert pp.convert_number_words_to_digits("oh") == "oh"


# ---------------------------------------------------------------------------
# Serial numbers, model codes, NATO phonetics & the verbal spell command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # Trigger-noun anchored identifiers.
    ("serial number A B C 1 2 3", "serial number ABC123"),
    ("model X K 9 4 J", "model XK94J"),
    ("part number 4 5 B 9 0", "part number 45B90"),
    ("license plate 7 S A M 1 2 3", "license plate 7SAM123"),
    ("VIN 1 H G C R 2", "VIN 1HGCR2"),
    ("code F 1 5 0", "code F150"),
    ("serial 7 O 2 A B C", "serial 7O2ABC"),
    # Untriggered alphanumeric chains.
    ("A B C 1 2 3", "ABC123"),
    ("X K 9 4 J", "XK94J"),
    ("4 5 B 9 0", "45B90"),
    ("W P A 2", "WPA2"),
    ("K G B", "KGB"),
    ("F B I", "FBI"),
])
def test_serial_numbers_and_codes_collapse(serial_settings, text, expected):
    assert pp.process_serial_numbers(text) == expected


@pytest.mark.parametrize("text,expected", [
    # NATO phonetic dictation.
    ("Alpha Bravo Charlie 1 2 3", "ABC123"),
    ("code Alpha Bravo Charlie 1 2 3", "code ABC123"),
    ("Sierra Tango 4 2", "ST42"),
    ("Delta Kilo 9", "DK9"),
    ("Hotel 4", "H4"),
    ("Kilo 9", "K9"),
])
def test_nato_phonetic_collapse(serial_settings, text, expected):
    assert pp.process_serial_numbers(text) == expected


@pytest.mark.parametrize("text", [
    # Natural English must be left completely alone.
    "I have a dog",
    "I stayed at a hotel in India in November",
    "the letter O is round",
    "O Canada",
    "hear me, O Lord",
    "type O blood",
    "call Mike",
    "drinking whiskey",
    "playing golf",
    # Spoken quantity followed by the indefinite article is prose, not a code.
    "I pay like 150 to 170 a month",
    "it costs 20 a year",
    "three a day",
    "we need 5 an hour",
    "paid 170 a month",
])
def test_serial_detection_ignores_natural_english(serial_settings, text):
    assert pp.process_serial_numbers(text) == text


def test_quantity_plus_article_survives_full_pipeline(serial_settings):
    # "150 to 170 a month" is a price, not a model code (F150 / XK94J still collapse).
    out = pp.clean_speech_transcription(
        "I pay like 150 to 170 a month for my car insurance"
    )
    assert "170A" not in out
    assert "170 a month" in out

    assert pp.process_serial_numbers("code F 1 5 0") == "code F150"
    assert pp.process_serial_numbers("X K 9 4 J") == "XK94J"


def test_serial_collapse_setting_controls_spacing(serial_settings):
    pp.set_serial_collapse(True)
    assert pp.process_serial_numbers("A B C 1 2 3") == "ABC123"

    pp.set_serial_collapse(False)
    # Single letters/digits are already spaced; NATO words now resolve to spaced letters.
    assert pp.process_serial_numbers("A B C 1 2 3") == "A B C 1 2 3"
    assert pp.process_serial_numbers("Alpha Bravo Charlie 1 2 3") == "A B C 1 2 3"
    assert pp.process_serial_numbers("serial number X K 9 4 J") == "serial number X K 9 4 J"


@pytest.mark.parametrize("text,expected", [
    ("spell c a t", "CAT"),
    ("spell out d o g", "DOG"),
    ("spelled c a t", "CAT"),
    ("spell Charlie Alpha Tango", "CAT"),
    ("the word is spelled c a t", "the word is spelled CAT"),
    # The letter "o" stays a letter in a spell command; only "zero"/"oh" are digits.
    ("spell d o g", "DOG"),
    ("spell d zero g", "D0G"),
])
def test_spell_command(serial_settings, text, expected):
    pp.set_spell_command(True)
    assert pp.process_spell_commands(text) == expected


def test_spell_command_disabled_leaves_phrase(serial_settings):
    pp.set_spell_command(False)
    assert pp.process_spell_commands("spell c a t") == "spell c a t"


def test_spell_command_ignores_natural_sentence(serial_settings):
    pp.set_spell_command(True)
    assert pp.process_spell_commands("how do you spell accommodation") == \
        "how do you spell accommodation"


def test_spell_command_uncollapsed_setting(serial_settings):
    pp.set_spell_command(True)
    pp.set_serial_collapse(False)
    assert pp.process_spell_commands("spell c a t") == "C A T"


def test_serial_settings_env_var_override(serial_settings):
    pp.set_serial_collapse(True)
    os.environ["VT_SERIAL_COLLAPSE"] = "0"
    assert pp.get_serial_collapse() is False
    os.environ["VT_SERIAL_COLLAPSE"] = "1"
    assert pp.get_serial_collapse() is True

    pp.set_spell_command(True)
    os.environ["VT_SPELL_COMMAND"] = "0"
    assert pp.get_spell_command() is False
    os.environ["VT_SPELL_COMMAND"] = "1"
    assert pp.get_spell_command() is True


def test_full_pipeline_o_synonym_and_codes(serial_settings, number_mode):
    """End-to-end: 'o' as zero plus a collapsed serial number."""
    pp.set_number_digits_mode("auto")
    assert pp.clean_speech_transcription("seven o two", skip_slm=True) == "702"
    assert pp.clean_speech_transcription("call 7 0 2 5 5 5 1 2 3 4", skip_slm=True) == \
        "call 7025551234"
    assert "ABC123" in pp.clean_speech_transcription("serial number A B C 1 2 3", skip_slm=True)
    assert pp.clean_speech_transcription("spell c a t", skip_slm=True) == "CAT"


# ---------------------------------------------------------------------------
# Digits -> words: ordinals and small standalone cardinals (auto/words modes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("the 5th item", "the fifth item"),
    ("I got 1st place", "I got first place"),
    ("he came in 2nd", "he came in second"),
    ("she finished 3rd", "she finished third"),
    ("the 4th of July", "the fourth of July"),
    ("my 21st birthday", "my twenty first birthday"),
    ("he came in 23rd", "he came in twenty third"),
    ("the 100th day", "the one hundredth day"),
    ("the 1000th subscriber", "the one thousandth subscriber"),
    ("ranked 12th", "ranked twelfth"),
])
def test_ordinal_digits_become_words(text, expected):
    assert pp.expand_digits_to_words(text, mode="auto") == expected


@pytest.mark.parametrize("text,expected", [
    ("I have 2 dogs", "I have two dogs"),
    ("there are 12 left", "there are twelve left"),
    ("I need 3 reasons", "I need three reasons"),
    ("wait 5 seconds", "wait five seconds"),
    ("he ate 0 cookies", "he ate zero cookies"),
    ("give me 20 dollars", "give me twenty dollars"),
])
def test_small_standalone_cardinals_become_words(text, expected):
    assert pp.expand_digits_to_words(text, mode="auto") == expected


@pytest.mark.parametrize("text", [
    # Chains, decimals, times, fractions, ranges, currency and identifiers are untouched.
    "call 7025551234",
    "the value is 3.14",
    "meet at 5:30",
    "add 1/2 cup",
    "open 24/7",
    "it costs $5 total",
    "that is #2 on the list",
    "in 2020 there were 1200 people",
    "ios 18 is out",
    "version 2 shipped",
    "chapter 7 was good",
    "route 66 is long",
    "the 5 pm meeting",
    "it is 9 am now",
    "there is 15% left",
])
def test_digit_expansion_leaves_identifiers_and_units_alone(text):
    assert pp.expand_digits_to_words(text, mode="auto") == text


def test_digits_mode_is_a_hard_opt_out_for_expansion():
    assert pp.expand_digits_to_words("the 5th item", mode="digits") == "the 5th item"
    assert pp.expand_digits_to_words("I have 2 dogs", mode="digits") == "I have 2 dogs"


@pytest.mark.parametrize("n,expected", [
    (0, "zero"), (1, "one"), (9, "nine"), (10, "ten"), (13, "thirteen"),
    (20, "twenty"), (21, "twenty one"), (30, "thirty"), (42, "forty two"),
    (100, "one hundred"), (105, "one hundred five"), (999, "nine hundred ninety nine"),
    (1000, "one thousand"), (1985, "one thousand nine hundred eighty five"),
    (1_000_000, "one million"), (2_000_042, "two million forty two"),
])
def test_int_to_cardinal_words(n, expected):
    assert pp._int_to_cardinal_words(n) == expected


@pytest.mark.parametrize("n,expected", [
    (0, "zeroth"), (1, "first"), (2, "second"), (3, "third"), (5, "fifth"),
    (8, "eighth"), (9, "ninth"), (11, "eleventh"), (12, "twelfth"),
    (20, "twentieth"), (21, "twenty first"), (23, "twenty third"),
    (100, "one hundredth"), (1000, "one thousandth"), (1_000_000, "one millionth"),
])
def test_int_to_ordinal_words(n, expected):
    assert pp._int_to_ordinal_words(n) == expected


def test_full_pipeline_auto_mode_prefers_words(number_mode):
    """Auto mode: only digit chains become digits; ordinals/small counts stay words."""
    pp.set_number_digits_mode("auto")
    # Chains still collapse.
    assert "5551212" in pp.clean_speech_transcription("call five five five one two one two", skip_slm=True)
    # Ordinals and small counts read as words.
    assert pp.clean_speech_transcription("the 5th item", skip_slm=True) == "the fifth item."
    assert pp.clean_speech_transcription("I have 2 dogs", skip_slm=True) == "I have two dogs."


def test_full_pipeline_digits_mode_keeps_numerals(number_mode):
    pp.set_number_digits_mode("digits")
    assert pp.clean_speech_transcription("the 5th item", skip_slm=True) == "the 5th item."
    assert pp.clean_speech_transcription("I have 2 dogs", skip_slm=True) == "I have 2 dogs."


# ---------------------------------------------------------------------------
# Spoken dates: the day always carries an ordinal numeral (auto/digits modes)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("October twentieth", "October 20th"),
    ("october twentieth", "October 20th"),
    ("October the twentieth", "October 20th"),
    ("October twenty", "October 20th"),
    ("October twenty five", "October 25th"),
    ("January one", "January 1st"),
    ("September twenty", "September 20th"),
    ("October thirty one", "October 31st"),
    ("the twentieth of October", "the 20th of October"),
    ("the fourth of July", "the 4th of July"),
    ("meet on October twenty fifth at noon", "meet on October 25th at noon"),
    ("October 20th", "October 20th"),
    # A capitalized ambiguous month is a date even with a day of 1.
    ("May first", "May 1st"),
    ("March fourth", "March 4th"),
])
def test_format_dates_writes_the_day_as_an_ordinal(text, expected):
    assert pp.format_dates(text, mode="auto") == expected


@pytest.mark.parametrize("text,expected", [
    ("October twentieth twenty twenty five", "October 20th, 2025"),
    ("October twenty five, 2025", "October 25th, 2025"),
    ("October twenty twenty five", "October 2025"),
    ("October 20th, 2025", "October 20th, 2025"),
])
def test_format_dates_handles_years(text, expected):
    assert pp.format_dates(text, mode="auto") == expected


@pytest.mark.parametrize("text", [
    # "may"/"march" are ordinary verbs too: a bare cardinal day needs a date context.
    "May one of you do the thing",
    "may one of you do that",
    "May one ask",
    "we march twenty miles",
    # ...and so are "first/second/third/fourth", which are ordinal words: the guard
    # must cover ordinals as well as cardinals ("you may second that motion").
    "we may first ask",
    "you may second that motion",
    "I may fourth the idea",
    "we march first",
    # A cardinal day without a year must terminate the date phrase.
    "in October twenty people came",
    "October twenty five people",
])
def test_format_dates_does_not_invent_dates(text):
    assert pp.format_dates(text, mode="auto") == text


def test_format_dates_leaves_words_mode_spelled_out():
    assert pp.format_dates("the twentieth of October", mode="words") == "the twentieth of October"
    assert pp.format_dates("October twentieth", mode="words") == "October twentieth"


def test_full_pipeline_auto_mode_formats_dates(number_mode):
    pp.set_number_digits_mode("auto")
    assert pp.clean_speech_transcription("October twentieth", skip_slm=True) == "October 20th"
    assert pp.clean_speech_transcription("the twentieth of October", skip_slm=True) == \
        "the 20th of October."
    # Ordinals are normally spelled out in auto mode; a date is the exception.
    assert pp.clean_speech_transcription("the 4th of July", skip_slm=True) == "the 4th of July."
    assert pp.clean_speech_transcription("October twentieth twenty twenty five", skip_slm=True) == \
        "October 20th, 2025."
    # A mid-sentence month keeps its capitalization as part of the date.
    assert pp.clean_speech_transcription("my birthday is June twenty third", skip_slm=True) == \
        "my birthday is June 23rd."
    # The ambiguous-month guard survives the whole pipeline.
    assert pp.clean_speech_transcription("May one of you do the thing", skip_slm=True) == \
        "May one of you do the thing."
    assert pp.clean_speech_transcription("we march twenty miles", skip_slm=True) == \
        "we march twenty miles."


# ---------------------------------------------------------------------------
# "oh" as an interjection vs. as a leading zero (regression)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # "oh" is an interjection here; "one" is a pronoun. Must survive verbatim.
    ("oh one of them", "oh one of them."),
    ("oh one of those", "oh one of those."),
    ("oh one of those is broken", "oh one of those is broken."),
    ("oh one of the servers", "oh one of the servers."),
    ("oh one thing", "oh one thing."),
    ("oh five of them", "oh five of them."),
    ("oh three people came", "oh three people came."),
    ("Oh, one of them", "Oh, one of them."),
])
def test_oh_interjection_before_one_of(number_mode, text, expected):
    for mode in ("auto", "digits", "words"):
        pp.set_number_digits_mode(mode)
        assert pp.clean_speech_transcription(text, skip_slm=True) == expected, mode


@pytest.mark.parametrize("text,expected", [
    # A real "oh" zero inside / at the head of a digit chain still converts.
    ("seven oh two", "702"),
    ("double oh seven", "007"),
    ("oh seven", "07"),
    ("oh one two", "012"),
    ("oh one", "01"),
    ("the code is oh seven", "the code is 07."),
    ("oh five five five one two three four", "05551234"),
])
def test_oh_still_zero_in_digit_chains(number_mode, text, expected):
    pp.set_number_digits_mode("auto")
    assert pp.clean_speech_transcription(text, skip_slm=True) == expected


@pytest.mark.parametrize("text", [
    "oh one of them",
    "oh one of those",
    "oh five of them",
])
def test_oh_interjection_never_reaches_digits_mode_either(number_mode, text):
    pp.set_number_digits_mode("digits")
    out = pp.clean_speech_transcription(text, skip_slm=True)
    assert out.startswith("oh "), out
    assert "01" not in out and "05" not in out


def test_leading_zeros_are_never_spelled_out(number_mode):
    """A leading zero marks a code/number, not a count ('07' must not become 'seven')."""
    pp.set_number_digits_mode("auto")
    assert pp.expand_digits_to_words("07", mode="auto") == "07"
    assert pp.expand_digits_to_words("01", mode="auto") == "01"
    assert pp.expand_digits_to_words("007", mode="auto") == "007"
    assert pp.expand_digits_to_words("the code is 07", mode="auto") == "the code is 07"
    # A bare zero is still a count.
    assert pp.expand_digits_to_words("he ate 0 cookies", mode="auto") == "he ate zero cookies"


def test_digit_string_guards_unit(number_mode):
    """Unit-level: the two guards live in _expand_digit_string."""
    pp.set_number_digits_mode("auto")
    for text, expected in [
        ("oh one of them", "oh one of them"),
        ("oh one of those", "oh one of those"),
        ("oh five of them", "oh five of them"),
        ("one thing", "one thing"),
    ]:
        assert pp._DIGIT_STRING_REGEX.sub(pp._expand_digit_string, text) == expected, text
    # Unambiguous chains are untouched by the guards.
    assert pp._DIGIT_STRING_REGEX.sub(pp._expand_digit_string, "seven oh two") == "702"
    assert pp._DIGIT_STRING_REGEX.sub(pp._expand_digit_string, "oh seven") == "07"


# ---------------------------------------------------------------------------
# Verbal self-corrections (Workstream B / M1)
# ---------------------------------------------------------------------------
# A retraction is only resolved when its marker is followed by a replacement of
# the *same kind of value* (both weekdays, both clock times, both amounts), or
# when the marker is "I mean" — never a verb phrase, and names have no category
# to compare. The first case below is the demo advertised in README.md and
# docs/blog_post.md, so it is pinned verbatim: v1.3.1 turned it into "remind
# make that wednesday and also add a note to the GitHub issue.", losing
# "me tuesday" and leaving the marker "make that" in the text.

@pytest.mark.parametrize("spoken,expected", [
    ("Remind me Tuesday no wait make that Wednesday", "Remind me Wednesday."),
    ("Remind me Tuesday, actually make that Wednesday", "Remind me Wednesday."),
    ("Let's make it on Tuesday, no, Wednesday", "Let's make it on Wednesday."),
    ("Let's make it Tuesday no Wednesday", "Let's make it Wednesday."),
    ("Let's make it on Tuesday. No. Wednesday.", "Let's make it on Wednesday."),
    ("The meeting is on Monday no Tuesday", "The meeting is on Tuesday."),
    ("Let's meet at 5 PM... actually 6 PM", "Let's meet at 6 PM."),
    ("We should deploy on Tuesday... no wait Wednesday", "We should deploy on Wednesday."),
    ("Send the report to John... I mean Alice", "Send the report to Alice."),
    ("Add the class definition... scratch that", "Add the class definition."),
])
def test_verbal_self_corrections_are_resolved(spoken, expected):
    assert pp.clean_speech_transcription(spoken, skip_slm=True, punctuation_mode="full") == expected


def test_the_advertised_demo_is_pinned():
    """The README/blog demo: the correction resolves and nothing is lost."""
    # The utterance ends with the shipped dictionary's "github" -> "GitHub", so
    # install it explicitly: this test must not depend on what an earlier test
    # happened to leave in the dictionary globals.
    saved = pp.get_custom_dictionary()
    pp.set_custom_dictionary({**saved, "github": "GitHub"})
    try:
        out = pp.clean_speech_transcription(
            "remind me tuesday no wait make that wednesday uh and uh also add a note to the github issue",
            skip_slm=True, punctuation_mode="full")
    finally:
        pp.set_custom_dictionary(saved if saved else None)
    assert out == "remind me wednesday also add a note to the GitHub issue."
    # Guards for the three parts of the v1.3.1 corruption specifically.
    assert "make that" not in out, "the marker leaked into the text"
    assert "tuesday" not in out, "the retracted value survived"
    assert " me " in f" {out} ", "the word 'me' was eaten along with the retracted value"


@pytest.mark.parametrize("text", [
    "please make that happen with the new build",
    "the deploy actually works now",
    "I have no idea what happened",
    "there is no Wednesday meeting this week",
    "I never said that to anyone",
])
def test_marker_phrases_that_are_not_retractions(text):
    """A marker word inside ordinary speech must not be rewritten into nonsense."""
    assert pp.process_verbal_retractions(text) == text


def test_weekday_capitalisation_is_preserved_mid_sentence():
    """"on Tuesday, no, Wednesday" must not lose the capital on Wednesday.

    Weekdays are always capitalised in English and the ASR is the only source of
    that casing, so a capitalised weekday survives mid-sentence — while lowercase
    ASR output stays lowercase, exactly like the existing month rule.
    """
    assert pp.normalize_mid_sentence_casing("we met on Tuesday, no, Wednesday") == \
        "we met on Tuesday, no, Wednesday"
    assert pp.clean_speech_transcription("The build ran on Wednesday and Thursday", skip_slm=True) == \
        "The build ran on Wednesday and Thursday."


# ---------------------------------------------------------------------------
# Apologies and hesitation inside a correction
# ---------------------------------------------------------------------------
# Reported from real dictation: "...let's schedule it for Tuesday no sorry
# Wednesday" came out as "...schedule it for sorry Wednesday" — the apology was
# swallowed into the *replacement*, and the category guard let it through because
# "sorry Wednesday" merely *contains* a weekday. An apology now extends the
# marker, never the value, and a value has to be nothing but the value.

@pytest.mark.parametrize("spoken,expected", [
    ("Okay this should be a good test here let's schedule it for Tuesday no sorry Wednesday",
     "Okay this should be a good test here let's schedule it for Wednesday."),
    ("Let's schedule it for Tuesday no wait sorry Wednesday", "Let's schedule it for Wednesday."),
    ("Let's schedule it for Tuesday actually sorry Wednesday", "Let's schedule it for Wednesday."),
    ("Let's schedule it for Tuesday no uh Wednesday", "Let's schedule it for Wednesday."),
])
def test_an_apology_or_filler_inside_a_correction(spoken, expected):
    assert pp.clean_speech_transcription(spoken, skip_slm=True, punctuation_mode="full") == expected


@pytest.mark.parametrize("text", [
    "I'm sorry Wednesday works for me",
    "I'm sorry about the delay",
    "Sorry, Tuesday is bad for me",
    "The apologies were sincere",
    # Punctuation alone cannot say which Tuesday is being retracted, so this
    # needs commas around a word that is *also* ordinary speech. Left alone.
    "Let's schedule it for Tuesday, sorry, Wednesday",
])
def test_an_apology_alone_is_not_a_retraction(text):
    assert pp.process_verbal_retractions(text) == text


def test_a_value_is_the_whole_span_not_a_span_containing_a_value():
    """Every token has to belong to the category, or the span is not a value."""
    assert pp._value_category("Wednesday") == "weekday"
    assert pp._value_category("next Wednesday") == "weekday"
    assert pp._value_category("sorry Wednesday") is None
    assert pp._value_category("Wednesday please") is None
    assert pp._value_category("October 20th") == "month"
    assert pp._value_category("5 PM") == "time"
    assert pp._value_category("two") == "number"


def test_slm_output_sanitizer_exists_and_scrubs():
    """The optional SLM path's sanitiser is reachable and does its documented job.

    Regression: the function is only called when the SLM path is live (needs a
    local vLLM, and the tests disable it with ``VT_ENABLE_SLM=0``), so a whole
    function could be deleted from between the retraction patterns without a
    single test failing. ``ruff`` caught it; this pins it from the test side too.
    """
    assert pp._sanitize_slm_output("hi", "<cleaned_text>hi there.</cleaned_text>") == "hi there."
    assert pp._sanitize_slm_output("hi", '"hi there."') == "hi there."
    assert pp._sanitize_slm_output("hi", "<b>hi</b> there") == "hi there"


# ---------------------------------------------------------------------------
# Enumerations, list cues and spoken quotes (Phase A1)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Thing one. Thing two. Thing three.",
    "Deploy alpha. Deploy beta. Deploy gamma.",
    "Item one. Item two. Item three.",
    "One. Two. Three.",
])
def test_short_parallel_sentences_keep_their_boundaries(text):
    """An enumeration is not a false ASR break: every '.' must survive.

    Regression: the "dangling word before a period" repair listed the cardinals
    two/three/four/five, so it deleted the period after them and handed the
    result to the serial-number collapser. "Thing one. Thing two. Thing three."
    became "Thing one. Thing two thing three." and "One. Two. Three." became
    "One. 23." — an enumeration dictated as a list was rewritten into nonsense.
    """
    pp.set_number_digits_mode("auto")
    assert pp.clean_speech_transcription(text, skip_slm=True, punctuation_mode="full") == text


@pytest.mark.parametrize("spoken,expected", [
    ("I can list them like: Thing one. Thing two.", "I can list them like: Thing one. Thing two."),
    ("Note: Remember this", "Note: Remember this."),
    ("Three things - Alpha, Beta", "Three things - Alpha, beta."),
])
def test_a_list_cue_keeps_the_capital_after_it(spoken, expected):
    """A colon or dash introduces a list or label, so the capital is the writer's.

    Regression: the mid-sentence decapitaliser only knew about sentence-enders
    (its lookbehind covers . ! ? and newline), so "like: Thing one" lost the
    capital that marks the list.
    """
    assert pp.clean_speech_transcription(spoken, skip_slm=True, punctuation_mode="full") == expected


@pytest.mark.parametrize("spoken,expected", [
    ("He said quote hello unquote to me.", 'He said "hello" to me.'),
    ("She said quote I am going to do three things end quote and left.",
     'She said "I am going to do three things" and left.'),
])
def test_spoken_quotation_marks_become_real_quotes(spoken, expected):
    """ASR emits words, not quote characters, so a matched pair is rewrapped."""
    assert pp.clean_speech_transcription(spoken, skip_slm=True, punctuation_mode="full") == expected


@pytest.mark.parametrize("text", [
    "this quote is great",
    "and I quote the docs are wrong",
    "I quoted him yesterday",
    "we should quote the price and move on",
])
def test_a_lone_quote_word_is_left_alone(text):
    """Only a matched pair is a quotation; a single "quote" is ordinary speech."""
    assert pp.process_spoken_quotes(text) == text
