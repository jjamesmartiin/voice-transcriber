#!/usr/bin/env python3
"""
Unit tests for configuration loading, saving, and TUI status bar synchronization.
"""

import os
import sys
import tempfile
import json
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import t2
import transcribe2


def test_save_audio_config_preserves_yaml_and_extra_keys(tmp_path, monkeypatch):
    # Setup temporary yaml config file
    yaml_config = tmp_path / "config.yaml"
    initial_data = {
        "hf_token": "hf_test_secret_token_12345",
        "model_backend": "cohere",
        "is_muted": True,
        "auto_type": False,
        "ui_theme": "auto",
        "keep_bluetooth_handsfree": True,
    }
    
    import yaml
    yaml_config.write_text(yaml.dump(initial_data))

    # Point get_config_file() at our temp file so load_audio_config() doesn't
    # reset CONFIG_FILE back to the real live config (and clobber it)
    monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)

    # Load config
    t2.load_audio_config()
    assert t2.IS_MUTED is True
    assert t2.AUTO_TYPE is False
    assert t2.MODEL_BACKEND == "cohere"
    assert t2.KEEP_BLUETOOTH_HANDSFREE is True

    # Modify settings
    t2.IS_MUTED = False
    t2.AUTO_TYPE = True
    t2.KEEP_BLUETOOTH_HANDSFREE = False

    # Save config
    t2.save_audio_config()

    # Verify YAML content preserved extra keys (hf_token, ui_theme) and saved new values
    saved_content = yaml.safe_load(yaml_config.read_text())
    assert saved_content["hf_token"] == "hf_test_secret_token_12345"
    assert saved_content["ui_theme"] == "auto"  # extra key preserved unchanged
    assert saved_content["is_muted"] is False
    assert saved_content["auto_type"] is True
    assert saved_content["model_backend"] == "cohere"  # preserved extra key (option removed)
    assert saved_content["keep_bluetooth_handsfree"] is False


def test_save_audio_config_json_format(tmp_path, monkeypatch):
    json_config = tmp_path / "audio_device_config.json"
    json_config.write_text(json.dumps({"custom_setting": 42}))

    monkeypatch.setattr(t2, 'get_config_file', lambda: json_config)

    t2.load_audio_config()
    t2.IS_MUTED = False
    t2.AUTO_TYPE = True
    t2.KEEP_BLUETOOTH_HANDSFREE = True
    t2.save_audio_config()

    saved_data = json.loads(json_config.read_text())
    assert saved_data["custom_setting"] == 42
    assert saved_data["is_muted"] is False
    assert saved_data["auto_type"] is True
    assert saved_data["keep_bluetooth_handsfree"] is True


def test_keep_bluetooth_handsfree_env_override(tmp_path, monkeypatch):
    yaml_config = tmp_path / "config.yaml"
    import yaml
    yaml_config.write_text(yaml.dump({"keep_bluetooth_handsfree": True}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)

    monkeypatch.setenv("VT_KEEP_BLUETOOTH_HANDSFREE", "0")
    t2.load_audio_config()
    assert t2.KEEP_BLUETOOTH_HANDSFREE is False

    monkeypatch.setenv("VT_KEEP_BLUETOOTH_HANDSFREE", "1")
    t2.load_audio_config()
    assert t2.KEEP_BLUETOOTH_HANDSFREE is True


def test_set_default_input_device_preserves_output_device(monkeypatch):
    import sounddevice as sd
    # Record starting output device
    initial_dev = sd.default.device
    out_dev = initial_dev[1] if isinstance(initial_dev, (list, tuple)) and len(initial_dev) > 1 else None

    # Set input device to index 3
    t2.set_default_input_device(3)
    curr = sd.default.device
    assert curr[0] == 3
    if out_dev is not None:
        assert curr[1] == out_dev

    # Reset input device to None
    t2.set_default_input_device(None)
    curr_reset = sd.default.device
    if out_dev is not None:
        assert curr_reset[1] == out_dev


def test_wireplumber_helpers(monkeypatch):
    import subprocess
    calls = []

    def mock_run(cmd, *args, **kwargs):
        calls.append(cmd)
        class MockResult:
            returncode = 0
            stdout = "Value: false\n"
        return MockResult()

    monkeypatch.setattr(subprocess, 'run', mock_run)
    monkeypatch.setattr('shutil.which', lambda tool: '/usr/bin/' + tool)

    # Test query
    val = t2.get_wireplumber_bt_autoswitch()
    assert val is False

    # Test setting policy (keep handsfree disables autoswitch)
    t2.apply_bluetooth_handsfree_policy(True)
    assert any("bluetooth.autoswitch-to-headset-profile" in c and "false" in c for c in calls)


def test_tui_status_bar_matches_settings():
    # TUI module was removed from production src/; skip gracefully when unavailable
    tui = pytest.importorskip("tui", reason="TUI module no longer shipped in src/")
    VoiceTranscriberTUI = tui.VoiceTranscriberTUI
    tui = VoiceTranscriberTUI()
    tui.set_active_device("Microphone USB")
    tui.set_config_state(
        backend="cohere",
        muted=False,
        auto_type=True,
        sound_theme="proximity",
        ui_theme="magenta"
    )

    assert tui.model_backend == "cohere"
    assert tui.is_muted is False
    assert tui.auto_type is True
    assert tui.ui_theme == "magenta"

    rendered = tui._render_status_bar()
    rendered_plain = rendered.plain

    assert "mic: Microphone USB" in rendered_plain
    assert "model: cohere" in rendered_plain
    assert "sound: on" in rendered_plain
    assert "auto-type" in rendered_plain


def test_toml_config_support(tmp_path, monkeypatch):
    import tomllib
    toml_file = tmp_path / "config.toml"
    toml_file.write_text("""
model_backend = "cohere"
is_muted = true
auto_type = false
punctuation_mode = "no_terminal_period"
number_digits = true

[dictionary]
deepseq = "Deepseek"
""")
    monkeypatch.setattr(t2, 'get_config_file', lambda: toml_file)

    t2.load_audio_config()
    assert t2.MODEL_BACKEND == "cohere"
    assert t2.PUNCTUATION_MODE == "no_terminal_period"
    assert t2.NUMBER_DIGITS is True

    # Test cycling punctuation mode
    new_mode = t2.cycle_punctuation_mode()
    assert new_mode == "no_punctuation"
    assert t2.PUNCTUATION_MODE == "no_punctuation"

    # Save to TOML
    t2.save_audio_config()

    # Re-read and check round-trip
    saved = tomllib.loads(toml_file.read_text())
    assert saved["punctuation_mode"] == "no_punctuation"
    assert saved["model_backend"] == "cohere"
    assert saved["dictionary"]["deepseq"] == "Deepseek"


def test_toml_dump_quotes_spaced_keys_and_roundtrips():
    """Regression: dictionary keys with spaces must be quoted, or the file is invalid TOML."""
    import tomllib

    cfg = {
        "model_backend": "cohere",
        "empty_value": None,
        "ratio": 0.5,
        "dictionary": {"deep seq": "Deepseek", "nixos": "NixOS", "x86 64": "x86_64"},
        "nested": {"a b": {"c d": "e"}},
    }
    dumped = t2._dump_toml(cfg)
    parsed = tomllib.loads(dumped)  # must not raise
    assert parsed["dictionary"] == cfg["dictionary"]
    assert parsed["nested"] == cfg["nested"]
    assert parsed["ratio"] == 0.5
    assert "empty_value" not in parsed


def test_toml_roundtrip_with_spaced_dictionary_key(tmp_path, monkeypatch):
    """Saving a config whose dictionary has spaced keys must stay loadable."""
    import tomllib

    toml_file = tmp_path / "config.toml"
    toml_file.write_text(
        'model_backend = "cohere"\n'
        'number_digits = false\n'
        'punctuation_mode = "full"\n'
        '\n'
        '[dictionary]\n'
        '"deep seq" = "Deepseek"\n'
        '"x86 64" = "x86_64"\n'
    )
    monkeypatch.setattr(t2, 'get_config_file', lambda: toml_file)
    t2.load_audio_config()
    t2.save_audio_config()  # previously produced invalid TOML

    parsed = tomllib.loads(toml_file.read_text())
    assert parsed["dictionary"]["deep seq"] == "Deepseek"
    assert parsed["dictionary"]["x86 64"] == "x86_64"
    assert parsed["punctuation_mode"] == "full"


def test_malformed_toml_is_not_silently_wiped(tmp_path, monkeypatch):
    """A bad TOML file must be left intact (and warned about), not reset to defaults."""
    toml_file = tmp_path / "config.toml"
    bad = 'model_backend = "cohere"\nthis is not toml =\n'
    toml_file.write_text(bad)
    monkeypatch.setattr(t2, 'get_config_file', lambda: toml_file)

    t2.load_audio_config()  # must not raise

    assert toml_file.read_text() == bad  # untouched


def test_punctuation_modes_in_post_processor():
    from post_processor import clean_speech_transcription, set_punctuation_mode

    sample = "Hello world, this is Voice Transcriber."
    
    # 1. Full mode
    set_punctuation_mode("full")
    assert clean_speech_transcription(sample, punctuation_mode="full") == "Hello world, this is voice transcriber."

    # 2. No terminal period (semi-formal)
    assert clean_speech_transcription(sample, punctuation_mode="no_terminal_period") == "Hello world, this is voice transcriber"
    assert clean_speech_transcription(sample, punctuation_mode="semi-formal") == "Hello world, this is voice transcriber"

    # 3. No punctuation
    assert clean_speech_transcription(sample, punctuation_mode="no_punctuation") == "Hello world this is voice transcriber"

    # 4. Lowercase no punctuation
    assert clean_speech_transcription(sample, punctuation_mode="lowercase_no_punctuation") == "hello world this is voice transcriber"


def test_output_mode_cycle():
    t2.set_output_mode("clipboard")
    assert t2.get_output_mode() == "clipboard"
    assert t2.AUTO_TYPE is False
    assert t2.COPY_TO_CLIPBOARD is True

    # 1. clipboard -> type (auto-switches punctuation to full)
    t2.set_punctuation_mode("no_punctuation")
    assert t2.cycle_output_mode() == "type"
    assert t2.AUTO_TYPE is True
    assert t2.COPY_TO_CLIPBOARD is False
    assert t2.PUNCTUATION_MODE == "full"

    # 2. type -> type_fast (remains in full)
    assert t2.cycle_output_mode() == "type_fast"
    assert t2.AUTO_TYPE is True
    assert t2.COPY_TO_CLIPBOARD is False
    assert t2.PUNCTUATION_MODE == "full"

    # 3. type_fast -> clipboard
    assert t2.cycle_output_mode() == "clipboard"
    assert t2.AUTO_TYPE is False
    assert t2.COPY_TO_CLIPBOARD is True


def test_output_mode_config_sync(tmp_path, monkeypatch):
    yaml_config = tmp_path / "config.yaml"
    import yaml

    # Legacy paste migrates to type_fast
    yaml_config.write_text(yaml.dump({"output_mode": "paste"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)

    t2.load_audio_config()
    assert t2.get_output_mode() == "type_fast"

    # Save with type_fast
    t2.set_output_mode("type_fast")
    t2.save_audio_config()

    saved = yaml.safe_load(yaml_config.read_text())
    assert saved["output_mode"] == "type_fast"

    # Test env override
    monkeypatch.setenv("VT_OUTPUT_MODE", "type")
    t2.load_audio_config()
    assert t2.get_output_mode() == "type"


def test_auto_type_trailing_space_config_and_toggle(tmp_path, monkeypatch):
    import yaml
    yaml_config = tmp_path / "config.yaml"
    yaml_config.write_text(yaml.dump({"auto_type_trailing_space": False}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)

    t2.load_audio_config()
    assert t2.get_auto_type_trailing_space() is False

    # Toggle to True
    t2.toggle_auto_type_trailing_space()
    assert t2.get_auto_type_trailing_space() is True
    t2.save_audio_config()

    saved = yaml.safe_load(yaml_config.read_text())
    assert saved["auto_type_trailing_space"] is True

    # Test env override
    monkeypatch.setenv("VT_AUTO_TYPE_TRAILING_SPACE", "0")
    t2.load_audio_config()
    assert t2.get_auto_type_trailing_space() is False


def test_auto_type_auto_punctuate_config_and_toggle(tmp_path, monkeypatch):
    import yaml
    yaml_config = tmp_path / "config.yaml"
    yaml_config.write_text(yaml.dump({"auto_type_auto_punctuate": False}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)

    t2.load_audio_config()
    assert t2.get_auto_type_auto_punctuate() is False

    # When auto_punctuate is False, cycling output mode must NOT force full punctuation
    t2.set_output_mode("clipboard")
    t2.set_punctuation_mode("no_punctuation")
    t2.cycle_output_mode()  # cycles to "type"
    assert t2.PUNCTUATION_MODE == "no_punctuation"

    # Toggle back to True
    t2.toggle_auto_type_auto_punctuate()
    assert t2.get_auto_type_auto_punctuate() is True
    t2.save_audio_config()

    saved = yaml.safe_load(yaml_config.read_text())
    assert saved["auto_type_auto_punctuate"] is True

    # When auto_punctuate is True, cycling output mode sets punctuation to full
    t2.set_output_mode("clipboard")
    t2.set_punctuation_mode("no_punctuation")
    t2.cycle_output_mode()
    assert t2.PUNCTUATION_MODE == "full"


def test_main_typing_formatting_options(monkeypatch):
    """Test that disabling trailing space and auto-punctuate works in main._do_process_recording."""
    import main
    from main import SimpleVoiceTranscriber
    from unittest.mock import MagicMock
    import numpy as np

    monkeypatch.setattr(t2, 'OUTPUT_MODE', 'type_fast')
    monkeypatch.setattr(t2, 'AUTO_TYPE_TRAILING_SPACE', False)
    monkeypatch.setattr(t2, 'AUTO_TYPE_AUTO_PUNCTUATE', False)

    app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
    app.recording = False
    app.copy_to_clipboard = False
    app._model_ready_event = MagicMock()
    app._model_ready_event.is_set.return_value = True
    app.model_load_error = None
    app.tui = MagicMock()
    app.visual_notification = MagicMock()
    app.clipboard_sink = MagicMock()
    app.start_time = 0.0
    app.release_time = 1.0
    app.last_finish_time = 0.0
    app.audio_frames = np.ones(16000, dtype=np.float32) * 0.05
    app.last_transcription = ""

    typed = []
    mock_hotkey = MagicMock()
    mock_hotkey.are_modifiers_pressed.return_value = False
    mock_hotkey.type_text.side_effect = lambda text, fast=False: typed.append(text) or True
    app.hotkey_system = mock_hotkey

    monkeypatch.setattr('main.process_audio_stream', lambda frames: ("hello world", 0.1))
    monkeypatch.setattr('main.copy_to_clipboard_crossplatform', lambda text, sink=None: True)

    app.process_recording()

    # Should NOT have trailing period and should NOT have trailing space
    assert len(typed) == 1
    assert typed[0] == "hello world"

    # Now enable trailing space and auto punctuate
    monkeypatch.setattr(t2, 'AUTO_TYPE_TRAILING_SPACE', True)
    monkeypatch.setattr(t2, 'AUTO_TYPE_AUTO_PUNCTUATE', True)
    app.audio_frames = np.ones(16000, dtype=np.float32) * 0.05
    typed.clear()

    app.process_recording()

    assert len(typed) == 1
    assert typed[0] == "hello world. "



