"""Tests for hotkeys.py - Linux Wayland global hotkeys (evdev+uinput)."""
import sys
import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace

SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


def make_mock_evdev():
    """Create a mock evdev module with realistic ecodes."""
    mock_evdev = MagicMock()
    # EV_KEY event type
    mock_evdev.ecodes.EV_KEY = 1
    # Key codes matching the real values
    mock_evdev.ecodes.KEY_A = 30
    mock_evdev.ecodes.KEY_B = 48
    mock_evdev.ecodes.KEY_C = 46
    mock_evdev.ecodes.KEY_Q = 16
    mock_evdev.ecodes.KEY_W = 17
    mock_evdev.ecodes.KEY_E = 18
    mock_evdev.ecodes.KEY_LEFTALT = 56
    mock_evdev.ecodes.KEY_RIGHTALT = 100
    mock_evdev.ecodes.KEY_LEFTSHIFT = 42
    mock_evdev.ecodes.KEY_RIGHTSHIFT = 54
    mock_evdev.ecodes.KEY_LEFTCTRL = 29
    mock_evdev.ecodes.KEY_RIGHTCTRL = 97
    mock_evdev.ecodes.KEY_SPACE = 57
    mock_evdev.ecodes.KEY_ENTER = 28
    mock_evdev.list_devices.return_value = []
    return mock_evdev


def make_mock_uinput():
    """Create a mock uinput module with key constants."""
    mock_uinput = MagicMock()
    # Add KEY_ attributes that dir() should return
    key_attrs = [
        "KEY_A", "KEY_B", "KEY_C", "KEY_D", "KEY_E", "KEY_F", "KEY_G",
        "KEY_H", "KEY_I", "KEY_J", "KEY_K", "KEY_L", "KEY_M", "KEY_N",
        "KEY_O", "KEY_P", "KEY_Q", "KEY_R", "KEY_S", "KEY_T", "KEY_U",
        "KEY_V", "KEY_W", "KEY_X", "KEY_Y", "KEY_Z",
        "KEY_1", "KEY_2", "KEY_3", "KEY_4", "KEY_5", "KEY_6",
        "KEY_7", "KEY_8", "KEY_9", "KEY_0",
        "KEY_SPACE", "KEY_DOT", "KEY_COMMA", "KEY_SLASH",
        "KEY_ENTER", "KEY_TAB", "KEY_MINUS", "KEY_EQUAL",
        "KEY_SEMICOLON", "KEY_APOSTROPHE", "KEY_LEFTBRACE", "KEY_RIGHTBRACE",
        "KEY_BACKSLASH", "KEY_GRAVE", "KEY_LEFTSHIFT",
    ]
    for attr in key_attrs:
        setattr(mock_uinput, attr, MagicMock())

    mock_device = MagicMock()
    mock_uinput.Device.return_value = mock_device
    return mock_uinput


@pytest.fixture(autouse=True)
def reset_module():
    for mod_name in list(sys.modules.keys()):
        if "hotkeys" in mod_name and "test" not in mod_name:
            del sys.modules[mod_name]
    yield


@pytest.fixture
def hotkeys_setup():
    """Set up mocked evdev/uinput and return hotkeys module + instance."""
    mock_evdev = make_mock_evdev()
    mock_uinput = make_mock_uinput()

    with patch.dict(sys.modules, {"evdev": mock_evdev, "uinput": mock_uinput}):
        import hotkeys
        cb_start = MagicMock()
        cb_stop = MagicMock()
        cb_config = MagicMock()
        hk = hotkeys.WaylandGlobalHotkeys(cb_start, cb_stop, cb_config)
        yield hotkeys, hk, cb_start, cb_stop, cb_config, mock_evdev, mock_uinput


def make_key_event(evdev_mock, code, value):
    """Create a fake key event. value: 1=press, 0=release, 2=repeat."""
    ev = SimpleNamespace(type=evdev_mock.ecodes.EV_KEY, code=code, value=value)
    return ev


class TestInit:
    def test_key_codes_set(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        assert hk.ALT_KEYS == [56, 100]
        assert hk.SHIFT_KEYS == [42, 54]
        assert hk.CTRL_KEYS == [29, 97]
        assert hk.KEY_I == [23]

    def test_initial_state(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        assert hk.hotkey_active is False
        assert hk.running is False
        assert hk.key_states == {}

    def test_virtual_keyboard_created(self, hotkeys_setup):
        _, hk, _, _, _, _, mock_uinput = hotkeys_setup
        mock_uinput.Device.assert_called_once()
        assert hk.virtual_keyboard is not None


class TestHotkeyDetection:
    def test_alt_shift_pressed(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {56: True, 42: True}  # Left Alt + Left Shift
        assert hk.is_hotkey_pressed() is True

    def test_right_alt_right_shift(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {100: True, 54: True}  # Right Alt + Right Shift
        assert hk.is_hotkey_pressed() is True

    def test_only_alt_not_enough(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {56: True}
        assert hk.is_hotkey_pressed() is False

    def test_only_shift_not_enough(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {42: True}
        assert hk.is_hotkey_pressed() is False

    def test_hotkey_released(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {56: False, 42: True}
        assert hk.is_hotkey_released() is True

    def test_hotkey_not_released(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {56: True, 42: True}
        assert hk.is_hotkey_released() is False


class TestConfigHotkey:
    def test_ctrl_alt_i_detected(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {29: True, 56: True, 23: True}  # Ctrl+Alt+I
        assert hk.is_config_hotkey_pressed() is True

    def test_missing_ctrl(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {56: True, 23: True}
        assert hk.is_config_hotkey_pressed() is False

    def test_missing_i(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {29: True, 56: True}
        assert hk.is_config_hotkey_pressed() is False


class TestHandleKeyEvent:
    def test_press_updates_state(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        event = make_key_event(mock_evdev, 56, 1)  # Press Left Alt
        hk.handle_key_event(event)
        assert hk.key_states[56] is True

    def test_release_updates_state(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        hk.key_states[56] = True
        event = make_key_event(mock_evdev, 56, 0)  # Release Left Alt
        hk.handle_key_event(event)
        assert hk.key_states[56] is False

    def test_repeat_ignored(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        hk.key_states[56] = True
        event = make_key_event(mock_evdev, 56, 2)  # Repeat
        hk.handle_key_event(event)
        assert hk.key_states[56] is True  # Unchanged

    def test_hotkey_triggers_start(self, hotkeys_setup):
        _, hk, cb_start, _, _, mock_evdev, _ = hotkeys_setup
        # Press Alt
        hk.handle_key_event(make_key_event(mock_evdev, 56, 1))
        # Press Shift -> triggers start
        hk.handle_key_event(make_key_event(mock_evdev, 42, 1))
        cb_start.assert_called_once()
        assert hk.hotkey_active is True

    def test_hotkey_release_triggers_stop(self, hotkeys_setup):
        _, hk, cb_start, cb_stop, _, mock_evdev, _ = hotkeys_setup
        # Activate
        hk.handle_key_event(make_key_event(mock_evdev, 56, 1))
        hk.handle_key_event(make_key_event(mock_evdev, 42, 1))
        # Release Alt
        hk.handle_key_event(make_key_event(mock_evdev, 56, 0))
        cb_stop.assert_called_once()
        assert hk.hotkey_active is False

    def test_config_hotkey_triggers_callback(self, hotkeys_setup):
        _, hk, _, _, cb_config, mock_evdev, _ = hotkeys_setup
        hk.key_states = {29: True, 56: True}  # Ctrl+Alt already held
        hk.handle_key_event(make_key_event(mock_evdev, 23, 1))  # Press I
        cb_config.assert_called_once()

    def test_non_key_event_ignored(self, hotkeys_setup):
        _, hk, cb_start, _, _, _, _ = hotkeys_setup
        event = SimpleNamespace(type=3, code=0, value=100)  # EV_ABS, not EV_KEY
        hk.handle_key_event(event)
        cb_start.assert_not_called()

    def test_double_activation_prevented(self, hotkeys_setup):
        _, hk, cb_start, _, _, mock_evdev, _ = hotkeys_setup
        hk.handle_key_event(make_key_event(mock_evdev, 56, 1))
        hk.handle_key_event(make_key_event(mock_evdev, 42, 1))
        # Try to activate again (repeat events)
        hk.handle_key_event(make_key_event(mock_evdev, 42, 1))
        assert cb_start.call_count == 1


class TestModifiers:
    def test_ctrl_pressed(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {29: True}
        assert hk.is_ctrl_pressed() is True

    def test_ctrl_not_pressed(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {}
        assert hk.is_ctrl_pressed() is False

    def test_any_modifiers_pressed(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {42: True}  # Just shift
        assert hk.are_modifiers_pressed() is True

    def test_no_modifiers_pressed(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.key_states = {}
        assert hk.are_modifiers_pressed() is False


class TestTypeText:
    def test_type_simple_text(self, hotkeys_setup):
        _, hk, _, _, _, _, mock_uinput = hotkeys_setup
        result = hk.type_text("hi")
        assert result is True
        # Should have emitted key presses
        assert hk.virtual_keyboard.emit.call_count > 0

    def test_type_uppercase(self, hotkeys_setup):
        _, hk, _, _, _, _, mock_uinput = hotkeys_setup
        result = hk.type_text("A")
        assert result is True
        # Should include shift press/release
        calls = hk.virtual_keyboard.emit.call_args_list
        # At least 4 calls: shift down, key down, key up, shift up
        assert len(calls) >= 4

    def test_type_unknown_char_skipped(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        # Unicode chars not in key_map should be skipped without error
        result = hk.type_text("\u00e9")  # accented e
        assert result is True

    def test_type_empty_string(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        result = hk.type_text("")
        assert result is True

    def test_type_without_virtual_keyboard(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.virtual_keyboard = None
        result = hk.type_text("hello")
        assert result is False


class TestTypeTextError:
    def test_type_text_emit_exception(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.virtual_keyboard.emit.side_effect = Exception("emit failed")
        result = hk.type_text("a")
        assert result is False


class TestInitFailures:
    def test_init_without_evdev(self):
        """Test that init handles missing evdev gracefully."""
        mock_evdev = make_mock_evdev()
        mock_uinput = make_mock_uinput()

        with patch.dict(sys.modules, {"evdev": mock_evdev, "uinput": mock_uinput}):
            import hotkeys
            cb_start = MagicMock()
            cb_stop = MagicMock()
            # Test virtual keyboard creation failure
            mock_uinput.Device.side_effect = Exception("no uinput")
            hk = hotkeys.WaylandGlobalHotkeys(cb_start, cb_stop)
            assert hk.virtual_keyboard is None


class TestStop:
    def test_stop_clears_state(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.running = True
        hk.key_states = {56: True}
        hk.stop()
        assert hk.running is False
        assert hk.key_states == {}
        assert hk.devices == []

    def test_stop_closes_devices(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        mock_dev = MagicMock()
        hk.devices = [mock_dev]
        hk.stop()
        mock_dev.close.assert_called_once()

    def test_stop_handles_close_error(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        mock_dev = MagicMock()
        mock_dev.close.side_effect = Exception("close error")
        hk.devices = [mock_dev]
        hk.stop()  # Should not raise
        assert hk.devices == []

    def test_stop_destroys_virtual_keyboard(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        vk = hk.virtual_keyboard
        hk.stop()
        vk.destroy.assert_called_once()


class TestRunLoopEdgeCases:
    def test_run_with_no_fd_devices(self, hotkeys_setup):
        """Test run loop when devices have None fd (invalid)."""
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        import threading

        mock_device = MagicMock()
        mock_device.fd = None  # Invalid fd
        mock_device.path = "/dev/input/event0"
        hk.devices = [mock_device]

        def stop_soon():
            import time
            time.sleep(0.15)
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()
        result = hk.run()
        t.join()
        # Devices with None fd should be cleared
        assert result is True

    def test_run_event_loop_error(self, hotkeys_setup):
        """Test run loop handles generic exceptions."""
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        import threading

        def stop_soon():
            import time
            time.sleep(0.3)
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()

        mock_device = MagicMock()
        mock_device.fd = 42
        mock_device.path = "/dev/input/event0"
        mock_device.read.side_effect = RuntimeError("unexpected")
        hk.devices = [mock_device]

        with patch("select.select", return_value=([42], [], [])):
            result = hk.run()
        t.join()

    def test_run_scan_finds_devices(self, hotkeys_setup):
        """Test that periodic scan picks up new devices."""
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        import threading

        hk.devices = []  # Start with no devices

        def stop_soon():
            import time
            time.sleep(0.8)  # Wait past scan interval check
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()

        with patch.object(hk, "scan_for_devices", return_value=False):
            result = hk.run()
        t.join()
        assert result is True


class TestDeviceDetection:
    def test_is_keyboard_device(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        mock_device = MagicMock()
        mock_device.capabilities.return_value = {
            mock_evdev.ecodes.EV_KEY: [
                mock_evdev.ecodes.KEY_A,
                mock_evdev.ecodes.KEY_LEFTALT,
                mock_evdev.ecodes.KEY_RIGHTALT,
                mock_evdev.ecodes.KEY_LEFTSHIFT,
                mock_evdev.ecodes.KEY_RIGHTSHIFT,
                mock_evdev.ecodes.KEY_SPACE,
            ]
        }
        assert hk._is_keyboard_device(mock_device) is True

    def test_non_keyboard_device_rejected(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        mock_device = MagicMock()
        # No EV_KEY capability
        mock_device.capabilities.return_value = {}
        assert hk._is_keyboard_device(mock_device) is False

    def test_device_missing_alt(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        mock_device = MagicMock()
        mock_device.capabilities.return_value = {
            mock_evdev.ecodes.EV_KEY: [
                mock_evdev.ecodes.KEY_A,
                mock_evdev.ecodes.KEY_LEFTSHIFT,
                mock_evdev.ecodes.KEY_RIGHTSHIFT,
            ]
        }
        assert hk._is_keyboard_device(mock_device) is False

    def test_device_capabilities_exception(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        mock_device = MagicMock()
        mock_device.capabilities.side_effect = Exception("device error")
        assert hk._is_keyboard_device(mock_device) is False

    def test_scan_for_devices_finds_new(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        hk.devices = []  # Clear existing
        mock_device = MagicMock()
        mock_device.capabilities.return_value = {
            mock_evdev.ecodes.EV_KEY: [
                mock_evdev.ecodes.KEY_A,
                mock_evdev.ecodes.KEY_LEFTALT,
                mock_evdev.ecodes.KEY_RIGHTALT,
                mock_evdev.ecodes.KEY_LEFTSHIFT,
                mock_evdev.ecodes.KEY_RIGHTSHIFT,
                mock_evdev.ecodes.KEY_SPACE,
            ]
        }
        mock_evdev.list_devices.return_value = ["/dev/input/event0"]
        mock_evdev.InputDevice.return_value = mock_device
        with patch("glob.glob", return_value=[]):
            result = hk.scan_for_devices()
        assert result is True
        assert len(hk.devices) == 1

    def test_scan_no_new_devices(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        mock_evdev.list_devices.return_value = []
        with patch("glob.glob", return_value=[]):
            result = hk.scan_for_devices()
        assert result is False

    def test_scan_permission_error(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        hk.devices = []
        mock_evdev.list_devices.return_value = ["/dev/input/event0"]
        mock_evdev.InputDevice.side_effect = PermissionError("no access")
        with patch("glob.glob", return_value=[]):
            result = hk.scan_for_devices()
        assert result is False


class TestRunLoop:
    def test_run_stops_when_running_false(self, hotkeys_setup):
        _, hk, _, _, _, _, _ = hotkeys_setup
        hk.devices = []
        # Set running to False after a brief delay
        import threading
        def stop_soon():
            import time
            time.sleep(0.1)
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()
        result = hk.run()
        t.join()
        assert result is True

    def test_run_with_device_events(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        import threading

        mock_device = MagicMock()
        mock_device.fd = 42
        mock_device.path = "/dev/input/event0"
        mock_device.read.return_value = []
        hk.devices = [mock_device]

        def stop_soon():
            import time
            time.sleep(0.15)
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()

        with patch("select.select", return_value=([42], [], [])):
            result = hk.run()
        t.join()
        assert result is True

    def test_run_handles_device_disconnect(self, hotkeys_setup):
        _, hk, _, _, _, mock_evdev, _ = hotkeys_setup
        import threading

        mock_device = MagicMock()
        mock_device.fd = 42
        mock_device.path = "/dev/input/event0"
        mock_device.name = "Test KB"
        mock_device.read.side_effect = OSError(19, "No such device")
        hk.devices = [mock_device]

        def stop_soon():
            import time
            time.sleep(0.15)
            hk.running = False
        t = threading.Thread(target=stop_soon)
        t.start()

        with patch("select.select", return_value=([42], [], [])):
            result = hk.run()
        t.join()
        assert mock_device not in hk.devices
