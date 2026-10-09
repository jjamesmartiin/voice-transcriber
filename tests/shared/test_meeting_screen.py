#!/usr/bin/env python3
"""The meeting screen's terminal key contract, and the speakers map.

The speaker map is transcript metadata, not app config, so it is edited from the
*meeting screen* rather than the settings modal. That makes the meeting screen
the one place terminal keys differ from the global contract, and both halves of
that are pinned here.
"""
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2  # noqa: E402
from tui import VoiceTranscriberTUI  # noqa: E402


def _tui_with_spies(state="READY"):
    tui = VoiceTranscriberTUI(ui_theme="cyan")
    calls = []
    tui.on_toggle_record = lambda: calls.append("record")
    tui.on_open_settings_picker = lambda: calls.append("settings")
    tui.on_reset_terminal = lambda: calls.append("reset")
    tui.on_quit = lambda: calls.append("quit")
    tui.on_toggle_meeting = lambda: calls.append("meeting")
    tui.on_open_speaker_editor = lambda: calls.append("speakers")
    tui.state = state
    return tui, calls


class TestGlobalContractOutsideTheMeetingScreen:
    """The documented global key set is unchanged when not in a meeting."""

    def test_space_and_enter_toggle_recording(self):
        tui, calls = _tui_with_spies()
        tui._handle_keypress(" ")
        tui._handle_keypress("\r")
        tui._handle_keypress("\n")
        assert calls == ["record", "record", "record"]

    def test_s_and_comma_open_the_settings_modal(self):
        tui, calls = _tui_with_spies()
        for key in ("s", "S", ","):
            tui._handle_keypress(key)
        assert calls == ["settings", "settings", "settings"]
        assert "speakers" not in calls

    def test_r_resets_the_terminal_and_q_quits(self):
        tui, calls = _tui_with_spies()
        tui._handle_keypress("r")
        tui._handle_keypress("q")
        assert calls == ["reset", "quit"]


class TestMeetingScreenKeyMap:
    """On the meeting screen, Space/Enter capture, ``s`` names, Esc quits."""

    def test_space_and_enter_control_the_capture(self):
        tui, calls = _tui_with_spies(state="MEETING")
        tui._handle_keypress(" ")
        tui._handle_keypress("\r")
        assert calls == ["meeting", "meeting"]
        assert "record" not in calls

    def test_s_opens_the_speaker_editor_not_settings(self):
        tui, calls = _tui_with_spies(state="MEETING")
        tui._handle_keypress("s")
        tui._handle_keypress("S")
        assert calls == ["speakers", "speakers"]
        assert "settings" not in calls

    def test_escape_quits(self):
        tui, calls = _tui_with_spies(state="MEETING")
        tui._handle_keypress("\x1b")
        tui._handle_keypress("q")
        assert calls == ["quit", "quit"]


class TestEngineSpeakerMap:
    def _engine(self, monkeypatch, tmp_path):
        main = pytest.importorskip("main")
        monkeypatch.setattr(t2, "CONFIG_FILE", str(tmp_path / "config.yaml"))
        engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
        engine.speaker_names = []
        return engine

    def test_set_and_get_normalise_the_list(self, monkeypatch, tmp_path):
        engine = self._engine(monkeypatch, tmp_path)
        assert engine.get_speaker_names() == []
        engine.set_speaker_names("Priya, Sam , ")
        assert engine.get_speaker_names() == ["Priya", "Sam"]
        engine.set_speaker_names(["Bo", "", "Ada"])
        assert engine.get_speaker_names() == ["Bo", "Ada"]

    def test_the_speakers_control_verb_lists_and_sets(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock
        engine = self._engine(monkeypatch, tmp_path)
        engine.tui = MagicMock()
        engine.tui.state = "READY"
        engine.recording = False
        engine.hotkey_system = None

        listed = engine.handle_control("speakers", {})
        assert listed["speakers"] == []

        set_reply = engine.handle_control("speakers", {"value": "Priya, Sam"})
        assert set_reply["speakers"] == ["Priya", "Sam"]
        assert engine.get_speaker_names() == ["Priya", "Sam"]

        # Aliases resolve to the same verb.
        assert engine.handle_control("speaker-names", {})["speakers"] == ["Priya", "Sam"]

    def test_speaker_names_are_status_metadata(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock
        engine = self._engine(monkeypatch, tmp_path)
        engine.tui = MagicMock()
        engine.tui.state = "READY"
        engine.recording = False
        engine.hotkey_system = None
        engine.set_speaker_names("Priya")
        status = engine.handle_control("status", {})
        assert status["speakers"] == ["Priya"]
        assert status["meeting_output_dir"] == "meetings"
        assert status["meeting_output_format"] == "text"


REPO_ROOT = Path(__file__).resolve().parents[2]
RUST_MAIN = REPO_ROOT / "tui-rs" / "src" / "main.rs"
RUST_SPEAKERS = REPO_ROOT / "tui-rs" / "src" / "speaker_picker.rs"


class TestRatatuiMeetingScreen:
    """The ratatui half of the same contract, pinned by source.

    The Rust key map and the native speaker editor live in another language, so
    the literals that matter are held here. Behavioural coverage is on the Rust
    side (`main.rs::tests::test_meeting_screen_keys_differ` and
    `speaker_picker::tests`); this is the drift guard.
    """

    def _implementation(self, path):
        source = path.read_text(encoding="utf-8")
        return source.split("\n#[cfg(test)]", 1)[0]

    def test_meeting_screen_keys_are_scoped_to_the_meeting_state(self):
        implementation = self._implementation(RUST_MAIN)
        assert "RunState::Meeting" in implementation, (
            "tui-rs/src/main.rs no longer branches on RunState::Meeting")
        assert "Intent::ToggleMeeting" in implementation
        assert "Intent::OpenSpeakerEditor" in implementation
        # Space/Enter stop the capture through the engine, not dictation.
        assert '"toggle_meeting"' in implementation
        # The speaker editor is the native ratatui modal.
        assert "run_speaker_picker" in implementation

    def test_meeting_screen_leaves_the_settings_and_reset_keys_alone(self):
        implementation = self._implementation(RUST_MAIN)
        # Outside the meeting branch the global contract is untouched.
        assert "Intent::OpenSettingsPicker" in implementation
        assert "Intent::ResetTerminal" in implementation
        assert '"toggle_record"' in implementation

    def test_the_ratatui_speaker_editor_sends_the_ordered_list(self):
        implementation = self._implementation(RUST_SPEAKERS)
        assert "set_speakers" in implementation, (
            "tui-rs/src/speaker_picker.rs no longer sends set_speakers")
        assert '"speakers"' in implementation
        # The editor is native; the Rich editor stays a Python concern.
        assert "run_speaker_picker" in implementation


class TestRatatuiSpeakerBridge:
    def test_cfg_pushes_the_live_speakers_map_and_rust_parses_it(self):
        from tui_ratatui import RatatuiTui

        rat = RatatuiTui.__new__(RatatuiTui)
        sent = []
        rat._send = lambda msg: sent.append(msg)
        rat.set_config_state(speakers=["Priya", "Sam"])
        assert sent[-1] == {"t": "cfg", "speakers": ["Priya", "Sam"]}

        # The Rust wire decodes the same field.
        ipc = (REPO_ROOT / "tui-rs" / "src" / "ipc.rs").read_text(encoding="utf-8")
        assert "speakers: Option<Vec<String>>" in ipc
