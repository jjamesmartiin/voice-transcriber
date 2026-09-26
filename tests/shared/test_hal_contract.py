#!/usr/bin/env python3
"""Cross-platform HAL contract tests (``src/platform/``).

Platform detection + factory selection, plus the guarantee that every backend
module imports cleanly on any host. These run on Linux, Windows, and WSL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


class TestDetectPlatform:
    @pytest.mark.parametrize("value", [hal.LINUX, hal.WSL, hal.WINDOWS])
    def test_env_override_wins(self, monkeypatch, value):
        monkeypatch.setenv("VT_PLATFORM", value)
        assert hal.detect_platform() == value

    def test_unknown_override_is_a_hard_error(self, monkeypatch):
        monkeypatch.setenv("VT_PLATFORM", "beos")
        with pytest.raises(ValueError):
            hal.detect_platform()

    def test_autodetect_without_override(self, monkeypatch):
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        assert hal.detect_platform() in hal.VALID_PLATFORMS


class TestFactories:
    @pytest.mark.parametrize(
        "platform,sink_name",
        [
            (hal.LINUX, "LinuxClipboardSink"),
            (hal.WINDOWS, "WindowsClipboardSink"),
            (hal.WSL, "WSLClipboardSink"),
        ],
    )
    def test_clipboard_factory(self, platform, sink_name):
        assert type(hal.get_clipboard_sink(platform)).__name__ == sink_name

    @pytest.mark.parametrize(
        "platform,player_name",
        [
            (hal.LINUX, "LinuxAudioCuePlayer"),
            (hal.WINDOWS, "WindowsAudioCuePlayer"),
            (hal.WSL, "WSLAudioCuePlayer"),
        ],
    )
    def test_audio_cue_factory(self, platform, player_name):
        assert type(hal.get_audio_cue_player(platform)).__name__ == player_name

    @pytest.mark.parametrize(
        "platform,manager_name",
        [
            (hal.LINUX, "LinuxHotkeyManager"),
            (hal.WINDOWS, "WindowsHotkeyManager"),
            (hal.WSL, "WSLHotkeyManager"),
        ],
    )
    def test_hotkey_factory(self, platform, manager_name):
        manager = hal.create_hotkey_manager(platform)
        try:
            assert type(manager).__name__ == manager_name
        finally:
            manager.cleanup()


class TestImportSafety:
    """Every backend module must import on Linux, even without native deps."""

    @pytest.mark.parametrize(
        "platform,module",
        [
            (hal.LINUX, "hotkeys"),
            (hal.LINUX, "clipboard"),
            (hal.LINUX, "audio_cues"),
            (hal.WINDOWS, "hotkeys"),
            (hal.WINDOWS, "clipboard"),
            (hal.WINDOWS, "audio_cues"),
            (hal.WINDOWS, "notifications"),
            (hal.WSL, "hotkeys"),
            (hal.WSL, "clipboard"),
            (hal.WSL, "audio_cues"),
        ],
    )
    def test_backend_module_imports(self, platform, module):
        assert hal.load_backend(platform, module) is not None


class TestHotkeysShimBackwardCompatibility:
    def test_legacy_entry_points_exist(self):
        import hotkeys

        assert callable(hotkeys.create_global_hotkeys)
        assert callable(hotkeys.is_running_in_wsl)
        assert callable(hotkeys.set_global_sound_theme)
        assert hotkeys.WaylandGlobalHotkeys is not None
        assert hotkeys.WSLGlobalHotkeys is not None
        assert hotkeys.WindowsGlobalHotkeys is not None

    def test_is_running_in_wsl_tracks_hal(self, monkeypatch):
        import hotkeys

        monkeypatch.setenv("VT_PLATFORM", hal.WSL)
        assert hotkeys.is_running_in_wsl() is True
        monkeypatch.setenv("VT_PLATFORM", hal.LINUX)
        assert hotkeys.is_running_in_wsl() is False


class TestWindowsTuiCompatibility:
    def test_tui_imports_and_runs_without_termios(self, monkeypatch):
        import tui
        monkeypatch.setattr(tui, "termios", None)
        monkeypatch.setattr(tui, "tty", None)
        app_tui = tui.VoiceTranscriberTUI()
        assert app_tui.state == "READY"
        app_tui.update_state("RECORDING", "Testing")
        assert app_tui.state == "RECORDING"

