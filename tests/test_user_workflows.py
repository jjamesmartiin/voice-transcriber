#!/usr/bin/env python3
"""
Unit tests testing the primary user workflows:
1. Push-to-Talk (Alt+Shift down -> speak -> release -> transcribe -> output)
2. Hands-Free Latch (Alt+Shift down -> Space tap -> release -> speak -> Alt+Shift tap to stop)
3. Passive Middle-Click Push-to-Talk & Quick Click Cancellation
4. Output Modes: Fast Auto-Type with Auto-Punctuate & Trailing Space vs Clipboard
5. Temporary Ctrl Override (holding Ctrl forces clipboard output even in auto-type mode)
6. Real Dictation Post-Processing: Verbal Retractions, Custom Dictionary, Numbers, Paths, IPs
7. Interactive TUI Keypresses & Config Synchronization
8. Micro-Batching Text Overlap Deduplication & Silence Rejection
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock
import numpy as np
import pytest

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal
import t2
import post_processor as pp
from micro_batcher import (
    StreamingMicroBatcher,
    deduplicate_text_overlap,
    trim_trailing_silence,
    has_speech_activity,
)
from tui import VoiceTranscriberTUI


# ===========================================================================
# 1. Push-to-Talk, Output Modes, and Ctrl Override Workflows
# ===========================================================================
class TestPushToTalkAndOutputModesWorkflow:
    """Tests the full process_recording flow in SimpleVoiceTranscriber under all output modes."""

    def _create_app(self, monkeypatch, output_mode="type_fast", trailing_space=True, auto_punctuate=True):
        from main import SimpleVoiceTranscriber
        monkeypatch.setattr(t2, 'OUTPUT_MODE', output_mode)
        monkeypatch.setattr(t2, 'AUTO_TYPE_TRAILING_SPACE', trailing_space)
        monkeypatch.setattr(t2, 'AUTO_TYPE_AUTO_PUNCTUATE', auto_punctuate)
        monkeypatch.setattr(t2, 'IS_MUTED', True)

        app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
        app.recording = False
        app.copy_to_clipboard = False
        app.start_time = 0.0
        app.release_time = 1.0
        app.last_finish_time = 0.0
        app.audio_frames = np.ones(16000, dtype=np.float32) * 0.05
        app.last_transcription = ""
        app.model_load_error = None
        app._model_ready_event = MagicMock()
        app._model_ready_event.is_set.return_value = True

        app.tui = MagicMock()
        app.visual_notification = MagicMock()
        app.audio_cues = MagicMock()

        # Output sinks
        app.typed_text = []
        app.copied_text = []

        mock_hotkey = MagicMock()
        mock_hotkey.are_modifiers_pressed.return_value = False
        mock_hotkey.type_text.side_effect = lambda text, fast=False: app.typed_text.append(text) or True
        app.hotkey_system = mock_hotkey

        mock_sink = MagicMock()
        mock_sink.copy_text.side_effect = lambda text: app.copied_text.append(text) or True
        app.clipboard_sink = mock_sink

        monkeypatch.setattr('main.copy_to_clipboard_crossplatform', lambda text, sink=None: app.copied_text.append(text) or True)
        return app

    def test_workflow_fast_auto_type_with_punctuation_and_space(self, monkeypatch):
        """User speaks with type_fast enabled: output is typed with terminal period and trailing space."""
        app = self._create_app(monkeypatch, output_mode="type_fast", trailing_space=True, auto_punctuate=True)
        monkeypatch.setattr('main.process_audio_stream', lambda frames: ("deploying the new release", 0.08))

        app.process_recording()

        assert len(app.typed_text) == 1
        assert app.typed_text[0] == "deploying the new release. "

    def test_workflow_fast_auto_type_without_trailing_space(self, monkeypatch):
        """User disables trailing space: output has terminal punctuation but no trailing space."""
        app = self._create_app(monkeypatch, output_mode="type_fast", trailing_space=False, auto_punctuate=True)
        monkeypatch.setattr('main.process_audio_stream', lambda frames: ("system status is optimal", 0.08))

        app.process_recording()

        assert len(app.typed_text) == 1
        assert app.typed_text[0] == "system status is optimal."

    def test_workflow_clipboard_mode(self, monkeypatch):
        """User has clipboard mode selected: output is copied to clipboard, nothing typed."""
        app = self._create_app(monkeypatch, output_mode="clipboard")
        monkeypatch.setattr('main.process_audio_stream', lambda frames: ("copied text only", 0.08))

        app.process_recording()

        # In clipboard mode, no keystrokes are emitted
        assert len(app.typed_text) == 0

    def test_workflow_temporary_ctrl_clipboard_override(self, monkeypatch):
        """User has type_fast active, but held Ctrl at trigger: output redirects to clipboard (no typing)."""
        app = self._create_app(monkeypatch, output_mode="type_fast")
        app.copy_to_clipboard = True  # Ctrl key was held
        monkeypatch.setattr('main.process_audio_stream', lambda frames: ("this goes to clipboard", 0.08))

        app.process_recording()

        # Keystroke typing was skipped because Ctrl override was active
        assert len(app.typed_text) == 0


# ===========================================================================
# 2. Hands-Free Latch Workflow
# ===========================================================================
class TestHandsFreeLatchWorkflow:
    """Tests the Alt+Shift -> Space latch -> release -> Alt+Shift tap workflow."""

    def test_space_latch_workflow_linux(self):
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        cb_start = MagicMock()
        cb_stop = MagicMock()
        manager = LinuxHotkeyManager(cb_start, cb_stop)
        if not getattr(manager, "evdev", None):
            pytest.skip("Linux evdev dependency not available on this platform")
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()

        try:
            # 1. Alt+Shift pressed down
            manager.key_states[56] = 1   # KEY_LEFTALT
            manager.key_states[42] = 1   # KEY_LEFTSHIFT
            fake_alt_event = MagicMock(type=manager.evdev.ecodes.EV_KEY, code=56, value=1)
            manager.handle_key_event(fake_alt_event)
            assert manager.hotkey_active is True
            cb_start.assert_called_once()

            # 2. Space tapped while holding Alt+Shift
            fake_space_event = MagicMock(type=manager.evdev.ecodes.EV_KEY, code=57, value=1)
            manager.handle_key_event(fake_space_event)
            assert manager.latch_release is True

            # 3. Release Alt+Shift -> latch holds recording open
            manager.key_states[56] = 0
            manager.key_states[42] = 0
            fake_alt_up = MagicMock(type=manager.evdev.ecodes.EV_KEY, code=56, value=0)
            manager.handle_key_event(fake_alt_up)
            assert manager.latch_release is False
            cb_stop.assert_not_called()  # Did NOT stop recording

            # 4. Tap Alt+Shift again to conclude hands-free recording
            manager.key_states[56] = 1
            manager.key_states[42] = 1
            manager.handle_key_event(fake_alt_event)
            assert manager.hotkey_active is True

            manager.key_states[56] = 0
            manager.key_states[42] = 0
            manager.handle_key_event(fake_alt_up)
            assert manager.hotkey_active is False
            cb_stop.assert_called_once()
        finally:
            manager.cleanup()

    def test_space_latch_workflow_windows(self):
        WindowsHotkeyManager = hal.load_backend("windows", "hotkeys").WindowsHotkeyManager
        cb_start = MagicMock()
        cb_stop = MagicMock()
        manager = WindowsHotkeyManager(cb_start, cb_stop)

        try:
            manager.hotkey_active = True

            class FakeSpaceKey:
                name = "space"
                char = " "

            manager._Key = MagicMock()
            manager._Key.space = FakeSpaceKey()
            manager._KeyCode = MagicMock()

            # Space tapped during recording -> sets latch_release
            manager._on_press(FakeSpaceKey())
            assert manager.latch_release is True

            # Alt+Shift released while latched -> clears latch flag without stopping recording
            manager._on_release(FakeSpaceKey())
            assert manager.latch_release is False
            assert manager.hotkey_active is False
            cb_stop.assert_not_called()
        finally:
            manager.cleanup()


# ===========================================================================
# 3. Middle-Click Push-to-Talk & Quick Click Cancellation
# ===========================================================================
class TestMiddleClickWorkflow:
    def test_middle_click_hold_and_quick_cancel_linux(self):
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        cb_start = MagicMock()
        cb_stop = MagicMock()
        manager = LinuxHotkeyManager(cb_start, cb_stop)
        if not getattr(manager, "evdev", None):
            pytest.skip("Linux evdev dependency not available on this platform")
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()

        try:
            btn_middle = 274

            # Quick click: press and immediately release (< 0.25s)
            manager.handle_key_event(MagicMock(type=manager.evdev.ecodes.EV_KEY, code=btn_middle, value=1))
            time.sleep(0.05)
            manager.handle_key_event(MagicMock(type=manager.evdev.ecodes.EV_KEY, code=btn_middle, value=0))
            time.sleep(0.25)
            assert cb_start.call_count == 0
            assert manager.hotkey_active is False

            # Long hold: press and hold >= 0.25s -> triggers recording
            manager.handle_key_event(MagicMock(type=manager.evdev.ecodes.EV_KEY, code=btn_middle, value=1))
            time.sleep(0.28)
            assert cb_start.call_count == 1
            assert manager.hotkey_active is True

            # Release middle click -> stops recording
            manager.handle_key_event(MagicMock(type=manager.evdev.ecodes.EV_KEY, code=btn_middle, value=0))
            assert cb_stop.call_count == 1
            assert manager.hotkey_active is False
        finally:
            manager.cleanup()


# ===========================================================================
# 4. Wispr Flow Real Dictation Workflows (Speech-to-Text Post Processing)
# ===========================================================================
class TestWisprFlowDictationWorkflows:
    """Verifies that the speech patterns users actually say get cleaned properly."""

    def test_verbal_self_correction_retraction(self):
        """User corrects themselves mid-speech: previous clause is retracted."""
        # 'make that'
        assert pp.clean_speech_transcription("we will launch on monday make that tuesday", skip_slm=True) == "we will launch on tuesday."
        # 'actually'
        assert pp.clean_speech_transcription("the meeting is at two actually three", skip_slm=True) == "the meeting is at 3."
        # 'I mean'
        assert pp.clean_speech_transcription("send it to bob I mean alice", skip_slm=True) == "send it to alice."
        # 'scratch that' at phrase end
        assert pp.clean_speech_transcription("I think we should cancel the deploy, scratch that", skip_slm=True) == "I think we should cancel the deploy."

    def test_technical_custom_dictionary_replacements(self):
        """Technical domain terms are properly expanded and capitalized."""
        custom_dict = {
            "github": "GitHub",
            "gitea": "Gitea",
            "nixos": "NixOS",
            "pytorch": "PyTorch",
            "vllm": "vLLM",
            "tailscale": "Tailscale",
        }
        pp.set_custom_dictionary(custom_dict)
        try:
            text = "push the commit to github and mirror it to gitea on nixos using pytorch and vllm"
            cleaned = pp.clean_speech_transcription(text, skip_slm=True)
            assert "GitHub" in cleaned
            assert "Gitea" in cleaned
            assert "NixOS" in cleaned
            assert "PyTorch" in cleaned
            assert "vLLM" in cleaned
        finally:
            pp.set_custom_dictionary({})

    def test_spoken_numbers_to_digits(self):
        """Spoken numbers are converted to digits in natural contexts."""
        pp.set_number_digits_enabled(True)
        try:
            assert pp.clean_speech_transcription("there are twenty five servers and twelve nodes", skip_slm=True) == "there are 25 servers and 12 nodes."
            assert pp.clean_speech_transcription("refer to chapter three section four", skip_slm=True) == "refer to chapter 3 section 4."
        finally:
            pp.set_number_digits_enabled(True)

    def test_spoken_file_paths_and_ips(self):
        """Spoken paths and IP addresses format into clean technical strings."""
        assert pp.clean_spoken_paths("the config is in slash etc slash nixos now") == "the config is in /etc/nixos now"
        assert pp.clean_spoken_paths("net is 10 dot 0 dot 0 dot 0 slash 24 today") == "net is 10.0.0.0/24 today"

    def test_punctuation_modes_workflow(self):
        """Test full punctuation, no terminal period, and lowercase no punctuation modes."""
        sample = "The quick brown fox jumps over the lazy dog"

        # 1. Full mode (capitalized + period)
        pp.set_punctuation_mode("full")
        assert pp.clean_speech_transcription(sample, skip_slm=True) == "The quick brown fox jumps over the lazy dog."

        # 2. No terminal period (capitalized, internal commas, no ending period)
        pp.set_punctuation_mode("no_terminal_period")
        assert pp.clean_speech_transcription(sample, skip_slm=True) == "The quick brown fox jumps over the lazy dog"

        # 3. Lowercase no punctuation (ideal for terminal CLI commands)
        pp.set_punctuation_mode("lowercase_no_punctuation")
        assert pp.clean_speech_transcription(sample, skip_slm=True) == "the quick brown fox jumps over the lazy dog"

        # Restore default
        pp.set_punctuation_mode("full")


# ===========================================================================
# 5. Interactive TUI Keypresses & Config Persistence
# ===========================================================================
class TestTuiKeypressAndConfigSyncWorkflow:
    """Verifies that pressing TUI shortcut keys updates in-memory state and saves to config."""

    def test_tui_keypress_actions(self, tmp_path, monkeypatch):
        yaml_config = tmp_path / "config.yaml"
        yaml_config.write_text("output_mode: clipboard\nis_muted: false\nauto_type_trailing_space: true\n")
        monkeypatch.setattr(t2, 'get_config_file', lambda: yaml_config)
        t2.load_audio_config()

        tui = VoiceTranscriberTUI()

        # Wire TUI callbacks to t2 functions (mirrors SimpleVoiceTranscriber)
        tui.on_cycle_output_mode = lambda: (t2.cycle_output_mode(), t2.save_audio_config())
        tui.on_toggle_mute = lambda: (setattr(t2, 'IS_MUTED', not t2.IS_MUTED), t2.save_audio_config())
        tui.on_toggle_trailing_space = lambda: (t2.toggle_auto_type_trailing_space(), t2.save_audio_config())

        # Press 'c' -> cycle output mode: clipboard -> type
        tui._handle_keypress('c')
        assert t2.OUTPUT_MODE == "type"

        # Press 'c' again -> cycle: type -> type_fast
        tui._handle_keypress('c')
        assert t2.OUTPUT_MODE == "type_fast"

        # Press 'm' -> toggle mute
        tui._handle_keypress('m')
        assert t2.IS_MUTED is True

        # Press 's' -> toggle trailing space
        tui._handle_keypress('s')
        assert t2.AUTO_TYPE_TRAILING_SPACE is False

        # Verify config was persisted to file
        import yaml
        saved = yaml.safe_load(yaml_config.read_text())
        assert saved["output_mode"] == "type_fast"
        assert saved["is_muted"] is True
        assert saved["auto_type_trailing_space"] is False


# ===========================================================================
# 6. Streaming Micro-Batcher Dynamic Speech Processing
# ===========================================================================
class TestMicroBatcherSpeechWorkflow:
    def test_overlap_deduplication_continuous_speech(self):
        """Verifies overlapping chunks merge into continuous sentences cleanly."""
        chunk1 = "we need to ensure all unit tests"
        chunk2 = "unit tests pass across all environments"
        merged = deduplicate_text_overlap(chunk1, chunk2)
        assert merged == "we need to ensure all unit tests pass across all environments"

    def test_energy_gate_silence_vs_speech(self):
        """Energy gate correctly rejects pure zeros and low noise, detects speech."""
        silence = np.zeros(16000, dtype=np.float32)
        assert has_speech_activity(silence) is False

        low_ambient_noise = np.random.uniform(-0.001, 0.001, 16000).astype(np.float32)
        assert has_speech_activity(low_ambient_noise) is False

        # Real speech energy level (RMS >= 0.0035 and Peak >= 0.015)
        t = np.linspace(0, 1, 16000, endpoint=False)
        sine_speech = (0.05 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        assert has_speech_activity(sine_speech) is True

    def test_tail_silence_trimmer(self):
        """Trailing silence is trimmed leaving minimum speech cushion."""
        sr = 16000
        speech = np.random.uniform(-0.05, 0.05, sr).astype(np.float32)
        silence = np.zeros(sr * 2, dtype=np.float32)
        audio = np.concatenate([speech, silence])

        trimmed = trim_trailing_silence(audio, sample_rate=sr, min_speech_cushion_ms=100)
        assert len(trimmed) < len(audio)
        expected_len = int(1.0 * sr + 0.1 * sr)
        assert abs(len(trimmed) - expected_len) < 800
