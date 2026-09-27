#!/usr/bin/env python3
"""Settings menu: every toggle round-trips to disk, and the modal opens/exits cleanly.

These are model-free and run on Linux, Windows, and WSL.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2  # noqa: E402


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point t2's config persistence at a throwaway JSON file."""
    path = tmp_path / "audio_device_config.json"
    monkeypatch.setattr(t2, "CONFIG_FILE", path)
    # Env overrides in load_audio_config would mask the file value; clear them.
    for var in (
        "VT_NUMBER_DIGITS",
        "VT_SERIAL_COLLAPSE",
        "VT_SPELL_COMMAND",
        "VT_MIDDLE_CLICK_ENABLED",
        "VT_PUNCTUATION_MODE",
        "VT_AUTO_TYPE_TRAILING_SPACE",
        "VT_AUTO_TYPE_AUTO_PUNCTUATE",
    ):
        monkeypatch.delenv(var, raising=False)
    return path


class TestSettingsPersistence:
    """Each settings-menu toggle must survive save -> reload."""

    def test_output_mode_cycle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "OUTPUT_MODE", "clipboard")
        new = t2.cycle_output_mode()
        assert new != "clipboard"
        t2.save_audio_config()

        monkeypatch.setattr(t2, "OUTPUT_MODE", "clipboard")  # clobber in-memory
        t2.load_audio_config(file_path=str(cfg))
        assert t2.OUTPUT_MODE == new

    def test_trailing_space_toggle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "AUTO_TYPE_TRAILING_SPACE", True)
        assert t2.toggle_auto_type_trailing_space() is False
        t2.save_audio_config()

        monkeypatch.setattr(t2, "AUTO_TYPE_TRAILING_SPACE", True)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.AUTO_TYPE_TRAILING_SPACE is False

    def test_auto_punctuate_toggle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "AUTO_TYPE_AUTO_PUNCTUATE", True)
        assert t2.toggle_auto_type_auto_punctuate() is False
        t2.save_audio_config()

        monkeypatch.setattr(t2, "AUTO_TYPE_AUTO_PUNCTUATE", True)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.AUTO_TYPE_AUTO_PUNCTUATE is False

    def test_number_digits_toggle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "NUMBER_DIGITS", True)
        t2.set_number_digits(False)
        t2.save_audio_config()

        monkeypatch.setattr(t2, "NUMBER_DIGITS", True)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.NUMBER_DIGITS is False

    def test_number_mode_cycles_and_persists(self, cfg, monkeypatch):
        monkeypatch.delenv("VT_NUMBER_DIGITS", raising=False)
        t2.set_number_digits("auto")
        assert t2.NUMBER_MODE == "auto"
        assert t2.NUMBER_DIGITS is True

        assert t2.cycle_number_mode() == "digits"
        assert t2.NUMBER_MODE == "digits"
        assert t2.NUMBER_DIGITS is True

        assert t2.cycle_number_mode() == "words"
        assert t2.NUMBER_MODE == "words"
        assert t2.NUMBER_DIGITS is False

        assert t2.cycle_number_mode() == "auto"
        assert t2.NUMBER_MODE == "auto"
        t2.save_audio_config()

        # Clobber in-memory state, then confirm the mode round-trips from disk.
        monkeypatch.setattr(t2, "NUMBER_MODE", "words")
        monkeypatch.setattr(t2, "NUMBER_DIGITS", False)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.NUMBER_MODE == "auto"
        assert t2.NUMBER_DIGITS is True

    def test_number_mode_backward_compatible_bool_config(self, cfg, monkeypatch):
        import json
        cfg.write_text(json.dumps({"number_digits": False}))
        monkeypatch.delenv("VT_NUMBER_DIGITS", raising=False)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.NUMBER_MODE == "words"
        assert t2.NUMBER_DIGITS is False

    def test_serial_collapse_toggle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "SERIAL_COLLAPSE", True)
        assert t2.toggle_serial_collapse() is False
        t2.save_audio_config()

        monkeypatch.setattr(t2, "SERIAL_COLLAPSE", True)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.SERIAL_COLLAPSE is False

    def test_spell_command_toggle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "SPELL_COMMAND", True)
        assert t2.toggle_spell_command() is False
        t2.save_audio_config()

        monkeypatch.setattr(t2, "SPELL_COMMAND", True)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.SPELL_COMMAND is False

    def test_serial_and_spell_env_overrides(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "SERIAL_COLLAPSE", True)
        monkeypatch.setattr(t2, "SPELL_COMMAND", True)
        monkeypatch.setenv("VT_SERIAL_COLLAPSE", "0")
        monkeypatch.setenv("VT_SPELL_COMMAND", "0")
        t2.load_audio_config(file_path=str(cfg))
        assert t2.SERIAL_COLLAPSE is False
        assert t2.SPELL_COMMAND is False

    def test_middle_click_toggle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "MIDDLE_CLICK_ENABLED", True)
        t2.set_middle_click_enabled(False)
        t2.save_audio_config()

        monkeypatch.setattr(t2, "MIDDLE_CLICK_ENABLED", True)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.MIDDLE_CLICK_ENABLED is False

    def test_punctuation_mode_cycle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "PUNCTUATION_MODE", "full")
        new = t2.cycle_punctuation_mode()
        t2.save_audio_config()

        monkeypatch.setattr(t2, "PUNCTUATION_MODE", "full")
        t2.load_audio_config(file_path=str(cfg))
        assert t2.PUNCTUATION_MODE == new

    def test_ui_theme_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "UI_THEME", "auto")
        monkeypatch.setattr(t2, "UI_THEME", "dracula")
        t2.save_audio_config()

        monkeypatch.setattr(t2, "UI_THEME", "auto")
        t2.load_audio_config(file_path=str(cfg))
        assert t2.UI_THEME == "dracula"


def _make_app(monkeypatch, recording=False):
    from main import SimpleVoiceTranscriber

    app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
    app.recording = recording
    app.tui = MagicMock()
    app.visual_notification = MagicMock()
    app._sync_tui_state = MagicMock()
    monkeypatch.setattr("main.get_active_device_name", lambda **kw: "Fake Mic")
    return app


class TestSettingsMenuLifecycle:
    def test_open_and_exit_cleanly(self, monkeypatch):
        app = _make_app(monkeypatch)
        reset_calls = []
        monkeypatch.setattr(t2, "select_settings_picker", lambda: True)
        monkeypatch.setattr(t2, "reset_terminal", lambda: reset_calls.append(True))

        app.open_settings_picker()

        app.tui._pause_live.assert_called_once()
        app.tui._resume_live.assert_called_once()
        app.tui.update_state.assert_called_with("READY")
        assert reset_calls == [True]

    def test_picker_error_still_exits_cleanly(self, monkeypatch):
        app = _make_app(monkeypatch)

        def _boom():
            raise RuntimeError("picker blew up")

        monkeypatch.setattr(t2, "select_settings_picker", _boom)
        monkeypatch.setattr(t2, "reset_terminal", lambda: None)

        app.open_settings_picker()  # must not raise

        app.tui.print_error.assert_called_once()
        app.tui._resume_live.assert_called_once()
        app.tui.update_state.assert_called_with("READY")

    def test_settings_locked_while_recording(self, monkeypatch):
        app = _make_app(monkeypatch, recording=True)
        opened = []
        monkeypatch.setattr(t2, "select_settings_picker", lambda: opened.append(True) or True)

        app.open_settings_picker()

        app.tui.print_warning.assert_called_once()
        assert opened == []  # picker never opened
