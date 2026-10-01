#!/usr/bin/env python3
"""Cross-platform HAL contract tests (``src/platform/``).

Platform detection + factory selection, plus the guarantee that every backend
module imports cleanly on any host. These run on Linux, Windows, and WSL.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


class TestDetectPlatform:
    @pytest.mark.parametrize("value", [hal.LINUX, hal.WSL, hal.WINDOWS, hal.MACOS])
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
            (hal.MACOS, "MacOSClipboardSink"),
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
            (hal.MACOS, "MacOSAudioCuePlayer"),
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
            (hal.MACOS, "MacOSHotkeyManager"),
        ],
    )
    def test_hotkey_factory(self, platform, manager_name):
        manager = hal.create_hotkey_manager(platform)
        try:
            assert type(manager).__name__ == manager_name
        finally:
            manager.cleanup()

    @pytest.mark.parametrize(
        "platform,notification_name",
        [
            (hal.LINUX, "VisualNotification"),
            (hal.WINDOWS, "WindowsVisualNotification"),
            (hal.WSL, "VisualNotification"),
            (hal.MACOS, "MacOSVisualNotification"),
        ],
    )
    def test_visual_notification_factory(self, monkeypatch, platform, notification_name):
        monkeypatch.setenv("VT_PLATFORM", platform)
        notifier = hal.get_visual_notification()
        assert type(notifier).__name__ == notification_name


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
            (hal.MACOS, "hotkeys"),
            (hal.MACOS, "clipboard"),
            (hal.MACOS, "audio_cues"),
            (hal.MACOS, "notifications"),
        ],
    )
    def test_backend_module_imports(self, platform, module):
        assert hal.load_backend(platform, module) is not None


class TestStdlibShadowGuard:
    """``src/platform`` must never rob callers of the stdlib ``platform`` API.

    ``src/`` is on ``sys.path`` and the HAL package is named ``platform``, so a
    plain ``import platform`` can resolve to it. The shadow guard replaces
    ``sys.modules['platform']`` with the real stdlib module. A frozen
    PyInstaller build packs the HAL *as* ``platform`` and ships the stdlib copy
    as data under ``stdlib_shim/``, which is the path that regressed (crash at
    startup inside ``pyi_rth_pkgres``). Each case runs in a subprocess so the
    stdlib module never gets permanently swapped out of the test process.
    """

    def _run(self, script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            capture_output=True,
            text=True,
        )

    def test_frozen_bundle_uses_bundled_stdlib_shim(self, tmp_path):
        shim_dir = tmp_path / "stdlib_shim"
        shim_dir.mkdir()
        (shim_dir / "platform.py").write_text(
            "SENTINEL = 'vt-shim'\n"
            "def system():\n    return 'shim'\n"
            "def uname():\n    return ()\n",
            encoding="utf-8",
        )
        result = self._run(
            f"""
            import sys
            sys.path.insert(0, {str(SRC_DIR)!r})
            sys.modules.pop('platform', None)
            sys._MEIPASS = {str(tmp_path)!r}
            import platform
            assert platform.system() == 'shim', platform.system()
            assert platform.SENTINEL == 'vt-shim'
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK"

    def test_source_checkout_falls_back_to_stdlib(self):
        result = self._run(
            f"""
            import sys
            sys.path.insert(0, {str(SRC_DIR)!r})
            sys.modules.pop('platform', None)
            if hasattr(sys, '_MEIPASS'):
                del sys._MEIPASS
            import platform
            assert isinstance(platform.system(), str)
            assert hasattr(platform, 'uname')
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "OK"


class TestHotkeysShimBackwardCompatibility:
    def test_legacy_entry_points_exist(self):
        import hotkeys

        assert callable(hotkeys.create_global_hotkeys)
        assert callable(hotkeys.is_running_in_wsl)
        assert callable(hotkeys.set_global_sound_theme)
        assert hotkeys.WaylandGlobalHotkeys is not None
        assert hotkeys.WSLGlobalHotkeys is not None
        assert hotkeys.WindowsGlobalHotkeys is not None
        assert hotkeys.MacOSGlobalHotkeys is not None
        assert hotkeys.MacOSHotkeyManager is not None

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

