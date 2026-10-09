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
        "VT_TYPING_WPM",
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

    def test_typing_wpm_cycle_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        new = t2.cycle_typing_wpm()
        assert new == 50
        assert t2.TYPING_WPM == 50
        t2.save_audio_config()

        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        t2.load_audio_config(file_path=str(cfg))
        assert t2.TYPING_WPM == 50


def _fake_sd_with_mic_at(index):
    """A sounddevice stand-in whose only input device sits at ``index``.

    ``load_audio_config`` validates a saved ``input_device_index`` against the
    real device list and discards it when it does not exist, so a test that
    asserts a saved index survives a reload must not depend on the host's
    audio hardware (CI runners have none).
    """
    devices = [
        {"name": f"fake-output-{i}", "max_input_channels": 0} for i in range(index)
    ] + [{"name": "Yeti", "max_input_channels": 1}]

    class _FakeSD:
        default = MagicMock()

        def query_devices(self, idx=None, kind=None):
            if idx is None:
                return devices
            if not isinstance(idx, int) or not 0 <= idx < len(devices):
                raise ValueError(f"Invalid device index: {idx}")
            return devices[idx]

    return _FakeSD()


class TestResetToDefaults:
    """The settings modal's "Reset to Defaults" action."""

    def test_reset_restores_every_setting_and_persists(self, cfg, monkeypatch):
        monkeypatch.setattr(t2, "OUTPUT_MODE", "type_fast")
        monkeypatch.setattr(t2, "AUTO_TYPE", True)
        monkeypatch.setattr(t2, "UI_THEME", "magenta")
        monkeypatch.setattr(t2, "PUNCTUATION_MODE", "gen_z")
        monkeypatch.setattr(t2, "MIDDLE_CLICK_ENABLED", True)
        monkeypatch.setattr(t2, "NUMBER_MODE", "words")
        monkeypatch.setattr(t2, "SERIAL_COLLAPSE", False)
        monkeypatch.setattr(t2, "SPELL_COMMAND", False)
        monkeypatch.setattr(t2, "IS_MUTED", False)

        returned = t2.reset_to_defaults()

        assert returned == t2.DEFAULT_SETTINGS
        for name, expected in t2.DEFAULT_SETTINGS.items():
            assert getattr(t2, name) == expected, f"{name} not reset"
        assert t2.NUMBER_DIGITS is True  # mirror kept in sync

        # Survives a reload from the file it just wrote.
        monkeypatch.setattr(t2, "UI_THEME", "magenta")
        monkeypatch.setattr(t2, "PUNCTUATION_MODE", "gen_z")
        t2.load_audio_config(file_path=str(cfg))
        assert t2.UI_THEME == t2.DEFAULT_SETTINGS["UI_THEME"]
        assert t2.PUNCTUATION_MODE == t2.DEFAULT_SETTINGS["PUNCTUATION_MODE"]

    def test_reset_keeps_microphone_and_unknown_keys(self, cfg, monkeypatch):
        import json

        monkeypatch.setattr(t2, "sd", _fake_sd_with_mic_at(7))

        cfg.write_text(json.dumps({
            "input_device_index": 7,
            "primary_device_name": "Yeti",
            "legacy_thing": "keep me",
        }))
        t2.load_audio_config(file_path=str(cfg))
        assert t2.PRIMARY_DEVICE_NAME == "Yeti"

        t2.reset_to_defaults()

        assert t2.INPUT_DEVICE_INDEX == 7
        assert t2.PRIMARY_DEVICE_NAME == "Yeti"
        written = json.loads(cfg.read_text())
        assert written["primary_device_name"] == "Yeti"
        assert written["legacy_thing"] == "keep me"
        assert written["ui_theme"] == t2.DEFAULT_SETTINGS["UI_THEME"]

    def test_defaults_cover_every_toggle_the_modal_exposes(self):
        for name in (
            "OUTPUT_MODE",
            "AUTO_TYPE_TRAILING_SPACE",
            "NUMBER_MODE",
            "SERIAL_COLLAPSE",
            "SPELL_COMMAND",
            "MIDDLE_CLICK_ENABLED",
            "SOUND_THEME",
            "UI_THEME",
            "PUNCTUATION_MODE",
            "MEETING",
            "MEETING_SPILL_MINUTES",
        ):
            assert name in t2.DEFAULT_SETTINGS

    def test_shipped_defaults_are_the_intended_baseline(self):
        """The reset target is a product decision, not an incidental value."""
        assert t2.DEFAULT_SETTINGS["OUTPUT_MODE"] == "type_fast"
        assert t2.DEFAULT_SETTINGS["IS_MUTED"] is True  # start muted
        assert t2.DEFAULT_SETTINGS["MIDDLE_CLICK_ENABLED"] is False  # opt-in
        assert t2.DEFAULT_SETTINGS["PUNCTUATION_MODE"] == "no_punctuation"
        assert t2.DEFAULT_SETTINGS["NUMBER_MODE"] == "auto"
        assert t2.DEFAULT_SETTINGS["UI_THEME"] == "red"
        assert t2.DEFAULT_SETTINGS["AUTO_TYPE_AUTO_PUNCTUATE"] is False

        # Derived mirrors must agree with their source of truth.
        mode = t2.DEFAULT_SETTINGS["OUTPUT_MODE"]
        assert t2.DEFAULT_SETTINGS["AUTO_TYPE"] == (mode in ("type", "type_fast"))
        assert t2.DEFAULT_SETTINGS["COPY_TO_CLIPBOARD"] == (mode not in ("type", "type_fast"))
        assert t2.DEFAULT_SETTINGS["NUMBER_DIGITS"] == (t2.DEFAULT_SETTINGS["NUMBER_MODE"] != "words")

    def test_example_config_documents_the_shipped_defaults(self):
        """A copied config.yaml.example must behave exactly like the defaults."""
        import yaml

        example = Path(__file__).resolve().parents[2] / "config/example-config/config.yaml.example"
        documented = yaml.safe_load(example.read_text())

        key_map = {
            "is_muted": "IS_MUTED",
            "auto_type": "AUTO_TYPE",
            "output_mode": "OUTPUT_MODE",
            "auto_type_trailing_space": "AUTO_TYPE_TRAILING_SPACE",
            "auto_type_auto_punctuate": "AUTO_TYPE_AUTO_PUNCTUATE",
            "copy_to_clipboard": "COPY_TO_CLIPBOARD",
            "preset": "PUNCTUATION_MODE",
            "language": "LANGUAGE",
            "enable_slm": "ENABLE_SLM",
            "wait_for_model_on_startup": "WAIT_FOR_MODEL_ON_STARTUP",
            "number_digits": "NUMBER_MODE",
            "serial_collapse": "SERIAL_COLLAPSE",
            "spell_command": "SPELL_COMMAND",
            "keep_bluetooth_handsfree": "KEEP_BLUETOOTH_HANDSFREE",
            "middle_click_enabled": "MIDDLE_CLICK_ENABLED",
            "sound_theme": "SOUND_THEME",
            "ui_theme": "UI_THEME",
            "typing_wpm": "TYPING_WPM",
            "meeting": "MEETING",
            "meeting_spill_minutes": "MEETING_SPILL_MINUTES",
        }

        for example_key, default_key in key_map.items():
            assert example_key in documented, f"config.yaml.example lost '{example_key}'"
            value = documented[example_key]
            if default_key == "PUNCTUATION_MODE":
                value = t2.get_canonical_preset_name(value)
            elif default_key == "NUMBER_MODE" and isinstance(value, bool):
                value = "digits" if value else "words"
            assert value == t2.DEFAULT_SETTINGS[default_key], (
                f"config.yaml.example documents {example_key}={value!r} but the "
                f"shipped default is {t2.DEFAULT_SETTINGS[default_key]!r}"
            )


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

    def test_rescan_mics_callback_reports_and_syncs(self, monkeypatch):
        app = _make_app(monkeypatch)
        calls = []
        monkeypatch.setattr(
            t2,
            "rescan_audio_devices",
            lambda: calls.append("rescan") or {
                "ok": True,
                "count": 6,
                "devices": ["default"],
                "device": "default",
                "missing": [],
                "notice": "",
                "message": "6 input devices found",
            },
        )

        app._on_tui_rescan_mics()

        assert calls == ["rescan"]
        app._sync_tui_state.assert_called_once()
        app.tui.print_event.assert_called_once()
        app.tui.print_warning.assert_not_called()

    def test_rescan_mics_warns_when_a_mic_is_still_held(self, monkeypatch):
        """A device that is still absent must say who has it, not just fail."""
        app = _make_app(monkeypatch)
        monkeypatch.setattr(
            t2,
            "rescan_audio_devices",
            lambda: {
                "ok": True,
                "count": 5,
                "devices": ["default"],
                "device": "default",
                "missing": [{"name": "Blue Snowball", "holder": "GNOME Settings"}],
                "notice": "Blue Snowball is being used by GNOME Settings",
                "message": "5 input devices found · still missing: GNOME Settings",
            },
        )

        app._on_tui_rescan_mics()

        app.tui.print_warning.assert_called_once()
        assert "GNOME Settings" in app.tui.print_warning.call_args[0][1]
        app.tui.print_event.assert_not_called()

    def test_rescan_mics_blocked_while_recording(self, monkeypatch):
        """Pa_Terminate closes open streams, so this is idle-only by design."""
        app = _make_app(monkeypatch, recording=True)
        calls = []
        monkeypatch.setattr(t2, "rescan_audio_devices", lambda: calls.append("rescan"))

        app._on_tui_rescan_mics()

        assert calls == []
        app.tui.print_warning.assert_called_once()
        app._sync_tui_state.assert_not_called()

    def test_reset_defaults_callback_restores_and_notifies(self, monkeypatch):
        app = _make_app(monkeypatch)
        calls = []
        monkeypatch.setattr(
            t2, "reset_to_defaults", lambda: calls.append("reset") or dict(t2.DEFAULT_SETTINGS)
        )

        app._on_tui_reset_defaults()

        assert calls == ["reset"]
        app._sync_tui_state.assert_called_once()
        app.tui.print_event.assert_called_once()
        app.tui.print_warning.assert_not_called()

    def test_reset_defaults_blocked_while_recording(self, monkeypatch):
        app = _make_app(monkeypatch, recording=True)
        calls = []
        monkeypatch.setattr(t2, "reset_to_defaults", lambda: calls.append("reset"))

        app._on_tui_reset_defaults()

        assert calls == []
        app.tui.print_warning.assert_called_once()
        app._sync_tui_state.assert_not_called()

    def test_set_typing_wpm_callback(self, monkeypatch):
        app = _make_app(monkeypatch)
        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        monkeypatch.setattr(t2, "save_audio_config", lambda: None)

        app._on_tui_set_typing_wpm("65")

        assert t2.TYPING_WPM == 65
        app._sync_tui_state.assert_called_once()
        app.tui.print_event.assert_called_once()

    def test_cycle_typing_wpm_callback(self, monkeypatch):
        app = _make_app(monkeypatch)
        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        monkeypatch.setattr(t2, "save_audio_config", lambda: None)

        app._on_tui_cycle_typing_wpm()

        assert t2.TYPING_WPM == 50
        app._sync_tui_state.assert_called_once()
        app.tui.print_event.assert_called_once()

    def test_set_punctuation_mode_callback(self, monkeypatch):
        app = _make_app(monkeypatch)
        monkeypatch.setattr(t2, "PUNCTUATION_MODE", "full")
        monkeypatch.setattr(t2, "save_audio_config", lambda: None)

        app._on_tui_set_punctuation_mode("casual")

        assert t2.PUNCTUATION_MODE == "no_terminal_period"
        app._sync_tui_state.assert_called_once()
        app.tui.print_event.assert_called_once()
