#!/usr/bin/env python3
"""
Unit tests for VoiceTranscriberTUI module with color theme customization
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

from tui import VoiceTranscriberTUI, detect_system_theme_color, COLOR_PALETTES

def test_tui_initialization():
    tui = VoiceTranscriberTUI(app_version="1.0.0")
    assert tui.state == "READY"
    assert tui.model_backend == "cohere"
    assert tui.is_muted is True
    assert tui.auto_type is False
    assert tui.transcription_count == 0
    assert tui.ui_theme == "auto"

def test_tui_state_updates():
    tui = VoiceTranscriberTUI()
    tui.set_active_device("Test Mic")
    assert tui.active_device == "Test Mic"

    tui.set_config_state(backend="cohere", muted=False, auto_type=True, sound_theme="proximity", ui_theme="cyan")
    assert tui.model_backend == "cohere"
    assert tui.is_muted is False
    assert tui.auto_type is True
    assert tui.ui_theme == "cyan"

    tui.update_state("RECORDING", "Testing")
    assert tui.state == "RECORDING"
    assert tui.sub_state_text == "Testing"

    tui.update_vu_level(0.85)
    assert tui.vu_level == 0.85

def test_tui_theme_color_customization():
    tui = VoiceTranscriberTUI(ui_theme="auto")
    eff_auto = tui.get_effective_color()
    assert eff_auto in COLOR_PALETTES

    tui.set_ui_theme("magenta")
    assert tui.ui_theme == "magenta"
    assert tui.get_effective_color() == "magenta"

    tui.set_ui_theme("blue")
    assert tui.get_effective_color() == "blue"

    cycled = tui.cycle_ui_theme()
    assert cycled in COLOR_PALETTES

def test_tui_render_single_line_status_bar():
    tui = VoiceTranscriberTUI(ui_theme="cyan")
    tui.set_active_device("Mock USB Microphone")
    tui.update_state("READY")
    bar_ready = tui._render_status_bar()
    assert bar_ready is not None

    tui.update_state("RECORDING")
    tui.update_vu_level(0.6)
    bar_rec = tui._render_status_bar()
    assert bar_rec is not None

    tui.update_state("PROCESSING")
    bar_proc = tui._render_status_bar()
    assert bar_proc is not None

def test_tui_print_transcription():
    tui = VoiceTranscriberTUI(ui_theme="yellow")
    # Test printing transcription to console scrollback with incremental counter
    tui.print_transcription(
        text="This is test transcription #1 for unit tests.",
        elapsed_sec=0.75,
        copy_success=True,
        typed_success=True,
        device_name="Mock Mic"
    )
    assert tui.transcription_count == 1

    tui.print_transcription(
        text="This is test transcription #2 for unit tests.",
        elapsed_sec=1.10,
        copy_success=True,
        typed_success=False,
        device_name="Mock Mic",
        rec_duration=4.20
    )
    assert tui.transcription_count == 2

    # Capture output to ensure rec:, proc:, and ready: are present in top_rule
    tui.console.record = True
    tui.print_transcription(
        text="Testing three timing output.",
        elapsed_sec=0.45,
        rec_duration=3.20,
        proc_time=0.38
    )
    output = tui.console.export_text()
    assert "rec: 3.20s" in output
    assert "proc: 0.38s" in output
    assert "ready: 0.45s" in output

    tui.print_event("Test Event", "Settings updated cleanly", level="info")
    tui.print_warning("Test Warning", "Microphone level low")
    tui.print_error("Test Error", "Audio stream interrupted")

def test_tui_handle_keypress():
    tui = VoiceTranscriberTUI()
    called = []
    tui.on_change_device = lambda: called.append("mic")
    tui.on_toggle_mute = lambda: called.append("mute")
    tui.on_toggle_autotype = lambda: called.append("clipboard")
    tui.on_cycle_theme = lambda: called.append("theme")
    tui.on_toggle_trailing_space = lambda: called.append("space")
    tui.on_cycle_punctuation = lambda: called.append("punctuation")

    tui._handle_keypress('M')
    assert called == ["mic"]

    tui._handle_keypress('m')
    assert called == ["mic", "mute"]

    tui._handle_keypress('c')
    assert called == ["mic", "mute", "clipboard"]

    tui._handle_keypress('s')
    assert called == ["mic", "mute", "clipboard", "space"]

    tui._handle_keypress('p')
    assert called == ["mic", "mute", "clipboard", "space", "punctuation"]

    tui._handle_keypress('t')
    assert called == ["mic", "mute", "clipboard", "space", "punctuation", "theme"]

    tui.on_open_settings_picker = lambda: called.append("settings")
    tui._handle_keypress(',')
    assert called[-1] == "settings"
    tui._handle_keypress('S')
    assert called[-1] == "settings"


def test_tui_event_dividing_lines_follow_theme():
    tui = VoiceTranscriberTUI(ui_theme="cyan")
    assert tui.get_effective_color() == "cyan"

    printed_items = []
    original_print = tui.console.print
    tui.console.print = lambda item: printed_items.append(item)

    try:
        # 1. Model Ready (Success) must follow chosen theme (cyan)
        printed_items.clear()
        tui.print_event("✅ Model Ready", "Loaded in 1.2s", level="success")
        top, msg, bot = printed_items
        assert "bold cyan" in str(top.spans[0].style)
        assert "cyan" in str(bot.spans[0].style)

        # 2. Trailing Space (Info) must follow chosen theme (cyan)
        printed_items.clear()
        tui.print_event("␣ Trailing Space", "Auto-type trailing space enabled", level="info")
        top, msg, bot = printed_items
        assert "bold cyan" in str(top.spans[0].style)
        assert "cyan" in str(bot.spans[0].style)

        # 3. No Speech Detected (Warning) must follow chosen theme (cyan)
        printed_items.clear()
        tui.print_warning("No Speech Detected", "No audio detected")
        top, msg, bot = printed_items
        assert "bold cyan" in str(top.spans[0].style)
        assert "cyan" in str(bot.spans[0].style)

        # 4. Error stays red
        printed_items.clear()
        tui.print_error("Model Load Failed", "Could not connect")
        top, msg, bot = printed_items
        assert "bold red" in str(top.spans[0].style)
        assert "red" in str(bot.spans[0].style)

        # 5. Switch to Magenta theme and verify Model Ready and Warning follow Magenta
        tui.set_ui_theme("magenta")
        assert tui.get_effective_color() == "magenta"

        printed_items.clear()
        tui.print_event("✅ Model Ready", "Model loaded", level="success")
        top, msg, bot = printed_items
        assert "bold magenta" in str(top.spans[0].style)
        assert "magenta" in str(bot.spans[0].style)

        printed_items.clear()
        tui.print_warning("No Speech Detected", "No audio detected")
        top, msg, bot = printed_items
        assert "bold magenta" in str(top.spans[0].style)
        assert "magenta" in str(bot.spans[0].style)
    finally:
        tui.console.print = original_print


def test_theme_picker_key_dispatch():
    tui = VoiceTranscriberTUI()
    called = []
    tui.on_open_theme_picker = lambda: called.append("picker")
    tui._handle_keypress('t')
    assert called == ["picker"]

    called.clear()
    tui._handle_keypress('T')
    assert called == ["picker"]

    import t2
    assert hasattr(t2, 'select_theme_picker')


def test_settings_and_mic_picker_key_dispatch():
    tui = VoiceTranscriberTUI()
    called = []
    tui.on_open_settings_picker = lambda: called.append("settings")
    tui.on_open_mic_picker = lambda: called.append("mic_picker")

    # Settings triggers: S, ,, i, I
    tui._handle_keypress('S')
    assert called[-1] == "settings"

    tui._handle_keypress(',')
    assert called[-1] == "settings"

    tui._handle_keypress('i')
    assert called[-1] == "settings"

    tui._handle_keypress('I')
    assert called[-1] == "settings"

    # Mic trigger: M
    tui._handle_keypress('M')
    assert called[-1] == "mic_picker"

    import t2
    assert hasattr(t2, 'select_settings_picker')
    assert hasattr(t2, 'select_microphone_picker')
    assert hasattr(t2, 'select_audio_device')


def test_score_setting_fuzzy_matching():
    import t2
    # Exact match
    assert t2._score_setting("trailing space", "Trailing Space", "auto type") == 1000
    # Prefix match
    assert t2._score_setting("trail", "Trailing Space", "auto type") > 700
    # Keyword match
    assert t2._score_setting("auto type", "Trailing Space", "trailing space auto type") is not None
    # Fuzzy subsequence match
    assert t2._score_setting("trsp", "Trailing Space") is not None
    # No match
    assert t2._score_setting("nonexistentqueryxyz", "Trailing Space", "kw") is None


def test_read_key_windows_simulation(monkeypatch):
    import t2
    # Simulate Windows platform
    monkeypatch.setattr(t2.sys, "platform", "win32")

    class FakeMsvcrt:
        def __init__(self, key_seq):
            self.seq = list(key_seq)

        def getwch(self):
            if self.seq:
                return self.seq.pop(0)
            return ''

    # Test arrow keys
    monkeypatch.setattr(t2, "sys", t2.sys)
    import sys
    fake_mod = FakeMsvcrt(['\xe0', 'H'])
    monkeypatch.setitem(sys.modules, "msvcrt", fake_mod)
    assert t2._read_key() == 'UP'

    fake_mod = FakeMsvcrt(['\xe0', 'P'])
    monkeypatch.setitem(sys.modules, "msvcrt", fake_mod)
    assert t2._read_key() == 'DOWN'

    fake_mod = FakeMsvcrt(['\r'])
    monkeypatch.setitem(sys.modules, "msvcrt", fake_mod)
    assert t2._read_key() == 'ENTER'

    fake_mod = FakeMsvcrt(['\x1b'])
    monkeypatch.setitem(sys.modules, "msvcrt", fake_mod)
    assert t2._read_key() == 'ESC'




