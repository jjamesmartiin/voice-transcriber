"""Tests for hotkeys_windows.py - Windows global hotkeys (pynput+keyboard)."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


class FakeKey:
    """Hashable fake key object for pynput Key enum simulation."""
    def __init__(self, name):
        self._name = name
    def __repr__(self):
        return f"Key.{self._name}"
    def __hash__(self):
        return hash(self._name)
    def __eq__(self, other):
        return isinstance(other, FakeKey) and self._name == other._name


def make_mock_pynput():
    """Create mock pynput module hierarchy with hashable Key objects."""
    # Create hashable key instances
    alt_l = FakeKey("alt_l")
    alt_r = FakeKey("alt_r")
    shift_l = FakeKey("shift_l")
    shift_r = FakeKey("shift_r")
    ctrl_l = FakeKey("ctrl_l")
    ctrl_r = FakeKey("ctrl_r")

    # Build a Key namespace object
    mock_key = type("Key", (), {
        "alt_l": alt_l, "alt_r": alt_r,
        "shift_l": shift_l, "shift_r": shift_r,
        "ctrl_l": ctrl_l, "ctrl_r": ctrl_r,
    })()

    mock_keycode = type("KeyCode", (), {"from_char": staticmethod(lambda c: MagicMock())})

    mock_listener = MagicMock()
    mock_listener_cls = MagicMock(return_value=mock_listener)

    mock_keyboard = MagicMock()
    mock_keyboard.Key = mock_key
    mock_keyboard.KeyCode = mock_keycode
    mock_keyboard.Listener = mock_listener_cls

    mock_pynput = MagicMock()
    mock_pynput.keyboard = mock_keyboard

    return mock_pynput, mock_keyboard, mock_key, mock_listener


def make_mock_kb_lib():
    """Create mock keyboard library."""
    mock_kb = MagicMock()
    mock_kb.is_pressed.return_value = False
    return mock_kb


@pytest.fixture(autouse=True)
def reset_module():
    for mod_name in list(sys.modules.keys()):
        if "hotkeys" in mod_name and "test" not in mod_name:
            del sys.modules[mod_name]
    yield


@pytest.fixture
def win_hotkeys():
    """Set up mocked pynput/keyboard and return module + instance."""
    mock_pynput, mock_keyboard, mock_key, mock_listener = make_mock_pynput()
    mock_kb_lib = make_mock_kb_lib()

    with patch.dict(sys.modules, {
        "pynput": mock_pynput,
        "pynput.keyboard": mock_keyboard,
        "keyboard": mock_kb_lib,
    }):
        import hotkeys_windows
        cb_start = MagicMock()
        cb_stop = MagicMock()
        cb_config = MagicMock()
        hk = hotkeys_windows.WindowsGlobalHotkeys(cb_start, cb_stop, cb_config)
        yield hotkeys_windows, hk, cb_start, cb_stop, cb_config, mock_key, mock_kb_lib, mock_listener


class TestInit:
    def test_initial_state(self, win_hotkeys):
        _, hk, _, _, _, _, _, _ = win_hotkeys
        assert hk.hotkey_active is False
        assert hk.running is False
        assert len(hk.pressed_keys) == 0

    def test_listener_started(self, win_hotkeys):
        _, hk, _, _, _, _, _, mock_listener = win_hotkeys
        mock_listener.start.assert_called_once()

    def test_devices_set(self, win_hotkeys):
        _, hk, _, _, _, _, _, _ = win_hotkeys
        assert hk.devices == [True]


class TestMainHotkey:
    def test_alt_shift_detected(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk.pressed_keys = {mock_key.alt_l, mock_key.shift_l}
        assert hk._is_main_hotkey_pressed() is True

    def test_right_alt_shift(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk.pressed_keys = {mock_key.alt_r, mock_key.shift_r}
        assert hk._is_main_hotkey_pressed() is True

    def test_only_alt(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk.pressed_keys = {mock_key.alt_l}
        assert hk._is_main_hotkey_pressed() is False

    def test_only_shift(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk.pressed_keys = {mock_key.shift_l}
        assert hk._is_main_hotkey_pressed() is False

    def test_empty_keys(self, win_hotkeys):
        _, hk, _, _, _, _, _, _ = win_hotkeys
        hk.pressed_keys = set()
        assert hk._is_main_hotkey_pressed() is False


class TestOnPress:
    def test_triggers_start_on_hotkey(self, win_hotkeys):
        _, hk, cb_start, _, _, mock_key, _, _ = win_hotkeys
        hk._on_press(mock_key.alt_l)
        hk._on_press(mock_key.shift_l)
        cb_start.assert_called_once()
        assert hk.hotkey_active is True

    def test_no_double_activation(self, win_hotkeys):
        _, hk, cb_start, _, _, mock_key, _, _ = win_hotkeys
        hk._on_press(mock_key.alt_l)
        hk._on_press(mock_key.shift_l)
        hk._on_press(mock_key.shift_r)  # Another shift while active
        assert cb_start.call_count == 1

    def test_tracks_pressed_keys(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk._on_press(mock_key.alt_l)
        assert mock_key.alt_l in hk.pressed_keys


class TestOnRelease:
    def test_triggers_stop_on_release(self, win_hotkeys):
        _, hk, _, cb_stop, _, mock_key, _, _ = win_hotkeys
        # Activate first
        hk._on_press(mock_key.alt_l)
        hk._on_press(mock_key.shift_l)
        # Release
        hk._on_release(mock_key.alt_l)
        cb_stop.assert_called_once()
        assert hk.hotkey_active is False

    def test_removes_key_from_pressed(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk._on_press(mock_key.alt_l)
        hk._on_release(mock_key.alt_l)
        assert mock_key.alt_l not in hk.pressed_keys

    def test_no_stop_if_not_active(self, win_hotkeys):
        _, hk, _, cb_stop, _, mock_key, _, _ = win_hotkeys
        hk._on_press(mock_key.alt_l)
        hk._on_release(mock_key.alt_l)
        cb_stop.assert_not_called()


class TestModifiers:
    def test_modifiers_pressed(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk.pressed_keys = {mock_key.ctrl_l}
        assert hk.are_modifiers_pressed() is True

    def test_no_modifiers(self, win_hotkeys):
        _, hk, _, _, _, _, _, _ = win_hotkeys
        hk.pressed_keys = set()
        assert hk.are_modifiers_pressed() is False


class TestTypeText:
    def test_delegates_to_keyboard_write(self, win_hotkeys):
        _, hk, _, _, _, _, mock_kb_lib, _ = win_hotkeys
        result = hk.type_text("hello world")
        assert result is True
        mock_kb_lib.write.assert_called_once_with("hello world")

    def test_error_returns_false(self, win_hotkeys):
        _, hk, _, _, _, _, mock_kb_lib, _ = win_hotkeys
        mock_kb_lib.write.side_effect = Exception("keyboard error")
        result = hk.type_text("test")
        assert result is False


class TestStop:
    def test_stop_clears_state(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, mock_listener = win_hotkeys
        hk.running = True
        hk.pressed_keys = {mock_key.alt_l}
        hk.stop()
        assert hk.running is False
        assert len(hk.pressed_keys) == 0


class TestConfigHotkey:
    def test_config_hotkey_triggered(self, win_hotkeys):
        _, hk, _, _, cb_config, _, mock_kb_lib, _ = win_hotkeys
        # Simulate Ctrl+Alt+I detection
        mock_kb_lib.is_pressed.return_value = True
        mock_event = MagicMock()
        mock_event.name = "i"
        hk._on_config_press(mock_event)
        cb_config.assert_called_once()

    def test_config_hotkey_no_callback(self, win_hotkeys):
        _, hk, _, _, _, _, mock_kb_lib, _ = win_hotkeys
        hk.callback_config = None
        mock_kb_lib.is_pressed.return_value = True
        mock_event = MagicMock()
        mock_event.name = "i"
        hk._on_config_press(mock_event)  # Should not error

    def test_config_hotkey_wrong_key(self, win_hotkeys):
        _, hk, _, _, cb_config, _, mock_kb_lib, _ = win_hotkeys
        mock_kb_lib.is_pressed.return_value = True
        mock_event = MagicMock()
        mock_event.name = "j"
        hk._on_config_press(mock_event)
        cb_config.assert_not_called()


class TestCopyToClipboardMode:
    def test_ctrl_sets_clipboard_mode(self, win_hotkeys):
        _, hk, _, _, _, mock_key, _, _ = win_hotkeys
        hk.pressed_keys = set()
        hk._on_press(mock_key.ctrl_l)
        hk._on_press(mock_key.alt_l)
        hk._on_press(mock_key.shift_l)
        assert hk.copy_to_clipboard_mode is True

    def test_stop_passes_clipboard_mode(self, win_hotkeys):
        _, hk, _, cb_stop, _, mock_key, _, _ = win_hotkeys
        hk._on_press(mock_key.alt_l)
        hk._on_press(mock_key.shift_l)
        hk._on_release(mock_key.alt_l)
        call_kwargs = cb_stop.call_args[1]
        assert "copy_to_clipboard" in call_kwargs


class TestListenerFailure:
    def test_listener_start_failure(self, win_hotkeys):
        mod, _, _, _, _, _, _, _ = win_hotkeys
        mock_pynput, mock_keyboard, mock_key, mock_listener = make_mock_pynput()
        mock_kb_lib = make_mock_kb_lib()
        mock_listener.start.side_effect = Exception("no access")

        # Clear module to reimport
        for mod_name in list(sys.modules.keys()):
            if "hotkeys" in mod_name and "test" not in mod_name:
                del sys.modules[mod_name]

        with patch.dict(sys.modules, {
            "pynput": mock_pynput,
            "pynput.keyboard": mock_keyboard,
            "keyboard": mock_kb_lib,
        }):
            import hotkeys_windows
            hk = hotkeys_windows.WindowsGlobalHotkeys(MagicMock(), MagicMock())
            assert hk.devices == []


class TestOnPressError:
    def test_press_exception_caught(self, win_hotkeys):
        _, hk, cb_start, _, _, _, _, _ = win_hotkeys
        cb_start.side_effect = Exception("callback error")
        # Should not raise even if callback errors
        hk._on_press(MagicMock())  # Non-hotkey key, just adds to set

    def test_release_exception_caught(self, win_hotkeys):
        _, hk, _, cb_stop, _, mock_key, _, _ = win_hotkeys
        # Activate hotkey
        hk._on_press(mock_key.alt_l)
        hk._on_press(mock_key.shift_l)
        # Make stop callback error
        cb_stop.side_effect = Exception("stop error")
        hk._on_release(mock_key.alt_l)  # Should not raise


class TestRunLoop:
    def test_run_returns_true(self, win_hotkeys):
        _, hk, _, _, _, _, _, _ = win_hotkeys
        import threading
        def stop_soon():
            import time
            time.sleep(0.15)
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()
        result = hk.run()
        t.join()
        assert result is True


class TestAlias:
    def test_wayland_alias(self, win_hotkeys):
        mod, _, _, _, _, _, _, _ = win_hotkeys
        assert mod.WaylandGlobalHotkeys is mod.WindowsGlobalHotkeys
