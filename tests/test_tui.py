#!/usr/bin/env python3
"""
Unit tests for VoiceTranscriberTUI module
"""

import sys
import os
import time
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from tui import VoiceTranscriberTUI

def test_tui_initialization():
    tui = VoiceTranscriberTUI(app_version="1.0.0")
    assert tui.state == "READY"
    assert tui.model_backend == "cohere"
    assert tui.is_muted is True
    assert tui.auto_type is False
    assert tui.transcription_count == 0

def test_tui_state_updates():
    tui = VoiceTranscriberTUI()
    tui.set_active_device("Test Mic")
    assert tui.active_device == "Test Mic"

    tui.set_config_state(backend="whisper", muted=False, auto_type=True, sound_theme="proximity")
    assert tui.model_backend == "whisper"
    assert tui.is_muted is False
    assert tui.auto_type is True

    tui.update_state("RECORDING", "Testing")
    assert tui.state == "RECORDING"
    assert tui.sub_state_text == "Testing"

    tui.update_vu_level(0.85)
    assert tui.vu_level == 0.85

def test_tui_render_single_line_status_bar():
    tui = VoiceTranscriberTUI()
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
    tui = VoiceTranscriberTUI()
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
        device_name="Mock Mic"
    )
    assert tui.transcription_count == 2

    tui.print_event("Test Event", "Settings updated cleanly", level="info")
    tui.print_warning("Test Warning", "Microphone level low")
    tui.print_error("Test Error", "Audio stream interrupted")
