#!/usr/bin/env python3
"""Cross-platform HAL contract tests (``src/platform/``).

Platform detection + factory selection, plus the guarantee that every backend
module imports cleanly on any host. These run on Linux, Windows, and WSL.
"""
from __future__ import annotations

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
    def test_hotkey_factory(self, platform, manager_name, monkeypatch):
        if platform == hal.MACOS:
            # Constructing the macOS manager starts a real pynput listener. On a
            # headless runner that ends up calling the TIS/TSM input-source APIs
            # from a worker thread and *segfaults the whole pytest process*
            # (exit 139), taking the macOS CI job down with it — and a segfault
            # cannot be caught in Python. The shared tier is documented as
            # hermetic, so patch the OS listener out; the factory's actual job
            # (platform -> class) is still what is under test.
            backend = hal.load_backend("macos", "hotkeys")
            monkeypatch.setattr(
                backend.MacOSHotkeyManager, "_start_listener", lambda self: None
            )

        manager = hal.create_hotkey_manager(platform)
        try:
            assert type(manager).__name__ == manager_name
            if platform == hal.MACOS:
                # Guard the patch above: if it ever stops applying, the next
                # macOS run segfaults instead of failing a test.
                assert manager.listener is None
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
            sys.path.insert(0, {str(SRC_DIR / "voice_transcriber")!r})
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



class TestNoStalePlatformImports:
    """The HAL package is loaded under the private name ``vt_platform`` precisely so
    it cannot shadow the stdlib :mod:`platform` module. An absolute import written
    against the old layout — ``from platform.macos.hotkeys import ...`` — resolves
    to the *stdlib* module and dies with "No module named 'platform.macos';
    'platform' is not a package".

    That shipped in ``doctor.py`` and only macOS CI caught it, because the branch
    is darwin-only. Nothing static prevented a repeat, so this does.
    """

    def test_no_module_imports_the_hal_by_the_bare_stdlib_name(self):
        import re

        stale = re.compile(r"^\s*(?:from|import)\s+platform\.(linux|macos|windows|wsl)\b")
        offenders = []
        for path in sorted(SRC_DIR.rglob("*.py")):
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if stale.match(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")

        assert not offenders, (
            "import the backend through hal.load_backend() instead:\n  "
            + "\n  ".join(offenders)
        )


class TestDoctorLoadsMacBackendsThroughTheHal:
    """``doctor`` is the one place outside the HAL that needs a specific backend."""

    def test_macos_hotkey_check_goes_through_the_hal(self, monkeypatch):
        import doctor

        class FakeMacHotkeys:
            called = False

            @classmethod
            def check_accessibility_permissions(cls):
                cls.called = True
                return True

        seen = []
        monkeypatch.setattr(
            doctor.hal,
            "load_backend",
            lambda plat, name: seen.append((plat, name)) or FakeMacHotkeys,
        )

        status = doctor.check_hotkeys_and_permissions(doctor.hal.MACOS)

        assert seen == [("macos", "hotkeys")]
        assert FakeMacHotkeys.called is True
        assert status["ok"] is True

    def test_a_denied_macos_permission_fails_the_check(self, monkeypatch):
        import doctor

        class Denied:
            @staticmethod
            def check_accessibility_permissions():
                return False

        monkeypatch.setattr(doctor.hal, "load_backend", lambda plat, name: Denied)

        status = doctor.check_hotkeys_and_permissions(doctor.hal.MACOS)

        assert status["ok"] is False
        assert any("Accessibility" in err for err in status["errors"])
