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
import keybinds  # noqa: E402

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10, which the app still supports
    tomllib = None


# --- example-config parity ---------------------------------------------------
#
# The examples are documentation, so the only way to keep them honest is to
# derive the key set from the code (DEFAULT_SETTINGS, the model-choice lists)
# and check **both** directions: every shipped setting is documented, and every
# documented key is a real setting. A hand-written map cannot fail for a key it
# never lists - which is exactly how eight new settings went uncovered.
#
# The YAML example is the fully annotated reference and is asserted to carry the
# shipped default for every key. The TOML example is a real, supported format
# (`get_config_file` searches `config.toml` first) but a minimal template with
# sample values, so it is asserted to document every key rather than to carry the
# defaults.

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "config/example-config"

#: Both examples are checked. The TOML one needs ``tomllib`` (3.11+), the same
#: gate the app's own TOML loader has, so it is skipped on 3.10.
EXAMPLE_NAMES = ["config.yaml.example"] + (
    ["config.toml.example"] if tomllib is not None else []
)

#: Settings whose config-file key is not just the lowercased name.
_EXAMPLE_RENAMES = {
    "PUNCTUATION_MODE": "preset",
    "NUMBER_MODE": "number_digits",
    "MODEL_BACKEND": "model_backend",
    "HOTKEY_BINDS": "hotkeys",
}

#: A setting may be documented under any of these spellings.
_EXAMPLE_ALIASES = {
    "PUNCTUATION_MODE": ("preset", "punctuation_mode"),
}

#: Keys an example may carry that are not DEFAULT_SETTINGS entries: optional
#: device hints, and the dictionary, which has its own loader.
_EXAMPLE_EXTRAS = {
    "primary_device_name",
    "secondary_device_name",
    "dictionary",
    "dictionary_file",
}

#: Derived runtime mirrors, deliberately not file keys.
_DERIVED_SETTINGS = {"NUMBER_DIGITS"}


def _load_example(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if ".toml" in path.suffixes:
        assert tomllib is not None
        return tomllib.loads(text)
    import yaml

    return yaml.safe_load(text) or {}


def _expected_example_keys() -> dict:
    """Every setting -> the config-file key(s) that may document it."""
    expected = {}
    for setting in t2.DEFAULT_SETTINGS:
        if setting in _DERIVED_SETTINGS:
            continue
        expected[setting] = _EXAMPLE_ALIASES.get(
            setting, (_EXAMPLE_RENAMES.get(setting, setting.lower()),)
        )
    return expected


def _normalize_example_value(setting: str, value):
    if setting == "PUNCTUATION_MODE":
        return t2.get_canonical_preset_name(value)
    if setting == "NUMBER_MODE" and isinstance(value, bool):
        return "digits" if value else "words"
    if setting == "HOTKEY_BINDS":
        return keybinds.parse_binds(value)
    return value


def _assert_example_covers_every_setting(documented: dict, name: str) -> None:
    expected = _expected_example_keys()
    accepted_all = {key for keys in expected.values() for key in keys}

    for setting, accepted in expected.items():
        present = [key for key in accepted if key in documented]
        assert present, f"{name} no longer documents {accepted[0]!r} ({setting})"
        if name.endswith(".yaml.example"):
            key = present[0]
            got = _normalize_example_value(setting, documented[key])
            wanted = t2.DEFAULT_SETTINGS[setting]
            assert got == wanted, (
                f"{name} documents {key}={documented[key]!r} but the shipped "
                f"default for {setting} is {wanted!r}"
            )

    unknown = sorted(set(documented) - accepted_all - _EXAMPLE_EXTRAS)
    assert unknown == [], (
        f"{name} documents keys that are not shipped settings: {unknown}"
    )


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

    def test_reset_takes_effect_immediately(self, cfg, monkeypatch):
        """A reset must reach the runtime, not only the config file (A6).

        ``t2`` delegates to ``post_processor``, which keeps its own copies of
        the punctuation/cleanup/number/serial/spell/formatter state. Assigning
        the ``t2`` globals alone left the running app on the old preset until
        the next launch even though the file already said otherwise.
        """
        import post_processor as pp

        # Move every runtime copy away from the defaults...
        t2.set_punctuation_mode("gen_z")
        t2.set_cleanup_mode("off")
        t2.set_number_digits("words")
        t2.set_serial_collapse(False)
        t2.set_spell_command(False)

        t2.reset_to_defaults()

        # ...and confirm the reset pushed the shipped values straight through.
        assert pp.get_punctuation_mode() == t2.DEFAULT_SETTINGS["PUNCTUATION_MODE"]
        assert pp.get_cleanup_mode() == t2.DEFAULT_SETTINGS["CLEANUP_MODE"]
        assert pp.get_number_digits_mode() == t2.DEFAULT_SETTINGS["NUMBER_MODE"]
        assert pp.get_serial_collapse() is t2.DEFAULT_SETTINGS["SERIAL_COLLAPSE"]
        assert pp.get_spell_command() is t2.DEFAULT_SETTINGS["SPELL_COMMAND"]

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
            "DIARIZATION",
            "DIARIZATION_SPEAKERS",
            "DIARIZATION_MODEL",
        ):
            assert name in t2.DEFAULT_SETTINGS

    def test_shipped_defaults_are_the_intended_baseline(self):
        """The reset target is a product decision, not an incidental value.

        A6 (2026-10-09) resolved the old deliberate divergence between
        ``DEFAULT_SETTINGS['PUNCTUATION_MODE']`` and the module global: the reset
        target is now the most compatible configuration that still gives the
        most useful *cheap* corrections. Formatter and meeting/diarization stay
        off (models are opt-in), the deterministic corrections stay on, and the
        punctuation preset is ``full`` - standard complete sentences produced by
        pure-Python post-processing, which costs nothing on a weak machine.
        """
        assert t2.DEFAULT_SETTINGS["OUTPUT_MODE"] == "type_fast"
        assert t2.DEFAULT_SETTINGS["IS_MUTED"] is True  # start muted
        assert t2.DEFAULT_SETTINGS["MIDDLE_CLICK_ENABLED"] is False  # opt-in
        assert t2.DEFAULT_SETTINGS["PUNCTUATION_MODE"] == "full"
        assert t2.DEFAULT_SETTINGS["NUMBER_MODE"] == "auto"
        assert t2.DEFAULT_SETTINGS["UI_THEME"] == "red"
        assert t2.DEFAULT_SETTINGS["AUTO_TYPE_AUTO_PUNCTUATE"] is False

        # Heavyweight, model-backed features stay off by default: a reset must
        # produce a configuration that runs on a weak computer.
        assert t2.DEFAULT_SETTINGS["FORMATTER"] == "off"
        assert t2.DEFAULT_SETTINGS["MEETING"] == "off"
        # Cheap deterministic corrections stay on.
        assert t2.DEFAULT_SETTINGS["CLEANUP_MODE"] == "full"
        assert t2.DEFAULT_SETTINGS["SERIAL_COLLAPSE"] is True
        assert t2.DEFAULT_SETTINGS["SPELL_COMMAND"] is True

        # Derived mirrors must agree with their source of truth.
        mode = t2.DEFAULT_SETTINGS["OUTPUT_MODE"]
        assert t2.DEFAULT_SETTINGS["AUTO_TYPE"] == (mode in ("type", "type_fast"))
        assert t2.DEFAULT_SETTINGS["COPY_TO_CLIPBOARD"] == (mode not in ("type", "type_fast"))
        assert t2.DEFAULT_SETTINGS["NUMBER_DIGITS"] == (t2.DEFAULT_SETTINGS["NUMBER_MODE"] != "words")

    def test_punctuation_default_has_one_owner(self):
        """The reset target and the first-run default are the same value.

        Before A6 they were two hardcoded literals (``no_punctuation`` vs
        ``full``) plus a third in ``load_audio_config``'s fallback. All three
        now read ``DEFAULT_PUNCTUATION_MODE``.
        """
        assert t2.PUNCTUATION_MODE == t2.DEFAULT_PUNCTUATION_MODE
        assert t2.DEFAULT_SETTINGS["PUNCTUATION_MODE"] == t2.DEFAULT_PUNCTUATION_MODE

    def test_example_config_documents_the_shipped_defaults(self):
        """Both example configs must document every shipped setting.

        The key set is derived from ``DEFAULT_SETTINGS`` (plus the model-choice
        lists), not a hand-written map, so adding a setting without documenting
        it fails here. It also checks that every documented key is a real
        setting. The YAML example must additionally carry the shipped default
        for every key; the TOML template only has to cover the keys.
        """
        for name in EXAMPLE_NAMES:
            _assert_example_covers_every_setting(
                _load_example(EXAMPLES_DIR / name), name
            )

    def test_the_example_choices_are_real(self):
        """Documented model/hotkey choices must be ones the code registers."""
        for name in EXAMPLE_NAMES:
            documented = _load_example(EXAMPLES_DIR / name)
            assert documented["formatter_model"] in t2.FORMATTER_MODELS
            assert documented["diarization_model"] in t2.DIARIZATION_MODELS
            assert documented["model_backend"] in t2.transcribe2.BACKENDS
            assert keybinds.parse_binds(documented["hotkeys"]) == \
                t2.DEFAULT_SETTINGS["HOTKEY_BINDS"]


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
