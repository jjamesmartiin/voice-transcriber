#!/usr/bin/env python3
"""
Unit tests for the Time Saved stat feature based on configurable typing WPM.
Tests:
- WPM calculation and time saved math
- Formatting of durations (seconds, minutes, hours)
- Config loading, saving, environment overrides, and defaults
- Session accumulation in SimpleVoiceTranscriber and control status
- TUI display of the time saved badge
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2
from main import SimpleVoiceTranscriber
from tui import VoiceTranscriberTUI
from tui_ratatui import RatatuiTui


@pytest.fixture
def clean_config(tmp_path, monkeypatch):
    """Isolate config persistence to a temporary file and clear relevant env vars."""
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(t2, "CONFIG_FILE", config_path)
    monkeypatch.delenv("VT_TYPING_WPM", raising=False)
    monkeypatch.setattr(t2, "TYPING_WPM", 40)
    return config_path


# ===========================================================================
# 1. WPM Calculation and Time Saved Math
# ===========================================================================
class TestTimeSavedMath:
    def test_zero_words_returns_zero(self):
        """Empty, whitespace-only, or None text should return 0.0 time saved."""
        assert t2.calculate_time_saved("", 1.0, wpm=40) == 0.0
        assert t2.calculate_time_saved("   ", 1.0, wpm=40) == 0.0
        assert t2.calculate_time_saved(None, 1.0, wpm=40) == 0.0

    def test_basic_calculation_with_default_wpm(self, monkeypatch):
        """With default 40 WPM, 40 words take 60s typing time."""
        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        # 40 words, 60s estimated typing time
        words_40 = "word " * 40

        # Actual duration 10s -> 50s saved
        saved = t2.calculate_time_saved(words_40, 10.0)
        assert pytest.approx(saved, 0.01) == 50.0

        # Actual duration 60s -> 0s saved
        saved = t2.calculate_time_saved(words_40, 60.0)
        assert pytest.approx(saved, 0.01) == 0.0

        # Actual duration 75s (longer than typing) -> clamped at 0.0, never negative
        saved = t2.calculate_time_saved(words_40, 75.0)
        assert saved == 0.0

    def test_custom_wpm(self):
        """Custom WPM overrides module default correctly."""
        words_20 = "word " * 20

        # At 80 WPM: 20 words = 0.25 min = 15s typing time. Actual 5s -> 10s saved.
        assert pytest.approx(t2.calculate_time_saved(words_20, 5.0, wpm=80), 0.01) == 10.0

        # At 20 WPM: 20 words = 1.0 min = 60s typing time. Actual 15s -> 45s saved.
        assert pytest.approx(t2.calculate_time_saved(words_20, 15.0, wpm=20), 0.01) == 45.0

    def test_invalid_wpm_fallback(self, monkeypatch):
        """Invalid or non-positive WPM should fall back gracefully without raising."""
        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        words_40 = "word " * 40  # 60s typing time at 40 WPM

        # 0, negative, string, or None fallback to 40 WPM
        for bad_wpm in (0, -10, "invalid", None):
            saved = t2.calculate_time_saved(words_40, 10.0, wpm=bad_wpm)
            assert pytest.approx(saved, 0.01) == 50.0

    def test_multiline_and_punctuation_handling(self):
        """Word count correctly splits across newlines, tabs, and spaces."""
        text = "Hello world!\nThis is a multiline\tstring with   spaces."
        # words = ["Hello", "world!", "This", "is", "a", "multiline", "string", "with", "spaces."] (9 words)
        words = len(text.strip().split())
        assert words == 9

        # At 40 WPM: (9 / 40) * 60 = 13.5s typing time. Actual 3.5s -> 10.0s saved.
        assert pytest.approx(t2.calculate_time_saved(text, 3.5, wpm=40), 0.01) == 10.0


# ===========================================================================
# 2. Duration Formatting
# ===========================================================================
class TestDurationFormatting:
    def test_seconds_format(self):
        """Durations under 60 seconds format as Xs."""
        assert t2.format_duration(0) == "0s"
        assert t2.format_duration(14) == "14s"
        assert t2.format_duration(59) == "59s"
        assert t2.format_duration(14.2) == "14s"
        assert t2.format_duration(14.8) == "15s"
        assert t2.format_duration(-5) == "0s"

    def test_minutes_format(self):
        """Durations from 60s to 3599s format as Xm YYs."""
        assert t2.format_duration(60) == "1m 00s"
        assert t2.format_duration(65) == "1m 05s"
        assert t2.format_duration(135) == "2m 15s"
        assert t2.format_duration(120) == "2m 00s"
        assert t2.format_duration(3599) == "59m 59s"

    def test_hours_format(self):
        """Durations of 3600s or more format as Xh YYm."""
        assert t2.format_duration(3600) == "1h 00m"
        assert t2.format_duration(3660) == "1h 01m"
        assert t2.format_duration(3900) == "1h 05m"
        assert t2.format_duration(7200) == "2h 00m"
        assert t2.format_duration(7320) == "2h 02m"
        assert t2.format_duration(36000) == "10h 00m"

    def test_half_seconds_round_up_like_the_rust_frontend(self):
        """Parity with tui-rs/src/ui.rs::format_duration.

        Python's builtin round() is banker's rounding (round(2.5) == 2), so using
        it made the Rich and ratatui badges disagree by a second on exactly-.5
        values. Both sides now round half up; these are the same expected values
        pinned in ui.rs::tests::test_format_duration_matches_the_python_frontend.
        """
        assert t2.format_duration(0.5) == "1s"
        assert t2.format_duration(2.5) == "3s"
        assert t2.format_duration(12.5) == "13s"
        assert t2.format_duration(62.5) == "1m 03s"
        assert t2.format_duration(3599.5) == "1h 00m"
        assert t2.format_duration(14.4) == "14s"
        assert t2.format_duration(14.5) == "15s"


# ===========================================================================
# 3. Config Loading and Defaults
# ===========================================================================
class TestConfigLoadingAndDefaults:
    def test_defaults_include_typing_wpm(self):
        """Shipped defaults must include TYPING_WPM = 40."""
        assert t2.DEFAULT_SETTINGS.get("TYPING_WPM") == 40
        assert getattr(t2, "TYPING_WPM", None) is not None

    def test_load_audio_config_reads_typing_wpm(self, clean_config):
        """load_audio_config correctly parses typing_wpm from YAML."""
        clean_config.write_text("typing_wpm: 65\n")
        t2.load_audio_config(file_path=str(clean_config))
        assert t2.TYPING_WPM == 65

    def test_load_audio_config_invalid_fallback(self, clean_config):
        """Invalid typing_wpm values in config fall back safely to 40."""
        clean_config.write_text("typing_wpm: -20\n")
        t2.load_audio_config(file_path=str(clean_config))
        assert t2.TYPING_WPM == 40

        clean_config.write_text("typing_wpm: not_a_number\n")
        t2.load_audio_config(file_path=str(clean_config))
        assert t2.TYPING_WPM == 40

    def test_save_audio_config_persists_typing_wpm(self, clean_config):
        """save_audio_config writes typing_wpm to config file."""
        t2.set_typing_wpm(75)
        assert t2.TYPING_WPM == 75

        # Reload from file to ensure round-trip
        t2.TYPING_WPM = 40  # clobber in memory
        t2.load_audio_config(file_path=str(clean_config))
        assert t2.TYPING_WPM == 75

    def test_env_var_override(self, clean_config, monkeypatch):
        """VT_TYPING_WPM environment variable overrides config file value."""
        clean_config.write_text("typing_wpm: 50\n")
        monkeypatch.setenv("VT_TYPING_WPM", "90")
        t2.load_audio_config(file_path=str(clean_config))
        assert t2.TYPING_WPM == 90

    def test_reset_to_defaults_restores_typing_wpm(self, clean_config):
        """reset_to_defaults restores TYPING_WPM to 40 and persists it."""
        t2.TYPING_WPM = 120
        t2.reset_to_defaults()
        assert t2.TYPING_WPM == 40

    def test_cycle_typing_wpm(self, clean_config):
        """cycle_typing_wpm advances through presets and persists."""
        t2.set_typing_wpm(40)
        assert t2.cycle_typing_wpm() == 50
        assert t2.TYPING_WPM == 50
        assert t2.cycle_typing_wpm() == 60
        assert t2.cycle_typing_wpm() == 70
        assert t2.cycle_typing_wpm() == 80
        assert t2.cycle_typing_wpm() == 100
        assert t2.cycle_typing_wpm() == 30
        assert t2.cycle_typing_wpm() == 40
        # Custom value outside presets advances to next higher preset
        t2.set_typing_wpm(45)
        assert t2.cycle_typing_wpm() == 50
        # Very high custom value wraps to first preset
        t2.set_typing_wpm(150)
        assert t2.cycle_typing_wpm() == 30


# ===========================================================================
# 4. Session Accumulation and Status Tracking
# ===========================================================================
class TestSessionAccumulation:
    def _create_app(self, monkeypatch):
        app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
        app.recording = False
        app.copy_to_clipboard = False
        app.start_time = 0.0
        app.release_time = 2.0
        app.last_finish_time = 0.0
        app.audio_frames = [b"mock"]
        app.last_transcription = ""
        app.model_load_error = None
        app._model_ready_event = MagicMock()
        app._model_ready_event.is_set.return_value = True

        app.session_words = 0
        app.session_time_saved_sec = 0.0
        app.session_transcriptions = 0

        app.tui = MagicMock()
        app.visual_notification = MagicMock()
        app.audio_cues = MagicMock()
        app.hotkey_system = None
        app.clipboard_sink = MagicMock()

        monkeypatch.setattr(
            "main.copy_to_clipboard_crossplatform", lambda text, sink=None: True
        )
        monkeypatch.setattr(t2, "TYPING_WPM", 40)
        return app

    def test_initial_session_stats(self, monkeypatch):
        """New app instance has zero session counters and correct status values."""
        app = self._create_app(monkeypatch)
        assert app.session_words == 0
        assert app.session_time_saved_sec == 0.0
        assert app.session_transcriptions == 0

        status = app._control_status()
        assert status["session_words"] == 0
        assert status["session_time_saved_sec"] == 0.0
        assert status["typing_wpm"] == 40
        assert status["lifetime_words"] == 0
        assert status["lifetime_time_saved_sec"] == 0.0
        assert status["lifetime_time_saved"] == "0s"

    def test_process_recording_accumulates_stats(self, monkeypatch):
        """process_recording updates session_words, session_time_saved_sec, and session_transcriptions."""
        app = self._create_app(monkeypatch)

        # 20 words. At 40 WPM: typing time is 30.0s.
        sentence = "word " * 20
        monkeypatch.setattr(
            "main.process_audio_stream", lambda frames: (sentence, 0.1)
        )

        # Fixed time: start_time = 0.0, proc_time will be ~0, actual_duration ~0.1
        app.start_time = 100.0
        app.release_time = 102.0
        monkeypatch.setattr("time.time", lambda: 102.5)

        app.process_recording()

        assert app.session_words == 20
        assert app.session_transcriptions == 1
        assert app.session_time_saved_sec > 0.0

        first_saved = app.session_time_saved_sec

        # Status reflects the accumulated stats
        status = app._control_status()
        assert status["session_words"] == 20
        assert status["session_time_saved_sec"] == first_saved
        assert status["typing_wpm"] == 40

        # Second recording with 10 words
        app.audio_frames = [b"mock2"]
        sentence2 = "second " * 10
        monkeypatch.setattr(
            "main.process_audio_stream", lambda frames: (sentence2, 0.1)
        )
        app.start_time = 200.0
        app.release_time = 201.0
        monkeypatch.setattr("time.time", lambda: 201.5)

        app.process_recording()

        assert app.session_words == 30
        assert app.session_transcriptions == 2
        assert app.session_time_saved_sec > first_saved

        status2 = app._control_status()
        assert status2["session_words"] == 30
        assert status2["session_time_saved_sec"] == app.session_time_saved_sec

    def test_notification_receives_time_saved(self, monkeypatch):
        """visual_notification.show_completed receives time_saved and session_time_saved."""
        app = self._create_app(monkeypatch)
        monkeypatch.setattr(
            "main.process_audio_stream", lambda frames: ("alpha beta gamma", 0.05)
        )
        app.start_time = 10.0
        app.release_time = 11.0
        monkeypatch.setattr("time.time", lambda: 11.2)

        # Intercept background thread to run synchronously
        class SyncThread:
            def __init__(self, target, daemon=True):
                self.target = target

            def start(self):
                self.target()

        monkeypatch.setattr("threading.Thread", SyncThread)

        app.process_recording()

        app.visual_notification.show_completed.assert_called_once()
        _, kwargs = app.visual_notification.show_completed.call_args
        assert "time_saved" in kwargs
        assert "session_time_saved" in kwargs
        assert "lifetime_time_saved" in kwargs
        assert kwargs["time_saved"] > 0.0
        assert kwargs["session_time_saved"] == kwargs["time_saved"]

    def test_engine_startup_creates_the_stats_file(self, monkeypatch, tmp_path):
        """Engine start writes stats.json even if the user never dictates."""
        stats_path = tmp_path / "stats.json"
        monkeypatch.setenv("VT_STATS_FILE", str(stats_path))
        app = self._create_app(monkeypatch)
        assert not stats_path.exists()

        app._refresh_lifetime_stats(record_session=True)

        assert stats_path.exists()
        assert app.lifetime_sessions == 1
        assert app.lifetime_time_saved_sec == 0.0

    def test_lifetime_stats_persist_across_engine_instances(self, monkeypatch, tmp_path):
        """The all-time total outlives the process; the session total does not."""
        monkeypatch.setenv("VT_STATS_FILE", str(tmp_path / "stats.json"))
        app = self._create_app(monkeypatch)
        monkeypatch.setattr("main.process_audio_stream", lambda frames: ("word " * 20, 0.1))
        app.start_time = 100.0
        app.release_time = 102.0
        monkeypatch.setattr("time.time", lambda: 102.5)

        app.process_recording()

        saved = app.lifetime_time_saved_sec
        assert app.lifetime_words == 20
        assert app.lifetime_transcriptions == 1
        assert saved > 0.0

        # A new engine: session counters start at zero, lifetime totals resume.
        app2 = self._create_app(monkeypatch)
        app2._refresh_lifetime_stats()
        assert app2.session_time_saved_sec == 0.0
        assert app2.lifetime_words == 20
        assert app2.lifetime_transcriptions == 1
        assert app2.lifetime_time_saved_sec == pytest.approx(saved)

        status = app2._control_status()
        assert status["lifetime_time_saved_sec"] == pytest.approx(saved)
        assert status["lifetime_words"] == 20


# ===========================================================================
# 5. TUI Display Tests
# ===========================================================================
class TestTuiDisplay:
    def test_rich_tui_prints_time_saved_badge(self):
        """Rich VoiceTranscriberTUI renders the time saved badge in top header rule."""
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console.width = 120
        tui.console.record = True

        tui.print_transcription(
            text="Testing time saved display output.",
            elapsed_sec=0.5,
            rec_duration=2.0,
            proc_time=0.4,
            time_saved=12.0,
            session_time_saved=105.0,
        )

        output = tui.console.export_text()
        assert "⚡ saved: +12s (session: 1m 45s)" in output

    def test_rich_tui_prints_lifetime_total(self):
        """With lifetime stats the badge labels the session and the all-time total."""
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console.width = 120
        tui.console.record = True

        tui.print_transcription(
            text="All-time totals.",
            elapsed_sec=0.5,
            rec_duration=2.0,
            proc_time=0.4,
            time_saved=12.0,
            session_time_saved=105.0,
            lifetime_time_saved=3661.0,
        )

        output = tui.console.export_text()
        assert "⚡ saved: +12s (session: 1m 45s · total: 1h 01m)" in output

    def test_rich_tui_lifetime_only_on_a_fresh_session(self):
        """First dictation of a session: session total == all-time, so show one label."""
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console.width = 120
        tui.console.record = True

        tui.print_transcription(
            text="Fresh session.",
            elapsed_sec=0.5,
            time_saved=12.0,
            session_time_saved=12.0,
            lifetime_time_saved=12.0,
        )

        output = tui.console.export_text()
        assert "⚡ saved: +12s (total: 12s)" in output

    def test_rich_tui_without_time_saved_omits_badge(self):
        """When time_saved is 0.0 or omitted, no badge is displayed and output is clean."""
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console.record = True

        tui.print_transcription(
            text="Standard transcription without time saved.",
            elapsed_sec=0.5,
            rec_duration=2.0,
            proc_time=0.4,
        )

        output = tui.console.export_text()
        assert "⚡ saved:" not in output
        assert "rec: 2.00s" in output

    def test_rich_tui_formatted_string_input(self):
        """print_transcription accepts pre-formatted strings for time saved."""
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console.width = 120
        tui.console.record = True

        tui.print_transcription(
            text="Pre-formatted strings.",
            elapsed_sec=0.5,
            time_saved="+15s",
            session_time_saved="3m 30s",
        )

        output = tui.console.export_text()
        assert "⚡ saved: +15s (session: 3m 30s)" in output

    def test_ratatui_forwards_time_saved(self):
        """RatatuiTui includes time_saved, session and lifetime totals in the IPC payload."""
        tui = RatatuiTui.__new__(RatatuiTui)
        tui.transcription_count = 0
        sent = []
        tui._send = lambda msg: sent.append(msg)

        tui.print_transcription(
            text="Ratatui IPC test",
            elapsed_sec=0.8,
            rec_duration=3.0,
            proc_time=0.2,
            time_saved=14.5,
            session_time_saved=65.0,
            lifetime_time_saved=3600.0,
        )

        assert len(sent) == 1
        msg = sent[0]
        assert msg["t"] == "tx"
        assert msg["text"] == "Ratatui IPC test"
        assert msg["time_saved"] == 14.5
        assert msg["session_time_saved"] == 65.0
        assert msg["lifetime_time_saved"] == 3600.0
