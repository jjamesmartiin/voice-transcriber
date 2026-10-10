#!/usr/bin/env python3
"""The Python -> Rust `cfg` contract, and the Rust initial defaults.

Two things are hand-synced between the Python engine and `vt-tui` and neither
was fully guarded:

* the field list of the `cfg` wire message, which lives in four places
  (`tui_ratatui.py`, `ipc.rs`'s `Wire::Cfg`, `main.rs`, `app.rs`). Only
  ``speakers`` was cross-checked. A field the engine emits but the Rust wire
  does not declare is silently dropped by ``#[serde(default)]``: the setting
  shows up in ``status`` and never reaches the TUI.
* the Rust ``App::new`` initial values, which stand in for the Python shipped
  defaults until the first ``cfg`` arrives. The existing parity guards pin
  labels, not values, which is how a stale ``formatter_model`` default survived
  a guard once already (docs/TODO-parity.md §4C).

Both are checked at the source level. Codegen would be overkill at this size;
the single authoritative source for the wire fields is ``ipc.rs``.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

import t2

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
TUI_RATATUI = os.path.join(REPO_ROOT, 'src', 'voice_transcriber', 'tui_ratatui.py')
RUST_IPC = os.path.join(REPO_ROOT, 'tui-rs', 'src', 'ipc.rs')
RUST_APP = os.path.join(REPO_ROOT, 'tui-rs', 'src', 'app.rs')


def _python_cfg_keys():
    """Every ``cfg`` key ``RatatuiTui`` can put on the wire.

    Two shapes: the ``msg["..."]`` assignments inside ``set_config_state``, and
    the one-field ``_send({"t": "cfg", ...})`` literals used by the direct
    setters (``set_active_device``/``set_secondary_device``/``set_ui_theme``).
    """
    source = open(TUI_RATATUI, encoding='utf-8').read()
    # Scope the `msg[...]` scan to set_config_state: the VU builder uses the
    # same local name for its own, non-cfg payload.
    start = source.index('def set_config_state(')
    end = source.index('\n    def ', start + 1)
    body = source[start:end]
    keys = set(re.findall(r'msg\["([a-z_]+)"\]\s*=', body))
    keys |= set(re.findall(r'\{"t": "cfg",\s*"([a-z_]+)"', source))
    return keys


def _rust_cfg_fields():
    """The field names declared in ``ipc.rs``'s ``Wire::Cfg`` variant."""
    source = open(RUST_IPC, encoding='utf-8').read()
    start = source.index('#[serde(rename = "cfg")]')
    end = source.index('#[serde(rename = "tx")]', start)
    block = source[start:end]
    return set(re.findall(r'^        ([a-z_]+):', block, re.M))


def test_every_cfg_key_python_emits_is_declared_in_the_rust_wire():
    python_keys = _python_cfg_keys()
    rust_fields = _rust_cfg_fields()

    # Guard against a vacuous pass: if either regex matches nothing (a rename,
    # a reformat) the subset check below would succeed for the wrong reason.
    sentinels = {'formatter_model', 'meeting_output_format', 'speakers'}
    assert sentinels <= python_keys, (
        f"the Python cfg extractor found {sorted(python_keys)} - it is looking "
        "in the wrong place, so this guard proves nothing")
    assert sentinels <= rust_fields, (
        f"the Rust cfg extractor found {sorted(rust_fields)} - it is looking in "
        "the wrong place, so this guard proves nothing")

    missing = python_keys - rust_fields
    assert not missing, (
        f"tui_ratatui emits cfg keys {sorted(missing)} that "
        "tui-rs/src/ipc.rs::Wire::Cfg does not declare. serde's "
        "#[serde(default)] silently drops them, so the setting would show in "
        "`status` but never reach the ratatui frontend.")


# Rust `App::new` field -> the `t2` constant whose shipped value it stands in
# for. Only fields where the initial value is meant to mirror Python.
_RUST_DEFAULTS = {
    'model_backend': 'MODEL_BACKEND',
    'output_mode': 'OUTPUT_MODE',
    'auto_type': 'AUTO_TYPE',
    'is_muted': 'IS_MUTED',
    'trailing_space': 'AUTO_TYPE_TRAILING_SPACE',
    'auto_punctuate': 'AUTO_TYPE_AUTO_PUNCTUATE',
    'number_digits': 'NUMBER_DIGITS',
    'number_mode': 'NUMBER_MODE',
    'serial_collapse': 'SERIAL_COLLAPSE',
    'spell_command': 'SPELL_COMMAND',
    'middle_click_enabled': 'MIDDLE_CLICK_ENABLED',
    'typing_wpm': 'TYPING_WPM',
    'punctuation_mode': 'PUNCTUATION_MODE',
    'structure_mode': 'STRUCTURE_MODE',
    'formatter': 'FORMATTER',
    'formatter_model': 'FORMATTER_MODEL',
    'formatter_style': 'FORMATTER_STYLE',
    'formatter_context': 'FORMATTER_CONTEXT',
    'cleanup_mode': 'CLEANUP_MODE',
    'meeting_mode': 'MEETING',
    'meeting_spill_minutes': 'MEETING_SPILL_MINUTES',
    'diarization': 'DIARIZATION',
    'diarization_speakers': 'DIARIZATION_SPEAKERS',
    'diarization_model': 'DIARIZATION_MODEL',
    'meeting_output_dir': 'MEETING_OUTPUT_DIR',
    'meeting_output_format': 'MEETING_OUTPUT_FORMAT',
    'sound_theme': 'SOUND_THEME',
}


def _rust_app_defaults():
    """Parse the ``field: literal,`` pairs out of ``App::new``'s ``Self { .. }``."""
    source = open(RUST_APP, encoding='utf-8').read()
    start = source.index('pub fn new(')
    end = source.index('/// Resolve the effective colour', start)
    body = source[start:end]

    defaults = {}
    for name, raw in re.findall(r'^ {12}([a-z_]+): (.+),$', body, re.M):
        raw = raw.strip()
        if raw.endswith('.to_string()'):
            raw = raw[: -len('.to_string()')]
        if raw.startswith('"') and raw.endswith('"'):
            value = raw[1:-1]
        elif raw == 'true':
            value = True
        elif raw == 'false':
            value = False
        elif raw.isdigit():
            value = int(raw)
        else:
            continue
        defaults[name] = value
    return defaults


def test_rust_app_defaults_match_the_python_shipped_defaults():
    defaults = _rust_app_defaults()
    assert defaults, "parsed no defaults from App::new - the guard is looking in the wrong place"

    mismatches = []
    for field, const in _RUST_DEFAULTS.items():
        assert field in defaults, (
            f"App::new no longer initialises {field!r}; drop it from this guard "
            "if that is intentional")
        expected = getattr(t2, const)
        if defaults[field] != expected:
            mismatches.append(
                f"App::new {field} = {defaults[field]!r}, "
                f"t2.{const} = {expected!r}")

    assert not mismatches, (
        "Rust App defaults drifted from the Python shipped defaults:\n  "
        + "\n  ".join(mismatches)
        + "\n(a wrong initial value is what made `formatter: on` look inert "
        "before the first cfg arrived)")
