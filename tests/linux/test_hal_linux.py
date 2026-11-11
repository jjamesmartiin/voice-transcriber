#!/usr/bin/env python3
"""Linux-specific HAL tests: clipboard command lines and uinput/evdev typing."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import hal  # noqa: E402


class TestLinuxClipboardSink:
    def test_wl_copy_preferred(self, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        assert hal.get_clipboard_sink(hal.LINUX).copy_text("hello") is True
        assert calls[0][0] == ["wl-copy"]
        assert calls[0][1]["input"] == b"hello"
        # The tool is given a leash: a copy must never block the pipeline. This is
        # far below the 1.5 s post-release budget, while a healthy tool returns in
        # milliseconds.
        assert 0 < calls[0][1]["timeout"] <= 0.5

    def test_a_tool_that_holds_the_selection_is_a_successful_copy(self, monkeypatch):
        """`wl-copy` can keep serving the clipboard instead of forking away.

        That used to hang the caller forever (it hung pytest tests/e2e on a machine
        where wl-clipboard 2.3.0 behaved that way). A tool still holding the
        selection has accepted the payload, so the copy counts as done.
        """
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None
        )

        def blocking_run(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 0))

        monkeypatch.setattr(subprocess, "run", blocking_run)
        assert hal.get_clipboard_sink(hal.LINUX).copy_text("held open") is True

    def test_a_tool_that_cannot_start_is_a_failed_copy(self, monkeypatch):
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None
        )

        def unspawnable(args, **kwargs):
            raise OSError("no such file")

        monkeypatch.setattr(subprocess, "run", unspawnable)
        assert hal.get_clipboard_sink(hal.LINUX).copy_text("nope") is False

    def test_xclip_fallback(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name == "xclip" else None
        )
        monkeypatch.setattr(
            subprocess, "run", lambda args, **kw: calls.append(args) or subprocess.CompletedProcess(args, 0)
        )

        assert hal.get_clipboard_sink(hal.LINUX).copy_text("hi") is True
        assert calls[0] == ["xclip", "-selection", "clipboard"]

    def test_no_tool_returns_false(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert hal.get_clipboard_sink(hal.LINUX).copy_text("nowhere") is False

    def test_ydotool_then_xdotool_typing(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("ydotool", "xdotool") else None
        )
        monkeypatch.setattr(
            subprocess, "run", lambda args, **kw: seen.append(args) or subprocess.CompletedProcess(args, 0)
        )

        sink = hal.get_clipboard_sink(hal.LINUX)
        assert sink.type_text("one") is True
        assert seen[-1] == ["ydotool", "type", "--", "one"]

        assert sink.type_text("one", fast=True) is True
        assert seen[-1] == ["ydotool", "type", "-d", "1", "-s", "1", "--", "one"]

    def test_ydotool_failure_falls_back_to_xdotool(self, monkeypatch):
        """When ydotool exits with error (e.g. ydotoold not running), xdotool is tried."""
        seen = []
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("ydotool", "xdotool") else None
        )
        def run_fail_first(args, **kw):
            seen.append(args[0])
            if args[0] == "ydotool":
                return subprocess.CompletedProcess(args, 1)  # failed
            return subprocess.CompletedProcess(args, 0)      # success

        monkeypatch.setattr(subprocess, "run", run_fail_first)
        sink = hal.get_clipboard_sink(hal.LINUX)
        assert sink.type_text("hello") is True
        assert seen == ["ydotool", "xdotool"]

    def test_all_typing_tools_failing_falls_back_to_clipboard(self, monkeypatch):
        """When all typing tools fail, text is preserved by copying to clipboard."""
        monkeypatch.setattr(
            shutil, "which", lambda name: f"/usr/bin/{name}" if name in ("ydotool", "wl-copy") else None
        )
        calls = []
        def run_check(args, **kw):
            calls.append(args[0])
            if args[0] == "ydotool":
                return subprocess.CompletedProcess(args, 1)  # failed typing
            return subprocess.CompletedProcess(args, 0)      # successful copy

        monkeypatch.setattr(subprocess, "run", run_check)
        sink = hal.get_clipboard_sink(hal.LINUX)
        assert sink.type_text("hello") is True
        assert "wl-copy" in calls


class TestPasteTextSupport:
    def test_linux_hotkey_manager_paste_text(self):
        from unittest.mock import MagicMock
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        manager = LinuxHotkeyManager(MagicMock(), MagicMock())
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()
        try:
            assert manager.paste_text(terminal=False) is True
            assert manager.virtual_keyboard.emit.call_count >= 4

            manager.virtual_keyboard.reset_mock()
            assert manager.paste_text(terminal=True) is True
            assert manager.virtual_keyboard.emit.call_count >= 6
        finally:
            manager.cleanup()

    def test_linux_hotkey_manager_type_text_slow_and_fast(self):
        from unittest.mock import MagicMock
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        manager = LinuxHotkeyManager(MagicMock(), MagicMock())
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()
        try:
            # Slow mode
            assert manager.type_text("Hi", fast=False) is True
            assert manager.virtual_keyboard.emit.call_count > 0

            # Fast mode with Unicode quotes normalization
            manager.virtual_keyboard.reset_mock()
            assert manager.type_text("“Hello”", fast=True) is True
            assert manager.virtual_keyboard.emit.call_count > 0
        finally:
            manager.cleanup()

    def test_linux_hotkey_manager_unmappable_chars_fallback(self, monkeypatch):
        from unittest.mock import MagicMock
        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager
        LinuxClipboardSink = hal.load_backend("linux", "clipboard").LinuxClipboardSink
        manager = LinuxHotkeyManager(MagicMock(), MagicMock())
        manager.virtual_keyboard = MagicMock()
        manager.uinput = MagicMock()
        mock_sink_type = MagicMock(return_value=True)
        monkeypatch.setattr(LinuxClipboardSink, "type_text", mock_sink_type)
        try:
            assert manager.type_text("café", fast=True) is True
            assert mock_sink_type.called
        finally:
            manager.cleanup()



    def test_a_wedged_typing_helper_cannot_hang_the_pipeline(self, monkeypatch):
        """ydotool/xdotool get a finite leash.

        Without one, a dead ``ydotoold`` held the transcription thread until the
        app was killed; the clipboard copies beside them were already bounded.
        """
        monkeypatch.setattr(
            shutil, "which",
            lambda name: f"/usr/bin/{name}" if name in ("ydotool", "xdotool") else None,
        )
        timeouts = []

        def never_returns(args, **kwargs):
            timeouts.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 0))

        monkeypatch.setattr(subprocess, "run", never_returns)
        sink = hal.get_clipboard_sink(hal.LINUX)
        monkeypatch.setattr(sink, "copy_text", lambda text: False)  # no last resort either

        assert sink.type_text("hello there") is False
        assert len(timeouts) == 2, timeouts          # ydotool, then xdotool
        assert all(t and t > 0 for t in timeouts), timeouts


class TestDeviceScanFollowsTheBinds:
    """A device that emits a configured bind must be monitored.

    The scan required KEY_LEFTALT/RIGHTALT *and* KEY_LEFTSHIFT/RIGHTSHIFT before
    it would open a device at all -- a leftover from the fixed default chord. A
    foot pedal or macropad that emits only KEY_F13 was never opened, so binding it
    silently did nothing even though ``set_binds`` reported success.
    """

    class _FakeDevice:
        def __init__(self, caps, path="/dev/input/event9", name="pedal"):
            self._caps = caps
            self.path = path
            self.name = name

        def capabilities(self):
            return {1: self._caps}  # EV_KEY == 1

    def _manager(self, binds):
        from unittest.mock import MagicMock as MM

        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager  # noqa: N806
        manager = LinuxHotkeyManager(MM(), MM())
        assert manager.set_binds(binds) is True
        return manager

    def test_a_pedal_with_only_the_bound_key_is_monitored(self):
        from voice_transcriber import keybinds

        manager = self._manager(["f13"])
        device = self._FakeDevice(list(keybinds.EVDEV_CODES["f13"]))

        assert manager._is_keyboard_device(device) is True

    def test_a_device_with_no_bound_key_and_no_modifiers_is_ignored(self):
        manager = self._manager(["f13"])
        device = self._FakeDevice([30])  # KEY_A only

        assert manager._is_keyboard_device(device) is False

    def test_the_default_chord_still_recognises_a_normal_keyboard(self):
        manager = self._manager(["alt+shift"])
        device = self._FakeDevice([56, 42])  # KEY_LEFTALT, KEY_LEFTSHIFT

        assert manager._is_keyboard_device(device) is True


class TestSetBindsRefusesWhatItCannotHonour:
    """`set_binds` must report failure, not save a chord that can never fire.

    AGENTS.md: "a backend that cannot honour a change returns ``False`` from
    ``set_binds`` rather than silently keeping the old chord". The WSL and macOS
    backends each pinned their half of that; the Linux one had no test, so the
    mutation that made it return ``True`` on an unparseable chord survived the
    whole suite. It matters more here than anywhere: `vt hotkey add` reports the
    result to the user, and a "saved" chord that never fires is indistinguishable
    from a broken keyboard.
    """

    def _manager(self):
        from unittest.mock import MagicMock as MM

        LinuxHotkeyManager = hal.load_backend("linux", "hotkeys").LinuxHotkeyManager  # noqa: N806
        manager = LinuxHotkeyManager(MM(), MM())
        assert manager.set_binds(["alt+shift"]) is True
        return manager

    def test_an_unknown_key_is_refused_and_the_old_bind_stays(self):
        manager = self._manager()
        assert manager.set_binds(["hyper+shift"]) is False
        assert [bind.chord for bind in manager.binds] == ["alt+shift"]

    def test_an_unparseable_value_is_refused_without_raising(self):
        manager = self._manager()
        assert manager.set_binds(12345) is False
        assert [bind.chord for bind in manager.binds] == ["alt+shift"]
