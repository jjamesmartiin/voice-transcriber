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
from tui import VoiceTranscriberTUI


def test_save_audio_config_preserves_yaml_and_extra_keys(tmp_path):
    # Setup temporary yaml config file
    yaml_config = tmp_path / "config.yaml"
    initial_data = {
        "hf_token": "hf_test_secret_token_12345",
        "model_backend": "cohere",
        "is_muted": True,
        "auto_type": False,
        "ui_theme": "auto"
    }
    
    import yaml
    yaml_config.write_text(yaml.dump(initial_data))

    # Temporarily set CONFIG_FILE to our temp file
    old_config_file = t2.CONFIG_FILE
    t2.CONFIG_FILE = yaml_config
    try:
        # Load config
        t2.load_audio_config()
        assert t2.IS_MUTED is True
        assert t2.AUTO_TYPE is False
        assert t2.MODEL_BACKEND == "cohere"

        # Modify settings
        t2.IS_MUTED = False
        t2.AUTO_TYPE = True
        t2.MODEL_BACKEND = "whisper"
        t2.UI_THEME = "cyan"

        # Save config
        t2.save_audio_config()

        # Verify YAML content preserved extra keys and saved new values
        saved_content = yaml.safe_load(yaml_config.read_text())
        assert saved_content["hf_token"] == "hf_test_secret_token_12345"
        assert saved_content["is_muted"] is False
        assert saved_content["auto_type"] is True
        assert saved_content["model_backend"] == "whisper"
        assert saved_content["ui_theme"] == "cyan"
    finally:
        t2.CONFIG_FILE = old_config_file


def test_save_audio_config_json_format(tmp_path):
    json_config = tmp_path / "audio_device_config.json"
    json_config.write_text(json.dumps({"custom_setting": 42}))

    old_config_file = t2.CONFIG_FILE
    t2.CONFIG_FILE = json_config
    try:
        t2.load_audio_config()
        t2.IS_MUTED = False
        t2.AUTO_TYPE = True
        t2.save_audio_config()

        saved_data = json.loads(json_config.read_text())
        assert saved_data["custom_setting"] == 42
        assert saved_data["is_muted"] is False
        assert saved_data["auto_type"] is True
    finally:
        t2.CONFIG_FILE = old_config_file


def test_tui_status_bar_matches_settings():
    tui = VoiceTranscriberTUI()
    tui.set_active_device("Microphone USB")
    tui.set_config_state(
        backend="whisper",
        muted=False,
        auto_type=True,
        sound_theme="proximity",
        ui_theme="magenta"
    )

    assert tui.model_backend == "whisper"
    assert tui.is_muted is False
    assert tui.auto_type is True
    assert tui.ui_theme == "magenta"

    rendered = tui._render_status_bar()
    rendered_plain = rendered.plain

    assert "mic: Microphone USB" in rendered_plain
    assert "model: whisper" in rendered_plain
    assert "sound: on" in rendered_plain
    assert "auto-type" in rendered_plain
