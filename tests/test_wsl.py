#!/usr/bin/env python3
"""
Dedicated unit test suite for Windows Subsystem for Linux (WSL / WSL2 / WSLg):
1. Platform detection heuristics (WSLg, WSL_DISTRO_NAME, WSLInterop, kernel release)
2. WSLClipboardSink: clip.exe UTF-16LE communication and PowerShell fallback
3. WSLClipboardSink + Bridge typing: host-side synthetic Ctrl+V dispatch
4. WSLAudioCuePlayer: Windows host bridge earcon forwarding (PLAY_DONE, SET_SOUND)
5. WSLHotkeyManager: IPC protocol over stdin/stdout with wsl_win_hotkeys.ps1
6. Microphone health check on WSL: pactl audio bridge detection (real mic vs monitor)
7. End-to-end WSL user workflow simulation
"""

import os
import sys
import subprocess
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import numpy as np

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal
from hal import WSL, LINUX, WINDOWS
import t2


# ===========================================================================
# 1. WSL Platform Detection Heuristics
# ===========================================================================
class TestWSLPlatformDetection:
    """Verifies all host-level WSL auto-detection heuristics."""

    def test_wsl_detection_via_wslg_socket(self, monkeypatch):
        """Presence of /mnt/wslg signals WSLg Wayland/PulseAudio environment."""
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_exists(path):
            return path == "/mnt/wslg"

        monkeypatch.setattr(os.path, "exists", fake_exists)
        assert hal.detect_platform() == WSL

    def test_wsl_detection_via_distro_env(self, monkeypatch):
        """Presence of WSL_DISTRO_NAME signals WSL environment."""
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        monkeypatch.setenv("WSL_DISTRO_NAME", "NixOS")
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os.path, "exists", lambda path: False)

        assert hal.detect_platform() == WSL

    def test_wsl_detection_via_wsl_interop(self, monkeypatch):
        """Presence of WSLInterop binfmt signals WSL environment."""
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")

        def fake_exists(path):
            return path == "/proc/sys/fs/binfmt_misc/WSLInterop"

        monkeypatch.setattr(os.path, "exists", fake_exists)
        assert hal.detect_platform() == WSL

    def test_wsl_detection_via_kernel_release(self, monkeypatch):
        """Linux kernel release string containing 'microsoft' signals WSL2."""
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(os.path, "exists", lambda path: False)

        class FakeUname:
            release = "5.15.153.1-microsoft-standard-WSL2"

        monkeypatch.setattr(os, "uname", lambda: FakeUname(), raising=False)
        assert hal.detect_platform() == WSL

    def test_wsl_env_override_takes_precedence(self, monkeypatch):
        """VT_PLATFORM=wsl forces WSL mode regardless of host heuristics."""
        monkeypatch.setenv("VT_PLATFORM", "wsl")
        assert hal.detect_platform() == WSL


# ===========================================================================
# 2. WSL Clipboard Sink (clip.exe & PowerShell fallback)
# ===========================================================================
class TestWSLClipboardSink:
    """Verifies copy_text and type_text behaviors on the Windows host."""

    def test_clip_exe_utf16le_piping(self, monkeypatch):
        """WSLClipboardSink feeds UTF-16LE encoded bytes to clip.exe."""
        WSLClipboardSink = hal.load_backend("wsl", "clipboard").WSLClipboardSink
        sink = WSLClipboardSink()

        captured_inputs = []

        class FakeProcess:
            def __init__(self, args, **kwargs):
                self.args = args

            def communicate(self, input=None):
                captured_inputs.append((self.args, input))
                return (b"", b"")

        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/clip.exe" if cmd == "clip.exe" else None)
        monkeypatch.setattr(subprocess, "Popen", FakeProcess)

        text = "Hello from NixOS on WSL! 🎙️"
        assert sink.copy_text(text) is True
        assert len(captured_inputs) == 1
        args, payload = captured_inputs[0]
        assert "clip.exe" in args[0]
        assert payload == text.encode("utf-16le")

    def test_clip_exe_fallback_to_powershell(self, monkeypatch):
        """If clip.exe fails, WSLClipboardSink falls back to PowerShell Set-Clipboard."""
        WSLClipboardSink = hal.load_backend("wsl", "clipboard").WSLClipboardSink
        sink = WSLClipboardSink()

        powershell_calls = []

        # Make clip.exe fail
        def fake_popen(*args, **kwargs):
            raise OSError("clip.exe not executable")

        def fake_run(cmd, **kwargs):
            powershell_calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(subprocess, "run", fake_run)

        text = "Fallback to powershell"
        assert sink.copy_text(text) is True
        assert len(powershell_calls) == 1
        assert "Set-Clipboard" in powershell_calls[0][3]

    def test_type_text_triggers_bridge_paste(self):
        """type_text on WSL delegates to the bridge to trigger a host-side Ctrl+V."""
        WSLClipboardSink = hal.load_backend("wsl", "clipboard").WSLClipboardSink
        mock_bridge = MagicMock()
        mock_bridge.paste_text.return_value = True

        sink = WSLClipboardSink(bridge=mock_bridge)
        assert sink.type_text("some transcription") is True
        mock_bridge.paste_text.assert_called_once()

    def test_set_bridge_wires_correctly(self):
        WSLClipboardSink = hal.load_backend("wsl", "clipboard").WSLClipboardSink
        sink = WSLClipboardSink()
        assert sink.bridge is None

        mock_bridge = MagicMock()
        mock_bridge.paste_text.return_value = True
        sink.set_bridge(mock_bridge)
        assert sink.bridge is mock_bridge
        assert sink.type_text("test") is True


# ===========================================================================
# 3. WSL Audio Cue Player (Windows Host Earcon Bridge)
# ===========================================================================
class TestWSLAudioCuePlayer:
    """Verifies audio cues on WSL forward requests to the Windows host bridge."""

    def test_play_cue_complete_forwards_play_done_sound(self):
        WSLAudioCuePlayer = hal.load_backend("wsl", "audio_cues").WSLAudioCuePlayer
        mock_bridge = MagicMock()
        player = WSLAudioCuePlayer(bridge=mock_bridge)

        player.play_cue("complete")
        mock_bridge.play_done_sound.assert_called_once()

        mock_bridge.reset_mock()
        player.play_cue("stop")
        mock_bridge.play_done_sound.assert_called_once()

    def test_play_cue_start_is_host_native(self):
        """The Windows PowerShell host script already chimes on hotkey down, so start is a no-op."""
        WSLAudioCuePlayer = hal.load_backend("wsl", "audio_cues").WSLAudioCuePlayer
        mock_bridge = MagicMock()
        player = WSLAudioCuePlayer(bridge=mock_bridge)

        player.play_cue("start")
        mock_bridge.play_done_sound.assert_not_called()

    def test_set_sound_theme_forwards_to_bridge(self):
        WSLAudioCuePlayer = hal.load_backend("wsl", "audio_cues").WSLAudioCuePlayer
        mock_bridge = MagicMock()
        player = WSLAudioCuePlayer(bridge=mock_bridge)

        player.set_sound_theme("classic")
        mock_bridge.set_sound_theme.assert_called_once_with("classic")


# ===========================================================================
# 4. WSL Hotkey Manager IPC Protocol
# ===========================================================================
class TestWSLHotkeyManagerIPC:
    """Verifies WSLHotkeyManager communication protocol with wsl_win_hotkeys.ps1."""

    def _create_mock_manager(self, monkeypatch):
        WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager

        # Prevent actual powershell.exe execution
        class MockStdin:
            def __init__(self):
                self.writes = []

            def write(self, s):
                self.writes.append(s)

            def flush(self):
                pass

        class MockStdout:
            def __init__(self):
                self.lines = ["READY\n"]

            def readline(self):
                if self.lines:
                    return self.lines.pop(0)
                return ""

        class MockProcess:
            def __init__(self):
                self.stdin = MockStdin()
                self.stdout = MockStdout()

            def poll(self):
                return None

            def terminate(self):
                pass

        mock_proc = MockProcess()
        monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: mock_proc)
        monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0))

        cb_start = MagicMock()
        cb_stop = MagicMock()
        cb_config = MagicMock()

        manager = WSLHotkeyManager(cb_start, cb_stop, cb_config)
        return manager, mock_proc

    def test_outbound_commands(self, monkeypatch):
        """Verifies messages sent over stdin to the Windows host script."""
        manager, proc = self._create_mock_manager(monkeypatch)
        try:
            assert manager.type_text("hello") is True
            assert "PASTE\n" in proc.stdin.writes

            assert manager.paste_text(terminal=True) is True
            assert "PASTE_TERMINAL\n" in proc.stdin.writes

            assert manager.play_done_sound() is True
            assert "PLAY_DONE\n" in proc.stdin.writes

            assert manager.set_sound_theme("notify") is True
            assert "SET_SOUND:notify\n" in proc.stdin.writes

            assert manager.set_middle_click_enabled(True) is True
            assert "SET_MCLICK:1\n" in proc.stdin.writes

            assert manager.set_middle_click_enabled(False) is True
            assert "SET_MCLICK:0\n" in proc.stdin.writes
        finally:
            manager.stop()
            assert "EXIT\n" in proc.stdin.writes

    def test_inbound_hotkey_events(self, monkeypatch):
        """Verifies messages received over stdout trigger the callbacks."""
        WSLHotkeyManager = hal.load_backend("wsl", "hotkeys").WSLHotkeyManager
        cb_start = MagicMock()
        cb_stop = MagicMock()
        cb_config = MagicMock()

        manager = WSLHotkeyManager.__new__(WSLHotkeyManager)
        manager.callback_start = cb_start
        manager.callback_stop = cb_stop
        manager.callback_config = cb_config
        manager.running = True
        manager.hotkey_active = False

        # Feed HOTKEY_DOWN line directly into reader event handler
        class FakeStdout:
            def __init__(self, events):
                self.events = [e + "\n" for e in events]

            def readline(self):
                if self.events:
                    return self.events.pop(0)
                return ""

        manager.process = MagicMock()
        manager.process.poll.return_value = None
        manager.process.stdout = FakeStdout(["HOTKEY_DOWN", "HOTKEY_UP", "CONFIG_DOWN"])

        manager._reader_loop()

        # Give threaded callbacks a fraction of a second to complete
        time.sleep(0.05)
        cb_start.assert_called_once()
        cb_stop.assert_called_once_with(copy_to_clipboard=True)
        cb_config.assert_called_once()


# ===========================================================================
# 5. WSL Microphone Health Check (PulseAudio / WSLg Bridge)
# ===========================================================================
class TestWSLMicrophoneHealthCheck:
    """Verifies check_microphone_health correctly audits WSLg audio sources."""

    def test_wslg_only_monitor_reports_warning(self, monkeypatch):
        """If WSLg only has speaker monitor and no input mic, flags an issue."""
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/mnt/wslg")

        # pactl returns only monitor
        pactl_output = "0\tsink.monitor\tPipeWire\ts16le 2ch 48000Hz\tIDLE"

        def fake_run(args, **kwargs):
            if args[0] == "pactl":
                return subprocess.CompletedProcess(args, 0, stdout=pactl_output, stderr="")
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        # Mock sounddevice with a dummy input device
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [{'name': 'pulse', 'max_input_channels': 2}]
        monkeypatch.setattr(t2, 'sd', mock_sd)

        is_healthy, issues = t2.check_microphone_health()
        assert is_healthy is False
        assert any("WSLg audio bridge has no active microphone source" in issue for issue in issues)

    def test_wslg_with_real_mic_reports_healthy(self, monkeypatch):
        """When real Windows microphone source exists in WSLg, reports healthy."""
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/mnt/wslg")

        pactl_output = (
            "0\tsink.monitor\tPipeWire\ts16le 2ch 48000Hz\tIDLE\n"
            "1\tRDPSource.input\tPipeWire\ts16le 1ch 16000Hz\tRUNNING\n"
        )

        def fake_run(args, **kwargs):
            if args[0] == "pactl":
                return subprocess.CompletedProcess(args, 0, stdout=pactl_output, stderr="")
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [{'name': 'RDPSource', 'max_input_channels': 1}]
        monkeypatch.setattr(t2, 'sd', mock_sd)

        is_healthy, issues = t2.check_microphone_health()
        assert is_healthy is True
        assert issues == []


# ===========================================================================
# 6. End-to-End WSL Dictation Workflow
# ===========================================================================
class TestWSLEndToEndWorkflow:
    """Verifies the complete dictation workflow running inside WSL."""

    def test_full_wsl_dictation_and_paste(self, monkeypatch):
        """Simulate user holding Alt+Shift in Windows -> WSL transcribes -> pastes to host cursor."""
        from main import SimpleVoiceTranscriber

        monkeypatch.setattr(hal, "detect_platform", lambda: WSL)
        monkeypatch.setattr(t2, "OUTPUT_MODE", "type_fast")
        monkeypatch.setattr(t2, "AUTO_TYPE_AUTO_PUNCTUATE", True)
        monkeypatch.setattr(t2, "AUTO_TYPE_TRAILING_SPACE", True)

        app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
        app.platform = WSL
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

        # Mock WSL components
        mock_bridge = MagicMock()
        mock_bridge.are_modifiers_pressed.return_value = False
        mock_bridge.type_text.return_value = True

        WSLClipboardSink = hal.load_backend("wsl", "clipboard").WSLClipboardSink
        app.clipboard_sink = WSLClipboardSink(bridge=mock_bridge)

        WSLAudioCuePlayer = hal.load_backend("wsl", "audio_cues").WSLAudioCuePlayer
        app.audio_cues = WSLAudioCuePlayer(bridge=mock_bridge)
        app.hotkey_system = mock_bridge

        copied_host_text = []

        def fake_copy_to_windows(text, sink=None):
            copied_host_text.append(text)
            return True

        monkeypatch.setattr("main.copy_to_clipboard_crossplatform", fake_copy_to_windows)
        monkeypatch.setattr("main.process_audio_stream", lambda frames: ("testing wsl voice transcriber", 0.05))

        # Run transcription processing
        app.process_recording()

        # 1. Host received the synthetic typing / paste request
        mock_bridge.type_text.assert_called_once()
        args, kwargs = mock_bridge.type_text.call_args
        assert "testing wsl voice transcriber." in args[0].lower()

        # 2. Host clipboard backup was completed
        time.sleep(0.05)
        assert len(copied_host_text) == 1
        assert "testing wsl voice transcriber." in copied_host_text[0].lower()
