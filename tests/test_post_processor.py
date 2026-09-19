#!/usr/bin/env python3
"""
Focused unit tests for post_processor formatting helpers.

Covers spoken Unix paths / IP / CIDR conversion (the ``clean_spoken_paths``
feature) and punctuation-mode formatting.
"""

import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

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
