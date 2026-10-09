#!/usr/bin/env python3
"""
Unit tests for configuration loading, saving, and TUI status bar synchronization.
"""

import os
import subprocess
import sys
import json
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

import t2


def test_save_audio_config_preserves_yaml_and_extra_keys(tmp_path, monkeypatch):
    # Setup temporary yaml config file
    yaml_config = tmp_path / "config.yaml"
    initial_data = {
        "api_key": "test_api_key_12345",
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

    # Verify YAML content preserved extra keys (api_key, ui_theme) and saved new values
    saved_content = yaml.safe_load(yaml_config.read_text())
    assert saved_content["api_key"] == "test_api_key_12345"
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
    """The setter must rewrite only the input slot of the host default.

    ``t2.sd`` is faked rather than importing the real ``sounddevice``: the real
    setter mutates the process-global PortAudio default, which would disturb the
    developer's actual audio setup and violates the hermetic shared-tier
    contract. This pins the tuple arithmetic in ``t2.set_default_input_device``.
    """

    class _FakeDefault:
        device = [0, 7]

    class _FakeSoundDevice:
        default = _FakeDefault()

    fake = _FakeSoundDevice()
    monkeypatch.setattr(t2, "sd", fake)

    t2.set_default_input_device(3)
    assert list(fake.default.device) == [3, 7]

    t2.set_default_input_device(None)
    assert list(fake.default.device) == [None, 7]


def test_set_default_input_device_handles_scalar_default(monkeypatch):
    """A scalar/absent ``sd.default.device`` must not raise; input is set and
    the output slot degrades to ``None``."""

    class _FakeDefault:
        device = None

    class _FakeSoundDevice:
        default = _FakeDefault()

    fake = _FakeSoundDevice()
    monkeypatch.setattr(t2, "sd", fake)

    t2.set_default_input_device(3)
    assert list(fake.default.device) == [3, None]


class FakeWirePlumberSettings:
    """Stateful stand-in for `wpctl settings` — WirePlumber's *persisted* config.

    Persistence is the whole point: the app borrows this global setting to keep
    Bluetooth headsets in hands-free mode, so it must give it back on exit
    instead of leaving the user's system reconfigured.
    """

    SETTING = "bluetooth.autoswitch-to-headset-profile"

    def __init__(self, autoswitch: bool = True, readable: bool = True):
        self.autoswitch = autoswitch
        self.readable = readable
        self.calls: list[list[str]] = []

    def run(self, cmd, *args, **kwargs):
        self.calls.append(list(cmd))
        if len(cmd) >= 2 and cmd[1] == "settings" and self.SETTING in cmd:
            if "-s" in cmd:  # write: wpctl settings -s <name> <value>
                self.autoswitch = cmd[cmd.index(self.SETTING) + 1] == "true"
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if cmd[-1] == self.SETTING:  # read
                if not self.readable:
                    return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
                value = "true" if self.autoswitch else "false"
                return subprocess.CompletedProcess(cmd, 0, stdout=f"Value: {value}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    def writes(self):
        return [c for c in self.calls if "-s" in c]


@pytest.fixture
def wp_settings(monkeypatch):
    """Fake WirePlumber settings, with the exit hook captured instead of registered."""
    fake = FakeWirePlumberSettings()
    registered = []
    monkeypatch.setattr(subprocess, "run", fake.run)
    monkeypatch.setattr("shutil.which", lambda tool: "/usr/bin/" + tool)
    monkeypatch.setattr(t2, "_register_exit_hook", registered.append)
    monkeypatch.setattr(t2, "_bt_autoswitch_before", None)
    monkeypatch.setattr(t2, "_bt_restore_registered", False)
    fake.registered = registered
    return fake


def test_wireplumber_helpers(wp_settings):
    # Test query
    assert t2.get_wireplumber_bt_autoswitch() is True

    # Test setting policy (keep handsfree disables autoswitch)
    assert t2.apply_bluetooth_handsfree_policy(True) is True
    assert wp_settings.autoswitch is False
    assert any("bluetooth.autoswitch-to-headset-profile" in c and "false" in c for c in wp_settings.calls)

    # The setting is global and persisted, so it is restored when the app exits.
    assert len(wp_settings.registered) == 1
    wp_settings.registered[0]()
    assert wp_settings.autoswitch is True


def test_bt_policy_registers_the_exit_hook_only_once(wp_settings):
    t2.apply_bluetooth_handsfree_policy(True)
    t2.apply_bluetooth_handsfree_policy(True)
    assert len(wp_settings.registered) == 1


def test_bt_policy_does_not_write_when_already_correct(wp_settings):
    wp_settings.autoswitch = False
    t2.apply_bluetooth_handsfree_policy(True)
    assert wp_settings.writes() == []


def test_bt_policy_is_skipped_when_the_current_value_is_unknown(wp_settings):
    """Nothing to restore means the setting must be left alone, not changed blind."""
    wp_settings.readable = False
    assert t2.apply_bluetooth_handsfree_policy(True) is False
    assert wp_settings.autoswitch is True
    assert wp_settings.writes() == []


def test_bt_restore_is_a_noop_when_nothing_was_changed(wp_settings):
    assert t2.restore_bluetooth_autoswitch() is False
    assert wp_settings.calls == []


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

    # 3. No punctuation / autocorrect
    assert clean_speech_transcription(sample, punctuation_mode="no_punctuation") == "Hello world this is voice transcriber"
    assert clean_speech_transcription(sample, punctuation_mode="autocorrect") == "Hello world this is voice transcriber"

    # 4. Aesthetic lowercase
    assert clean_speech_transcription(sample, punctuation_mode="aesthetic_lowercase") == "hello world, this is voice transcriber"

    # 5. Pure Gen Z / Lowercase no punctuation
    assert clean_speech_transcription(sample, punctuation_mode="lowercase_no_punctuation") == "hello world this is voice transcriber"
    assert clean_speech_transcription(sample, punctuation_mode="gen_z") == "hello world this is voice transcriber"


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


def test_structure_mode_defaults_off_and_round_trips(tmp_path, monkeypatch):
    """The list-formatting setting persists, and "off" is the shipped default."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    t2.load_audio_config()
    assert t2.get_structure_mode() == "off"
    assert t2.get_effective_structure_mode() == "off"

    t2.set_structure_mode("blocks")
    assert t2.get_structure_mode() == "blocks"
    saved = yaml.safe_load(config.read_text())
    assert saved["structure_mode"] == "blocks"

    # A fresh load reads it back.
    t2.STRUCTURE_MODE = "off"
    t2.load_audio_config()
    assert t2.get_structure_mode() == "blocks"


def test_structure_mode_is_unknown_value_safe(tmp_path, monkeypatch):
    """An unrecognised value falls back to "off" rather than guessing."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"structure_mode": "wat"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    t2.load_audio_config()
    assert t2.get_structure_mode() == "off"


def test_typing_downgrades_blocks_to_inline(tmp_path, monkeypatch):
    """A newline is an Enter keypress, so typed output never gets real breaks.

    The configured mode stays "blocks" (it is what the user chose, and what the
    settings modal shows); the *effective* mode is what reaches the
    post-processor, and it is "inline" whenever the text is being typed.
    """
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    t2.load_audio_config()

    import post_processor
    try:
        t2.set_structure_mode("blocks")

        t2.set_output_mode("type_fast")
        assert t2.get_structure_mode() == "blocks"
        assert t2.get_effective_structure_mode() == "inline"
        assert post_processor.get_structure_mode() == "inline"

        t2.set_output_mode("clipboard")
        assert t2.get_effective_structure_mode() == "blocks"
        assert post_processor.get_structure_mode() == "blocks"

        # Switching to a typing mode through the cycle path downgrades too.
        t2.cycle_output_mode()  # clipboard -> type
        assert t2.get_effective_structure_mode() == "inline"
        assert post_processor.get_structure_mode() == "inline"
    finally:
        t2.set_structure_mode("off")
        t2.set_output_mode("clipboard")


def test_cleanup_mode_defaults_full_and_round_trips(tmp_path, monkeypatch):
    """The cleanup setting persists, and "full" is the shipped default."""
    import yaml

    import post_processor
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    t2.load_audio_config()
    assert t2.get_cleanup_mode() == "full"
    # Loading publishes it to the post-processor, so the default is in force too.
    assert post_processor.get_cleanup_mode() == "full"

    try:
        t2.set_cleanup_mode("artifacts")
        assert t2.get_cleanup_mode() == "artifacts"
        assert post_processor.get_cleanup_mode() == "artifacts"
        assert yaml.safe_load(config.read_text())["cleanup_mode"] == "artifacts"

        # A fresh load reads it back.
        t2.CLEANUP_MODE = "full"
        t2.load_audio_config()
        assert t2.get_cleanup_mode() == "artifacts"

        # Cycling walks off -> artifacts -> full -> off.
        assert t2.toggle_cleanup_mode() == "full"
        assert t2.toggle_cleanup_mode() == "off"
        assert post_processor.get_cleanup_mode() == "off"
    finally:
        t2.set_cleanup_mode("full")


def test_cleanup_mode_env_override_and_unknown_value(tmp_path, monkeypatch):
    import yaml

    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"cleanup_mode": "artifacts"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    monkeypatch.setenv("VT_CLEANUP_MODE", "off")
    t2.load_audio_config()
    assert t2.get_cleanup_mode() == "off"

    # An unrecognised value falls back to the default, not to "off": a typo must
    # not look like a deliberately disabled feature.
    monkeypatch.setenv("VT_CLEANUP_MODE", "nonsense")
    t2.load_audio_config()
    assert t2.get_cleanup_mode() == "full"
    t2.set_cleanup_mode("full")


def test_meeting_defaults_off_and_round_trips(tmp_path, monkeypatch):
    """The meeting toggle persists, and "off" is the shipped default."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_MEETING", raising=False)

    try:
        t2.load_audio_config()
        assert t2.get_meeting() == "off"

        t2.set_meeting("on")
        assert t2.get_meeting() == "on"
        assert yaml.safe_load(config.read_text())["meeting"] == "on"

        # A fresh load reads it back.
        t2.MEETING = "off"
        t2.load_audio_config()
        assert t2.get_meeting() == "on"
    finally:
        monkeypatch.delenv("VT_MEETING", raising=False)
        t2.set_meeting("off")


def test_meeting_is_unknown_value_safe(tmp_path, monkeypatch):
    """An unrecognised meeting value falls back to "off" (fail safe)."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"meeting": "wat"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_MEETING", raising=False)

    t2.load_audio_config()
    assert t2.get_meeting() == "off"


def test_meeting_env_override_wins(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"meeting": "off"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    try:
        monkeypatch.setenv("VT_MEETING", "on")
        t2.load_audio_config()
        assert t2.get_meeting() == "on"
    finally:
        monkeypatch.delenv("VT_MEETING", raising=False)
        t2.set_meeting("off")


def test_meeting_spill_default_round_trips_and_cycles(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_MEETING_SPILL_MINUTES", raising=False)

    try:
        t2.load_audio_config()
        assert t2.get_meeting_spill_minutes() == 10

        t2.set_meeting_spill_minutes(20)
        assert t2.get_meeting_spill_minutes() == 20
        assert yaml.safe_load(config.read_text())["meeting_spill_minutes"] == 20

        # An out-of-range or unparseable value keeps the shipped default rather
        # than disabling the memory cap.
        assert t2.set_meeting_spill_minutes(999) == 10
        assert t2.set_meeting_spill_minutes("ten") == 10

        t2.set_meeting_spill_minutes(5)
        assert t2.cycle_meeting_spill_minutes() == 10
        assert t2.cycle_meeting_spill_minutes() == 20
    finally:
        t2.set_meeting_spill_minutes(10)


def test_meeting_spill_env_override(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"meeting_spill_minutes": 10}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    try:
        monkeypatch.setenv("VT_MEETING_SPILL_MINUTES", "30")
        t2.load_audio_config()
        assert t2.get_meeting_spill_minutes() == 30
    finally:
        monkeypatch.delenv("VT_MEETING_SPILL_MINUTES", raising=False)
        t2.set_meeting_spill_minutes(10)


def test_meeting_setting_labels_are_stable():
    assert t2.meeting_setting_state("on") == (
        "Long, non-injecting capture", "[ON]", "green"
    )
    assert t2.meeting_setting_state("off") == (
        "Off: dictation only", "[OFF]", "dim white"
    )
    assert t2.meeting_setting_state("nonsense") == (
        "Off: dictation only", "[OFF]", "dim white"
    )
    assert t2.meeting_spill_setting_state(10) == (
        "Spills to disk past 10 min", "[SPILL]", "cyan"
    )


def test_meeting_elapsed_label():
    assert t2.meeting_elapsed_label(0) == "00:00"
    assert t2.meeting_elapsed_label(65) == "01:05"
    assert t2.meeting_elapsed_label(3600) == "1:00:00"
    assert t2.meeting_elapsed_label(3661) == "1:01:01"
    assert t2.meeting_elapsed_label("bogus") == "00:00"


def test_diarization_defaults_off_and_round_trips(tmp_path, monkeypatch):
    """The speaker-label toggle persists, and "off" is the shipped default."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_DIARIZATION", raising=False)

    try:
        t2.load_audio_config()
        assert t2.get_diarization() == "off"

        t2.set_diarization("on")
        assert t2.get_diarization() == "on"
        assert yaml.safe_load(config.read_text())["diarization"] == "on"

        t2.DIARIZATION = "off"
        t2.load_audio_config()
        assert t2.get_diarization() == "on"
    finally:
        monkeypatch.delenv("VT_DIARIZATION", raising=False)
        t2.set_diarization("off")


def test_diarization_is_unknown_value_safe(tmp_path, monkeypatch):
    """An unrecognised value falls back to "off": the pass loads a model."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"diarization": "maybe"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_DIARIZATION", raising=False)

    t2.load_audio_config()
    assert t2.get_diarization() == "off"


def test_diarization_env_override_wins(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"diarization": "off"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    try:
        monkeypatch.setenv("VT_DIARIZATION", "on")
        t2.load_audio_config()
        assert t2.get_diarization() == "on"
    finally:
        monkeypatch.delenv("VT_DIARIZATION", raising=False)
        t2.set_diarization("off")


def test_diarization_speakers_round_trips_and_cycles(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_DIARIZATION_SPEAKERS", raising=False)

    try:
        t2.load_audio_config()
        assert t2.get_diarization_speakers() == "auto"

        t2.set_diarization_speakers(3)
        assert t2.get_diarization_speakers() == 3
        assert yaml.safe_load(config.read_text())["diarization_speakers"] == 3

        # An out-of-range or unparseable hint keeps auto-detection rather than
        # guessing a count: a wrong hint is worse than none.
        assert t2.set_diarization_speakers(1) == "auto"
        assert t2.set_diarization_speakers(99) == "auto"
        assert t2.set_diarization_speakers("lots") == "auto"

        t2.set_diarization_speakers("auto")
        assert t2.cycle_diarization_speakers() == 2
        assert t2.cycle_diarization_speakers() == 3
        for _ in range(6):
            t2.cycle_diarization_speakers()
        assert t2.cycle_diarization_speakers() == 2  # 8 -> auto -> 2
    finally:
        t2.set_diarization_speakers("auto")


def test_diarization_speakers_env_override(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({"diarization_speakers": "auto"}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    try:
        monkeypatch.setenv("VT_DIARIZATION_SPEAKERS", "4")
        t2.load_audio_config()
        assert t2.get_diarization_speakers() == 4
    finally:
        monkeypatch.delenv("VT_DIARIZATION_SPEAKERS", raising=False)
        t2.set_diarization_speakers("auto")


def test_diarization_model_default_and_fallback(tmp_path, monkeypatch):
    """The model is a registry name; an unknown one warns and keeps the default."""
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)
    monkeypatch.delenv("VT_DIARIZATION_MODEL", raising=False)

    try:
        t2.load_audio_config()
        assert t2.get_diarization_model() == "diarization"
        assert t2.DIARIZATION_MODEL in t2.DIARIZATION_MODELS

        # Unknown -> the shipped default, never an empty/disabled value.
        assert t2.set_diarization_model("nope") == "diarization"
        assert t2.set_diarization_model("") == "diarization"
        assert t2.set_diarization_model(None) == "diarization"
    finally:
        t2.set_diarization_model(t2.DIARIZATION_DEFAULT_MODEL)


def test_diarization_model_env_override(tmp_path, monkeypatch):
    import yaml
    config = tmp_path / "config.yaml"
    config.write_text(yaml.dump({}))
    monkeypatch.setattr(t2, 'get_config_file', lambda: config)

    try:
        monkeypatch.setenv("VT_DIARIZATION_MODEL", "diarization")
        t2.load_audio_config()
        assert t2.get_diarization_model() == "diarization"
    finally:
        monkeypatch.delenv("VT_DIARIZATION_MODEL", raising=False)
        t2.set_diarization_model(t2.DIARIZATION_DEFAULT_MODEL)


def test_diarization_setting_labels_are_stable():
    assert t2.diarization_setting_state("on") == (
        "Labels who said what in meeting transcripts", "[ON]", "green"
    )
    assert t2.diarization_setting_state("off") == (
        "Off: one speaker per transcript", "[OFF]", "dim white"
    )
    assert t2.diarization_setting_state("nonsense") == (
        "Off: one speaker per transcript", "[OFF]", "dim white"
    )
    assert t2.diarization_speakers_setting_state("auto") == (
        "Auto-detect the speaker count", "[AUTO]", "cyan"
    )
    assert t2.diarization_speakers_setting_state(3) == (
        "Expecting 3 speakers", "[3]", "cyan"
    )
    assert t2.diarization_model_setting_state("diarization") == (
        "Model: diarization", "[MODEL]", "cyan"
    )


def test_main_typing_formatting_options(monkeypatch):
    """Test that disabling trailing space and auto-punctuate works in main._do_process_recording."""
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





# ---------------------------------------------------------------------------
# Structure-mode labels: the two frontends must advertise the same thing
# ---------------------------------------------------------------------------

RUST_SETTINGS_PICKER = (
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    + "/tui-rs/src/settings_picker.rs"
)


@pytest.mark.parametrize("configured,effective,desc,badge,colour", [
    # Configuring "blocks" while typing is the interesting row: the engine
    # downgrades it to inline, so the row must not promise bullets on their own
    # lines. Python used to say exactly that; Rust already said this.
    ("blocks", "inline", "Typed text stays inline", "[PASTE]", "yellow"),
    ("blocks", "blocks", "Bullets on their own lines", "[BLOCKS]", "green"),
    ("inline", "inline", "Bullets on one line: - one. - two.", "[INLINE]", "cyan"),
    ("off", "off", "Flat prose (no list formatting)", "[OFF]", "dim white"),
])
def test_structure_setting_labels(configured, effective, desc, badge, colour):
    assert t2.structure_setting_state(configured, effective) == (desc, badge, colour)


def test_no_structure_label_advertises_a_formatting_not_in_force():
    """The regression this file exists to prevent.

    "Bullets on their own lines" is only true when the breaks will actually be
    emitted; under typing the configured blocks mode is downgraded to inline.
    """
    desc, badge, _ = t2.structure_setting_state("blocks", "inline")
    assert "own lines" not in desc
    assert badge == "[PASTE]"


def test_ratatui_structure_labels_match_python():
    """The ratatui modal renders these strings from another language.

    Pin its literals by source, scoped to the non-test half of the file: the
    Rust test repeats these strings, and matching those would let the real
    implementation drift. Mirrors the guard in tests/shared/test_mode_presets.py.
    """
    source = open(RUST_SETTINGS_PICKER, encoding="utf-8").read()
    implementation = source.split("\n#[cfg(test)]", 1)[0]
    assert "SettingKind::StructureMode" in implementation, (
        "could not find the structure row in tui-rs/src/settings_picker.rs - the "
        "parity guard is looking in the wrong place"
    )
    for configured, effective in [
        ("off", "off"), ("inline", "inline"),
        ("blocks", "blocks"), ("blocks", "inline"),
    ]:
        desc, badge, _ = t2.structure_setting_state(configured, effective)
        assert desc in implementation, (
            f"tui-rs/src/settings_picker.rs no longer shows {desc!r} "
            f"(structure_mode={configured}, effective={effective})"
        )
        assert badge in implementation, (
            f"tui-rs/src/settings_picker.rs no longer shows the {badge} badge"
        )


def test_ratatui_meeting_labels_match_python():
    """The ratatui modal renders the meeting rows from another language.

    Same source-scoped guard as the structure labels above: the Rust test
    repeats these strings, so only the non-test half of the file is searched.
    """
    source = open(RUST_SETTINGS_PICKER, encoding="utf-8").read()
    implementation = source.split("\n#[cfg(test)]", 1)[0]
    assert "SettingKind::MeetingMode" in implementation, (
        "could not find the meeting row in tui-rs/src/settings_picker.rs - the "
        "parity guard is looking in the wrong place"
    )
    assert "SettingKind::MeetingSpill" in implementation
    for mode in ("off", "on"):
        desc, badge, _ = t2.meeting_setting_state(mode)
        assert desc in implementation, (
            f"tui-rs/src/settings_picker.rs no longer shows {desc!r} "
            f"(meeting={mode})"
        )
        assert badge in implementation, (
            f"tui-rs/src/settings_picker.rs no longer shows the {badge} badge"
        )
    # The spill row's description is formatted at render time; pin the format
    # string and the static badge.
    assert "Spills to disk past {} min" in implementation
    assert t2.meeting_spill_setting_state(10)[1] in implementation


def test_the_naming_decision_is_recorded():
    """A2: `off`/`inline`/`blocks` are canonical; the others are aliases.

    Kept as a test rather than a comment because the alternatives are tempting
    and the reasons are non-obvious:

    * `full` is already the canonical *cleanup* mode, and the same modal shows
      it as a "[FULL]" badge. Reusing it for structure would put two identical
      badges on two different axes in one list.
    * `bullets` describes how the output looks, but not the thing that makes
      this setting dangerous - `inline` never emits a newline and `blocks` does,
      and a newline is an Enter keypress in whatever window has focus. The
      canonical names encode the transport, which is the safety-relevant axis.

    Both alternatives are accepted as *input*, so nobody who thinks in terms of
    "bullets" is locked out.
    """
    from post_processor import normalize_structure_mode

    assert t2.STRUCTURE_MODES == ["off", "inline", "blocks"]
    assert normalize_structure_mode("bullets") == "blocks"
    assert normalize_structure_mode("full") == "blocks"
    assert normalize_structure_mode("lists") == "blocks"
    assert normalize_structure_mode("dashes") == "inline"
    assert normalize_structure_mode("single-line") == "inline"
    assert normalize_structure_mode("nonsense") == "off"


def test_both_python_tuis_accept_diarization_settings():
    """D6: every setting lands in both Python frontends, not just the modal."""
    from tui import VoiceTranscriberTUI
    from tui_ratatui import RatatuiTui

    tui = VoiceTranscriberTUI(ui_theme="cyan")
    tui.set_config_state(
        diarization="on", diarization_speakers=3, diarization_model="diarization"
    )
    assert tui.diarization == "on"
    assert tui.diarization_speakers == 3
    assert tui.diarization_model == "diarization"

    rat = RatatuiTui.__new__(RatatuiTui)
    sent = []
    rat._send = lambda msg: sent.append(msg)
    rat.set_config_state(
        diarization="on", diarization_speakers=3, diarization_model="diarization"
    )
    assert sent and sent[-1]["t"] == "cfg"
    assert sent[-1]["diarization"] == "on"
    assert sent[-1]["diarization_speakers"] == 3
    assert sent[-1]["diarization_model"] == "diarization"


def test_ratatui_cycle_commands_reach_the_engine():
    """D6: the ratatui settings modal's cycle commands are wired to callbacks."""
    from tui_ratatui import RatatuiTui

    rat = RatatuiTui.__new__(RatatuiTui)
    calls = []
    rat.on_cycle_diarization = lambda: calls.append("diarization")
    rat.on_cycle_diarization_speakers = lambda: calls.append("speakers")
    rat.on_cycle_diarization_model = lambda: calls.append("model")

    for cmd in ("cycle_diarization", "cycle_diarization_speakers", "cycle_diarization_model"):
        rat._dispatch({"t": "cmd", "cmd": cmd})

    assert calls == ["diarization", "speakers", "model"]
