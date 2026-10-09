#!/usr/bin/env python3
"""
Unit tests for the optional on-device formatter.

Everything here is model-free, which is the point of milestone C1: the prompt
contract, the registry, the guardrails and the fail-open path can all be proven
before a single weight is downloaded. ``formatters/noop.py`` makes that possible
and a ``fake`` backend installed per test drives the failure paths.

The two halves that matter equally:

* every guardrail rejects the failure it exists for, and
* the guardrails **accept** the real Wispr-parity fixtures. A validator that
  rejects everything would pass half of these tests and destroy the feature.

Hermeticity: tests/shared/conftest.py blocks real non-loopback connects, so a
backend that tried to reach a network endpoint fails loudly here.
"""
import sys
import time
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import formatter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def install_fake(monkeypatch, format_text, *, available=True, warm=None):
    """Register a `fake` backend and return its registry name."""
    module = types.SimpleNamespace(
        available=lambda: available,
        warm=warm or (lambda: None),
        format_text=format_text,
    )
    monkeypatch.setitem(formatter.BACKENDS, "fake", "voice_transcriber.formatters.fake")
    monkeypatch.setitem(formatter._loaded, "fake", module)
    return "fake"


@pytest.fixture(autouse=True)
def _clear_backend_cache():
    formatter.reset_backend_cache()
    yield
    formatter.reset_backend_cache()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_noop_backend_is_registered_and_complete():
    module = formatter.load_backend("noop")
    assert module is not None
    for fn in formatter.REQUIRED_FUNCTIONS:
        assert hasattr(module, fn), f"noop backend is missing {fn}"


def test_default_backend_is_a_registry_key():
    assert formatter.DEFAULT_BACKEND in formatter.BACKENDS


def test_available_backends_lists_the_registry():
    assert formatter.available_backends() == sorted(formatter.BACKENDS)
    assert {"noop", "s1-mini", "llama-server"} <= set(formatter.available_backends())


def test_resolve_backend_name_defaults_to_s1_mini():
    assert formatter.resolve_backend_name(None) == formatter.DEFAULT_BACKEND
    assert formatter.resolve_backend_name("noop") == "noop"
    assert formatter.resolve_backend_name("  NOOP  ") == "noop"


def test_resolve_backend_name_rejects_an_unknown_name():
    with pytest.raises(formatter.UnknownBackendError, match="Unknown formatter backend"):
        formatter.resolve_backend_name("gpt-4")


def test_the_default_backend_is_absent_until_its_milestone():
    """Fail safe, not fail loud.

    ``s1-mini`` names the *in-process* backend and its module lands in milestone
    C4; the shipped default is ``llama-server``, which does exist. Resolving an
    absent module must mean "no formatting available", never a crash on the
    dictation path.
    """
    assert formatter.load_backend("s1-mini") is None
    assert formatter.load_backend("not-a-backend") is None


def test_the_shipped_backend_exists_and_satisfies_the_contract():
    module = formatter.load_backend("llama-server")
    assert module is not None, "C3 ships formatters/llama_server.py"
    for fn in formatter.REQUIRED_FUNCTIONS:
        assert hasattr(module, fn)


def test_an_unavailable_shipped_backend_still_yields_the_original(monkeypatch):
    """Present-but-unusable is the common case on a machine with no weights."""
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.delenv("VT_FORMATTER_SERVER_URL", raising=False)
    formatter.reset_backend_cache()
    original = "buy milk"
    assert formatter.format_text(original, backend="llama-server") == original


def test_backend_missing_part_of_the_contract_loads_as_none(monkeypatch):
    incomplete = types.SimpleNamespace(available=lambda: True)  # no format_text/warm
    monkeypatch.setitem(formatter.BACKENDS, "broken", "voice_transcriber.formatters.broken")
    monkeypatch.setitem(formatter._loaded, "broken", incomplete)
    # A cached module is trusted; the contract is checked at import time, so
    # simulate the real path by clearing the cache entry.
    formatter._loaded.pop("broken")
    monkeypatch.setattr(
        formatter.importlib, "import_module", lambda _name: incomplete)
    assert formatter.load_backend("broken") is None


# ---------------------------------------------------------------------------
# The exact integration contract (plan sec 5.3)
# ---------------------------------------------------------------------------
def test_system_prompt_is_verbatim():
    assert formatter.SYSTEM_PROMPT == (
        "You are a text normalizer for speech-to-text transcripts. The input begins "
        "with a control line specifying the styling, structure, and context settings; "
        "clean the transcript to match those settings and output only the cleaned text."
    )


def test_assistant_prefix_matches_the_trained_shape():
    assert formatter.ASSISTANT_PREFIX == "<|im_start|>assistant\n<think>\n\n</think>\n\n"


def test_control_line_shape():
    assert formatter.build_control_line("casual", "lists", "email") == (
        "[Styling: casual] [Structure: lists] [Context: email]")


def test_user_message_is_control_line_newline_then_transcript():
    message = formatter.build_user_message(
        "hello there", style="formal", structure="prose", context="general")
    assert message == (
        "[Styling: formal] [Structure: prose] [Context: general]\nhello there")


def test_structure_maps_from_the_existing_setting():
    """There is deliberately no fourth setting; structure_mode is the axis."""
    assert formatter.structure_for_mode("off") == "prose"
    assert formatter.structure_for_mode("inline") == "lists"
    assert formatter.structure_for_mode("blocks") == "lists"
    assert formatter.structure_for_mode("") == "lists"
    assert formatter.structure_for_mode(None) == "lists"


def test_max_new_tokens_is_the_models_own_ceiling():
    assert formatter.max_new_tokens(0) == 32
    assert formatter.max_new_tokens(100) == 162
    assert formatter.max_new_tokens(1000) == 1332


def test_axis_values_are_the_trained_ones():
    assert formatter.STYLES == ("casual", "semi-casual", "semi-formal", "formal")
    assert formatter.STRUCTURES == ("prose", "lists")
    assert formatter.CONTEXTS == ("general", "email")
    assert formatter.DEFAULT_STYLE == "semi-formal"
    assert formatter.DEFAULT_CONTEXT == "general"


# ---------------------------------------------------------------------------
# Canonicalisation - the allowed transformations cancel out
# ---------------------------------------------------------------------------
def test_allowed_transformations_are_folded_away():
    # capitalisation / punctuation / number words / contractions all compare equal
    assert formatter.validate(
        "meet me at six PM", "Meet me at 6 pm.") is None
    assert formatter.validate(
        "i don't think so", "I do not think so.") is None
    assert formatter.validate(
        "it's fine", "It is fine") is None


def test_canonicalisation_strips_punctuation_and_case():
    assert formatter._canonical_tokens("Hey, Michelle!") == ["hey", "michelle"]
    assert formatter._canonical_tokens("seven PM") == ["7", "pm"]
    assert formatter._canonical_tokens("don't") == ["do", "not"]


def test_canonicalisation_splits_the_letter_digit_boundary():
    """Regression for a guardrail that threw away correct answers.

    Matching ``[a-z0-9]+`` kept letters glued to digits, so the *input*
    "seven PM" canonicalised to ``[7, pm]`` while the model's "7pm" canonicalised
    to ``[7pm]``. Guardrail 2 then reported the model's own number normalisation
    as invented content. The real-model e2e run found it on the retraction and
    email fixtures, where the answer was right and got discarded, so the pipeline
    silently fell back to the unformatted transcript.
    """
    assert formatter._canonical_tokens("7pm") == ["7", "pm"]
    assert formatter._canonical_tokens("seven PM") == formatter._canonical_tokens("7pm")
    assert formatter._canonical_tokens("WPA2") == ["wpa", "2"]
    assert formatter._canonical_tokens("ABC123") == ["abc", "123"]


@pytest.mark.parametrize("original,candidate", [
    ("meet me at seven PM", "Meet me at 7pm."),
    ("meet me at seven PM", "Meet me at 7 pm."),
    ("it is three PM", "It is 3pm."),
    ("call me at five thirty", "Call me at 5:30."),
])
def test_number_normalisation_across_the_boundary_is_accepted(original, candidate):
    assert formatter.validate(original, candidate) is None


@pytest.mark.parametrize("original,candidate", [
    # The tokeniser does not fold ordinals ("twentieth" vs "20th") or serial
    # collapse ("W P A 2" vs "WPA2"). Both are left rejected here on purpose
    # rather than quietly accommodated, because both transformations are
    # deterministic stages that run *before* the formatter - so the model never
    # actually sees the split form, and accepting them would widen guardrail 2
    # to license word-joining for a case that cannot arise.
    ("the meeting is on october twentieth", "The meeting is on October 20th."),
    ("the code is W P A 2", "The code is WPA2."),
])
def test_transformations_that_run_before_the_formatter_are_not_licensed(original, candidate):
    assert formatter.validate(original, candidate) is not None


def test_folding_the_boundary_still_admits_no_invented_content():
    """The fix must loosen only the boundary, not the no-new-content rule."""
    assert formatter.validate("buy milk", "Buy milk and bread 7pm.") is not None
    assert formatter.validate("the code is wpa 2", "The code is wpa2 plus more.") is not None


# ---------------------------------------------------------------------------
# Guardrail 1 - length ceiling
# ---------------------------------------------------------------------------
def test_guardrail_1_rejects_runaway_output():
    original = "I want to buy milk"
    reason = formatter.validate(original, " ".join(["milk"] * 200))
    assert reason is not None
    assert "ceiling" in reason


# ---------------------------------------------------------------------------
# Guardrail 1b - retention floor (the half the ceiling cannot do)
# ---------------------------------------------------------------------------
def test_guardrail_1b_rejects_a_summary():
    """A summary is *shorter* than its input, so the ceiling cannot catch it."""
    original = ("I need to pick up the dry cleaning, drop the car at the garage, "
                "call the dentist about Thursday, and finish the quarterly report "
                "before the standup on Monday morning.")
    reason = formatter.validate(original, "Errands and work tasks.")
    assert reason is not None
    assert "retained" in reason


def test_guardrail_1b_tolerates_heavy_but_legitimate_rewriting():
    """A resolved retraction removes real words; it must not trip the floor."""
    original = ("Hey Michelle, meet me at my apartment lobby at six PM, "
                "actually no, seven PM.")
    formatted = "Hey Michelle, meet me at my apartment lobby at 7 pm."
    assert formatter.validate(original, formatted) is None


# ---------------------------------------------------------------------------
# Guardrail 2 - no new content
# ---------------------------------------------------------------------------
def test_guardrail_2_rejects_an_invented_sentence():
    original = "I want to grab three things at the store: milk and eggs."
    candidate = "I want to grab three things at the store: milk and eggs. You should also buy bread."
    reason = formatter.validate(original, candidate)
    assert reason is not None
    assert "absent from the input" in reason
    assert "bread" in reason


def test_guardrail_2_rejects_an_invented_function_word():
    """Even a harmless-looking addition is new content."""
    reason = formatter.validate("buy milk", "Please buy the milk")
    assert reason is not None


# ---------------------------------------------------------------------------
# Guardrail 3 - list-item provenance
# ---------------------------------------------------------------------------
def test_guardrail_3_rejects_an_invented_list_item():
    original = ("I want to grab three things at the grocery store: milk for the cake, "
                "eggs for breakfast, and white bread.")
    candidate = ("I want to grab three things at the grocery store:\n"
                 "- milk for the cake\n- eggs for breakfast\n- white bread\n- butter")
    reason = formatter.validate(original, candidate)
    assert reason is not None
    assert "butter" in reason


def test_guardrail_3_accepts_verbatim_items():
    original = ("I want to grab three things at the grocery store: milk for the cake, "
                "eggs for breakfast, and white bread.")
    candidate = ("I want to grab three things at the grocery store:\n"
                 "- milk for the cake\n- eggs for breakfast\n- white bread")
    assert formatter.validate(original, candidate) is None


def test_guardrail_3_recognises_numbered_items():
    original = "First, unpack the boxes. Second, build the shelves. Third, load them."
    candidate = "1. Unpack the boxes.\n2. Build the shelves.\n3. Load them."
    assert formatter.validate(original, candidate) is None


# ---------------------------------------------------------------------------
# Guardrail 4 - refusal / meta commentary
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("candidate", [
    "I'm sorry, I can't help with that.",
    "Here is the cleaned text: buy milk.",
    "<cleaned_text>buy milk</cleaned_text>",
    "```\nbuy milk\n```",
    "Certainly! Buy milk.",
    "As an AI, I cannot process this.",
])
def test_guardrail_4_rejects_meta_commentary(candidate):
    reason = formatter.validate("buy milk", candidate)
    assert reason is not None
    assert "meta commentary" in reason


# ---------------------------------------------------------------------------
# Guardrail 5 - empty output is only valid for content-free input
# ---------------------------------------------------------------------------
def test_guardrail_5_accepts_empty_output_for_filler_only_input():
    """Filler-only input returning nothing is correct, not a failure."""
    assert formatter.validate("um, uh, er", "") is None
    assert formatter.validate("um, uh, er", "   ") is None


def test_guardrail_5_rejects_empty_output_when_there_was_content():
    reason = formatter.validate("buy milk and eggs", "")
    assert reason is not None
    assert "empty" in reason


def test_has_content_words_ignores_fillers():
    assert formatter.has_content_words("buy milk") is True
    assert formatter.has_content_words("um uh er") is False
    assert formatter.has_content_words("") is False
    assert formatter.has_content_words(None) is False


# ---------------------------------------------------------------------------
# Guardrails 6 and 7 - timeout and fail open
# ---------------------------------------------------------------------------
def test_noop_backend_returns_the_text_unchanged():
    text = "Hey michelle, meet me at 7 pm."
    assert formatter.format_text(text, backend="noop") == text


def test_guardrail_7_unknown_backend_returns_the_input():
    text = "buy milk"
    assert formatter.format_text(text, backend="not-a-backend") == text


def test_guardrail_7_unavailable_backend_returns_the_input(monkeypatch):
    name = install_fake(
        monkeypatch,
        lambda text, **kw: "SHOULD NOT BE USED",
        available=False)
    assert formatter.format_text("buy milk", backend=name) == "buy milk"


def test_guardrail_7_a_raising_backend_returns_the_input(monkeypatch):
    def explode(text, **kwargs):
        raise RuntimeError("model exploded")

    name = install_fake(monkeypatch, explode)
    assert formatter.format_text("buy milk", backend=name) == "buy milk"


def test_guardrail_7_a_non_string_result_returns_the_input(monkeypatch):
    name = install_fake(monkeypatch, lambda text, **kw: None)
    assert formatter.format_text("buy milk", backend=name) == "buy milk"


def test_guardrail_7_invalid_output_returns_the_unformatted_text(monkeypatch):
    """The important one: a *successful* call can still be rejected."""
    name = install_fake(
        monkeypatch,
        lambda text, **kw: text + " You should also buy bread.")
    original = "buy milk"
    assert formatter.format_text(original, backend=name) == original


def test_guardrail_6_a_late_backend_returns_the_input(monkeypatch):
    """The backstop for a backend that ignored its budget."""
    def slow(text, *, timeout_s=3.0, **kwargs):
        time.sleep(0.05)
        return text.upper()

    name = install_fake(monkeypatch, slow)
    assert formatter.format_text("buy milk", backend=name,
                                 timeout_s=0.01) == "buy milk"


def test_guardrail_6_timeout_is_passed_through_to_the_backend(monkeypatch):
    seen = {}

    def record(text, *, timeout_s=3.0, **kwargs):
        seen["timeout_s"] = timeout_s
        return text

    name = install_fake(monkeypatch, record)
    formatter.format_text("buy milk", backend=name, timeout_s=1.25)
    assert seen["timeout_s"] == 1.25


def test_empty_input_short_circuits_without_calling_the_backend(monkeypatch):
    calls = []

    def record(text, **kwargs):
        calls.append(text)
        return text

    name = install_fake(monkeypatch, record)
    assert formatter.format_text("   ", backend=name) == "   "
    assert calls == []


def test_warm_reports_readiness_without_raising(monkeypatch):
    assert formatter.warm("noop") is True
    assert formatter.warm("not-a-backend") is False

    def explode():
        raise RuntimeError("cannot preload")

    name = install_fake(monkeypatch, lambda text, **kw: text, warm=explode)
    assert formatter.warm(name) is False


# ---------------------------------------------------------------------------
# Hard gate: never run when nothing may be deleted
# ---------------------------------------------------------------------------
def test_may_run_is_false_when_cleanup_is_off():
    """`cleanup_mode: off` promises nothing is deleted; the formatter deletes."""
    assert formatter.may_run("off") is False
    assert formatter.may_run("  OFF ") is False
    assert formatter.may_run("light") is True
    assert formatter.may_run("standard") is True


# ---------------------------------------------------------------------------
# The acceptance fixtures: the guardrails must ACCEPT real Wispr-shaped output
# ---------------------------------------------------------------------------
WISPR_RETRACTION_IN = ("Hey Michelle, meet me at my apartment lobby at six PM, "
                       "actually no, seven PM.")
WISPR_RETRACTION_OUT = "Hey Michelle, meet me at my apartment lobby at 7 pm."

WISPR_LIST_IN = ("I want to grab three things at the grocery store: milk for the cake, "
                 "eggs for breakfast, and white bread.")
WISPR_LIST_OUT = ("I want to grab three things at the grocery store:\n"
                  "- milk for the cake\n- eggs for breakfast\n- white bread")

WISPR_EMAIL_IN = ("Hi Nora, I am looking forward to working with you. Are you available "
                  "to meet at three PM on Friday? Best, Jacob.")
WISPR_EMAIL_OUT = ("Hi Nora,\n\nI am looking forward to working with you. Are you "
                   "available to meet at 3 pm on Friday?\n\nBest,\nJacob")


@pytest.mark.parametrize("original,candidate", [
    (WISPR_RETRACTION_IN, WISPR_RETRACTION_OUT),
    (WISPR_LIST_IN, WISPR_LIST_OUT),
    (WISPR_EMAIL_IN, WISPR_EMAIL_OUT),
])
def test_wispr_parity_fixtures_pass_every_guardrail(original, candidate):
    """Regression against an over-strict validator.

    These are the four reference fixtures from the plan, in the exact shape
    Wispr Flow returns them. If a guardrail starts rejecting these, the feature
    is broken even though every rejection test above still passes.
    """
    assert formatter.validate(original, candidate) is None


def test_the_pipeline_returns_formatted_text_end_to_end(monkeypatch):
    """The happy path: a valid rewrite is returned, not discarded."""
    def rewrite(text, **kwargs):
        return WISPR_RETRACTION_OUT

    name = install_fake(monkeypatch, rewrite)
    assert formatter.format_text(WISPR_RETRACTION_IN, backend=name) == WISPR_RETRACTION_OUT
