#!/usr/bin/env python3
"""Spec tests for the mode presets — see ``docs/mode_presets.md``.

Three things are pinned here:

1. **Behaviour** — the previews the spec promises are exactly what
   ``apply_punctuation_mode()`` produces for the spec's sample sentence, for
   every alias of every preset.
2. **Differences** — the axes that make the presets distinguishable (trailing
   period, internal punctuation, capitalisation) still separate all five, so a
   refactor cannot quietly collapse two presets into the same style.
3. **Parity** — the surfaces that advertise the presets (the Rich switcher and
   the ratatui settings modal) show the spec's strings, not their own drift.
"""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import post_processor as pp  # noqa: E402
import t2  # noqa: E402


# ---------------------------------------------------------------------------
# The spec, transcribed from docs/mode_presets.md
# ---------------------------------------------------------------------------

SPEC_SAMPLE = "Hey, how are you? I'm good."

# canonical id -> (aliases, badge, preview)
SPEC_PRESETS = {
    "full": (
        ("default", "standard"),
        "[DEFAULT]",
        "Hey, how are you? I'm good.",
    ),
    "no_terminal_period": (
        ("casual", "semi_formal", "no_period", "no_ending_period"),
        "[CASUAL]",
        "Hey, how are you? I'm good",
    ),
    "no_punctuation": (
        ("autocorrect", "phone", "none", "no_punct"),
        "[PHONE]",
        "Hey how are you I'm good",
    ),
    "aesthetic_lowercase": (
        ("aesthetic", "lowercase_punct", "lower_punct"),
        "[AESTH]",
        "hey, how are you? i'm good",
    ),
    "gen_z": (
        ("genz", "pure_gen_z", "lowercase_no_punctuation", "lowercase_no_punct"),
        "[GEN Z]",
        "hey how are you i'm good",
    ),
}

# The documented difference matrix (docs/mode_presets.md section 4): the axes
# that have to keep separating the presets on the sample.
SPEC_DIFFERENCES = {
    # canonical id: (keeps internal ,/?, trailing period, has uppercase, strips punctuation)
    "full": (True, True, True, False),
    "no_terminal_period": (True, False, True, False),
    "no_punctuation": (False, False, True, True),
    "aesthetic_lowercase": (True, False, False, False),
    "gen_z": (False, False, False, True),
}

# switcher id -> canonical id, as the Rich switcher maps them on Enter.
SWITCHER_IDS = {
    "default": "full",
    "casual": "no_terminal_period",
    "autocorrect": "no_punctuation",
    "aesthetic_lowercase": "aesthetic_lowercase",
    "gen_z": "gen_z",
}

RUST_SETTINGS_ROW = REPO_ROOT / "tui-rs" / "src" / "settings_picker.rs"


def every_mode():
    """(canonical id, alias) for every accepted spelling of every preset."""
    for canon, (aliases, _, _) in SPEC_PRESETS.items():
        yield canon, canon
        for alias in aliases:
            yield canon, alias


def trailing_period(text):
    return text.rstrip().endswith(".")


def keeps_internal_punctuation(text):
    return any(ch in text for ch in ",?")


def strips_punctuation(text):
    return not any(ch in text for ch in ",.?!")


def has_uppercase(text):
    return text != text.lower()


# ---------------------------------------------------------------------------
# 1. Behaviour
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("canon,alias", list(every_mode()))
def test_every_alias_produces_the_spec_preview(canon, alias):
    expected = SPEC_PRESETS[canon][2]
    assert pp.apply_punctuation_mode(SPEC_SAMPLE, mode=alias) == expected, (
        f"preset '{alias}' no longer matches the preview documented for "
        f"'{canon}' in docs/mode_presets.md"
    )


@pytest.mark.parametrize("canon,alias", list(every_mode()))
def test_aliases_of_one_preset_are_interchangeable(canon, alias):
    assert pp.apply_punctuation_mode(SPEC_SAMPLE, mode=alias) == pp.apply_punctuation_mode(
        SPEC_SAMPLE, mode=canon
    )
    assert t2.get_canonical_preset_name(alias) == canon


def test_the_sample_is_the_one_the_spec_documents():
    assert t2.PRESET_SAMPLE == SPEC_SAMPLE


def test_full_is_the_identity():
    assert pp.apply_punctuation_mode(SPEC_SAMPLE, mode="full") == SPEC_SAMPLE


@pytest.mark.parametrize("mode", ["definitely-not-a-mode", "FULLY", "nonsense_style"])
def test_unknown_mode_is_a_no_op(mode):
    """An unrecognised *non-empty* mode leaves the text alone."""
    assert pp.apply_punctuation_mode(SPEC_SAMPLE, mode=mode) == SPEC_SAMPLE


def test_missing_mode_uses_the_configured_preset(monkeypatch):
    """``None``/empty is not a no-op: it means "use whatever is configured"."""
    monkeypatch.setattr(pp, "_PUNCTUATION_MODE", "gen_z")
    assert pp.get_punctuation_mode() == "gen_z"
    assert pp.apply_punctuation_mode(SPEC_SAMPLE, mode=None) == SPEC_PRESETS["gen_z"][2]
    assert pp.apply_punctuation_mode(SPEC_SAMPLE, mode="") == SPEC_PRESETS["gen_z"][2]


@pytest.mark.parametrize("canon", sorted(SPEC_PRESETS))
def test_presets_are_idempotent(canon):
    once = pp.apply_punctuation_mode(SPEC_SAMPLE, mode=canon)
    assert pp.apply_punctuation_mode(once, mode=canon) == once


# ---------------------------------------------------------------------------
# 2. The differences hold up
# ---------------------------------------------------------------------------

def test_the_differences_hold_up():
    """The documented matrix must match what the presets actually do."""
    actual = {}
    for canon in SPEC_PRESETS:
        preview = pp.apply_punctuation_mode(SPEC_SAMPLE, mode=canon)
        actual[canon] = (
            keeps_internal_punctuation(preview),
            trailing_period(preview),
            has_uppercase(preview),
            strips_punctuation(preview),
        )
    assert actual == SPEC_DIFFERENCES


def test_no_two_presets_are_indistinguishable():
    previews = [pp.apply_punctuation_mode(SPEC_SAMPLE, mode=c) for c in SPEC_PRESETS]
    assert len(set(previews)) == len(previews), f"presets collapsed onto each other: {previews}"

    rows = list(SPEC_DIFFERENCES.values())
    duplicates = {row for row in rows if rows.count(row) > 1}
    assert not duplicates, f"presets share a row of the difference matrix: {duplicates}"


def test_each_difference_axis_separates_at_least_one_pair():
    previews = {c: pp.apply_punctuation_mode(SPEC_SAMPLE, mode=c) for c in SPEC_PRESETS}
    axes = {
        "trailing period": lambda t: trailing_period(t),
        "internal punctuation": lambda t: keeps_internal_punctuation(t),
        "capitalisation": lambda t: has_uppercase(t),
    }
    for label, axis in axes.items():
        values = {axis(text) for text in previews.values()}
        assert values == {True, False}, (
            f"the sample no longer exercises '{label}', so the presets cannot be "
            f"compared on it any more"
        )


def test_documented_derivations():
    """The step-by-step rules in the spec's invariants section."""
    full = pp.apply_punctuation_mode(SPEC_SAMPLE, mode="full")
    casual = pp.apply_punctuation_mode(SPEC_SAMPLE, mode="no_terminal_period")
    phone = pp.apply_punctuation_mode(SPEC_SAMPLE, mode="no_punctuation")
    aesthetic = pp.apply_punctuation_mode(SPEC_SAMPLE, mode="aesthetic_lowercase")
    gen_z = pp.apply_punctuation_mode(SPEC_SAMPLE, mode="gen_z")

    # casual == full without a trailing period
    assert casual == full.rstrip().rstrip(".")
    # aesthetic == casual, lowercased
    assert aesthetic == casual.lower()
    # gen_z == phone, lowercased
    assert gen_z == phone.lower()
    # phone keeps intra-token separators (the contraction) while stripping punctuation
    assert "'" in phone and "," not in phone and "?" not in phone
    assert phone.startswith("Hey")


# ---------------------------------------------------------------------------
# 3. Parity between the spec and the surfaces that advertise it
# ---------------------------------------------------------------------------

def test_rich_switcher_shows_the_spec():
    seen = set()
    for switcher_id, name, badge, desc, preview, _color in t2.PRESET_PRESENTATIONS:
        canon = SWITCHER_IDS[switcher_id]
        _, spec_badge, spec_preview = SPEC_PRESETS[canon]
        assert preview == spec_preview, f"switcher preview drifted for {canon}"
        assert badge == spec_badge, f"switcher badge drifted for {canon}"
        assert name and desc, f"switcher entry {canon} lost its name/description"
        seen.add(canon)
    assert seen == set(SPEC_PRESETS), "the switcher no longer lists every preset"
    assert t2.PRESET_CANON_BY_SWITCHER_ID == SWITCHER_IDS
    assert t2.PRESET_SWITCHER_ID_BY_CANON == {v: k for k, v in SWITCHER_IDS.items()}


@pytest.mark.parametrize("canon", sorted(SPEC_PRESETS))
def test_python_settings_menu_shows_the_spec(canon):
    """The Rich settings menu advertises the preset with the same sample."""
    aliases, spec_badge, spec_preview = SPEC_PRESETS[canon]
    for mode in (canon, *aliases):
        name, badge, preview, color = t2.get_preset_presentation(mode)
        assert preview == f'"{spec_preview}"', f"settings menu preview drifted for {mode}"
        assert badge == spec_badge, f"settings menu badge drifted for {mode}"
        assert name, "the settings menu lost the preset name"
        assert color, "the settings menu lost the preset colour"


def test_ratatui_settings_modal_shows_the_spec():
    """The Rust row lives in another language, so pin its literals by source.

    Only the sample text is matched here (the Rust source escapes its quotes);
    that the row renders it wrapped in quotes is asserted on the Rust side by
    ``settings_picker::tests::test_preset_previews_match_the_spec``. The check is
    deliberately scoped to the non-test half of the file: the Rust test repeats
    these literals, and matching those would let the real implementation drift.
    """
    source = RUST_SETTINGS_ROW.read_text(encoding="utf-8")
    implementation = source.split("\n#[cfg(test)]", 1)[0]
    assert "SettingKind::PunctuationMode" in implementation, (
        "could not find the preset row in tui-rs/src/settings_picker.rs - the "
        "parity guard is looking in the wrong place"
    )
    for canon, (_, _, preview) in SPEC_PRESETS.items():
        assert preview in implementation, (
            f"tui-rs/src/settings_picker.rs no longer shows the spec preview for "
            f"'{canon}' ({preview!r})"
        )


def test_the_spec_document_lists_every_preview():
    """Keep docs/mode_presets.md itself from drifting."""
    doc = (REPO_ROOT / "docs" / "mode_presets.md").read_text(encoding="utf-8")
    for canon, (aliases, badge, preview) in SPEC_PRESETS.items():
        assert f"`{canon}`" in doc, f"{canon} is missing from the spec document"
        assert f"`{preview}`" in doc, f"the preview for {canon} is missing from the spec"
        assert f"`{badge}`" in doc, f"the badge for {canon} is missing from the spec"
        for alias in aliases:
            assert re.search(rf"`{re.escape(alias)}`", doc), f"alias {alias} is not documented"
