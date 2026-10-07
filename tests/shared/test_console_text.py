#!/usr/bin/env python3
"""
Encoding-safety tests (src/voice_transcriber/console_text.py).

Motivation: on a stream that cannot encode our glyphs — a legacy Windows
codepage, ``LANG=C`` behind a pipe, ``PYTHONIOENCODING=ascii`` — ``print``
raises ``UnicodeEncodeError`` and Rich *re-raises* it instead of degrading. The
transcription divider is printed inside a ``try/except``, so the badge vanished
silently. These tests pin the two layers that prevent it.
"""

import io
import sys
from pathlib import Path

import pytest
from rich.console import Console

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import console_text
import control
from tui import VoiceTranscriberTUI


class AsciiStream:
    """A TextIO look-alike that encodes ASCII only, like a legacy console.

    Writing anything above U+007F raises exactly as a real ``TextIOWrapper`` with
    ``encoding="ascii"`` does — the error Rich re-raises.
    """

    encoding = "ascii"

    def __init__(self):
        self.chunks = []

    def write(self, text):
        text.encode("ascii")  # raises UnicodeEncodeError for any glyph
        self.chunks.append(text)
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    @property
    def text(self):
        return "".join(self.chunks)


@pytest.fixture
def ascii_stream():
    return AsciiStream()


class TestAsciiText:
    def test_utf8_stream_keeps_glyphs_untouched(self):
        assert console_text.ascii_text("⚡ saved · total", io.StringIO()) == "⚡ saved · total"

    def test_ascii_stream_downgrades_glyphs(self, ascii_stream):
        assert console_text.ascii_text("⚡ saved: +12s · total", ascii_stream) == "* saved: +12s | total"

    def test_downgrade_is_per_glyph_not_all_or_nothing(self):
        """cp437 has │ ─ · but not ⚡ ❯: keep what fits, replace only the rest."""

        class Cp437Stream:
            encoding = "cp437"

        assert console_text.ascii_text("⚡│─·❯", Cp437Stream()) == "*│─·>"

    def test_vt_ascii_forces_downgrade_even_on_utf8(self, monkeypatch):
        monkeypatch.setenv(console_text.ASCII_ENV, "1")
        assert console_text.ascii_text("⚡ saved · total", io.StringIO()) == "* saved | total"

    def test_empty_text_is_returned_as_is(self, ascii_stream):
        assert console_text.ascii_text("", ascii_stream) == ""

    def test_variation_selectors_are_dropped(self, ascii_stream):
        assert console_text.ascii_text("⚠️ warning", ascii_stream) == "! warning"

    def test_astral_emoji_are_translated(self, ascii_stream):
        assert console_text.ascii_text("🤖 SLM pass", ascii_stream) == "[SLM] SLM pass"

    def test_spinner_frames_still_animate_in_ascii(self, ascii_stream):
        frames = [console_text.ascii_text(f, ascii_stream) for f in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"]
        assert all(f.isascii() for f in frames)
        assert len(set(frames)) > 1, "an ASCII spinner must still change between frames"


class TestSafePrintAndWrite:
    def test_safe_print_never_raises_on_an_ascii_stream(self, ascii_stream):
        console_text.safe_print("⚡ saved: +12s · total: 1h", file=ascii_stream)
        assert ascii_stream.text == "* saved: +12s | total: 1h\n"

    def test_write_falls_back_for_glyphs_outside_the_table(self, ascii_stream):
        """A glyph we never mapped degrades to '?' rather than raising."""
        console_text.write("snowman \u2603\n", ascii_stream)
        assert ascii_stream.text == "snowman ?\n"

    def test_safe_print_matches_print_signature(self, ascii_stream):
        console_text.safe_print("a", "b", sep="-", end="!", file=ascii_stream)
        assert ascii_stream.text == "a-b!"

    def test_harden_stream_makes_a_real_ascii_stream_safe(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="ascii")

        with pytest.raises(UnicodeEncodeError):
            stream.write("⚡")  # the failure mode being fixed

        assert console_text.harden_stream(stream) is True
        stream.write("⚡")  # now replaced instead of raising
        stream.flush()
        assert buffer.getvalue() == b"?"

    def test_harden_stream_tolerates_streams_without_reconfigure(self):
        assert console_text.harden_stream(io.StringIO()) is False

    def test_harden_standard_streams_is_safe_to_call_repeatedly(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", AsciiStream())
        monkeypatch.setattr(sys, "stderr", AsciiStream())
        console_text.harden_standard_streams()
        console_text.harden_standard_streams()  # idempotent


class TestEncodingSafeStream:
    def test_write_translates_and_returns_length(self, ascii_stream):
        wrapper = console_text.EncodingSafeStream(ascii_stream)
        assert wrapper.write("⚡ ·") == 3  # ⚡, space, ·
        assert ascii_stream.text == "* |"

    def test_delegates_encoding_isatty_and_missing_attrs(self, ascii_stream):
        wrapper = console_text.EncodingSafeStream(ascii_stream)
        assert wrapper.encoding == "ascii"
        assert wrapper.isatty() is False
        with pytest.raises(AttributeError):
            _ = wrapper.fileno  # not provided by AsciiStream; must not recurse

    def test_resolves_sys_stdout_lazily(self, monkeypatch):
        """A later sys.stdout replacement (pytest, pager) must still be honoured."""
        wrapper = console_text.EncodingSafeStream()  # no explicit stream
        monkeypatch.setattr(sys, "stdout", AsciiStream())
        wrapper.write("⚡")
        assert sys.stdout.text == "*"

    def test_terminal_detection_parity_with_a_plain_console(self, monkeypatch):
        """Rich picks colour and Live animation from is_terminal: it must not change."""

        class TtyStream(AsciiStream):
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdout", TtyStream())
        assert Console().is_terminal is True
        assert Console(file=console_text.EncodingSafeStream()).is_terminal is True

        monkeypatch.setattr(sys, "stdout", AsciiStream())
        assert Console().is_terminal is False
        assert Console(file=console_text.EncodingSafeStream()).is_terminal is False


class TestNoConsoleInterpreter:
    """``pythonw.exe`` / PyInstaller ``--noconsole``: sys.stdout and stderr are None.

    Builtin ``print`` silently no-ops there, so our helpers must too rather than
    raising ``AttributeError: 'NoneType' object has no attribute 'write'``.
    """

    def test_safe_print_noops_when_stdout_is_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        console_text.safe_print("⚡ saved: +12s · total: 1h")

    def test_safe_print_noops_when_the_explicit_file_is_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        console_text.safe_print("x", file=sys.stdout)

    def test_safe_print_noops_when_stderr_is_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stderr", None)
        console_text.safe_print("✗ command failed", file=sys.stderr)

    def test_write_noops_when_stream_is_none(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        console_text.write("⚡ ·")

    def test_ascii_text_tolerates_a_none_stream(self):
        assert console_text.ascii_text("⚡ ·", None) == "⚡ ·"

    def test_harden_standard_streams_tolerates_none_streams(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        monkeypatch.setattr(sys, "stderr", None)
        console_text.harden_standard_streams()
        assert console_text.harden_stream(None) is False

    def test_rich_console_print_does_not_raise_without_a_console(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        console = Console(file=console_text.EncodingSafeStream())
        console.print("⚡ saved: +12s · total: 1h")  # must not raise

    def test_encoding_safe_stream_reports_no_tty_and_utf8(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", None)
        wrapper = console_text.EncodingSafeStream()
        assert wrapper.isatty() is False
        assert wrapper.encoding == "utf-8"


class TestTuiBadgeOnAnAsciiConsole:
    """End-to-end: the money path — the badge must render, not vanish."""

    def _tui_with_ascii_console(self, ascii_stream):
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console = Console(
            file=console_text.EncodingSafeStream(ascii_stream),
            width=120,
        )
        return tui

    def test_badge_renders_as_ascii_instead_of_raising(self, ascii_stream):
        tui = self._tui_with_ascii_console(ascii_stream)

        tui.print_transcription(
            text="Testing the badge on a legacy codepage.",
            elapsed_sec=0.5,
            rec_duration=2.0,
            proc_time=0.4,
            time_saved=12.0,
            session_time_saved=105.0,
            lifetime_time_saved=3661.0,
        )

        output = ascii_stream.text
        assert output.isascii(), "nothing un-encodable may reach the stream"
        assert "* saved: +12s (session: 1m 45s | total: 1h 01m)" in output

    def test_whole_frame_is_ascii_including_rules_and_icons(self, ascii_stream):
        tui = self._tui_with_ascii_console(ascii_stream)
        tui.print_transcription(text="Hello.", elapsed_sec=0.5, time_saved=9.0)

        output = ascii_stream.text
        assert output.isascii()
        assert "-" in output, "the ─ rules must become -"
        assert "|" in output, "the │ separators must become |"

    def test_utf8_console_still_gets_the_fancy_glyphs(self):
        """The default path is unchanged — no ASCII downgrade when it fits."""
        tui = VoiceTranscriberTUI(ui_theme="cyan")
        tui.console.width = 120
        tui.console.record = True

        tui.print_transcription(text="Unicode.", elapsed_sec=0.5, time_saved=12.0, session_time_saved=105.0)

        assert "⚡ saved: +12s (session: 1m 45s)" in tui.console.export_text()


class TestGlyphCoverage:
    """Everything the app renders must be in the table, so nothing becomes '?'."""

    @pytest.mark.parametrize("filename", ["tui.py", "control.py"])
    def test_every_rendered_glyph_has_an_ascii_stand_in(self, filename):
        source = (SRC_DIR / "voice_transcriber" / filename).read_text(encoding="utf-8")
        missing = sorted({c for c in source if ord(c) > 127 and c not in console_text._GLYPHS})
        assert missing == [], f"{filename} renders glyphs with no ASCII fallback: {missing}"


class TestControlCliOutput:
    def test_stats_summary_downgrades_its_separator(self, monkeypatch, capsys):
        monkeypatch.setenv(console_text.ASCII_ENV, "1")
        control._print_stats_summary({
            "lifetime_time_saved": "1h 20m",
            "lifetime_words": 4210,
            "lifetime_transcriptions": 512,
            "lifetime_sessions": 37,
            "session_time_saved_sec": 41.2,
        })
        output = capsys.readouterr().out
        assert output.isascii()
        assert "time saved: 1h 20m all-time | 4210 words | 512 dictations | 37 sessions | 41s this session" in output

    def test_stats_summary_pluralizes_a_single_item(self, monkeypatch, capsys):
        monkeypatch.setenv(console_text.ASCII_ENV, "1")
        control._print_stats_summary({
            "lifetime_time_saved": "6s",
            "lifetime_words": 1,
            "lifetime_transcriptions": 1,
            "lifetime_sessions": 1,
        })
        output = capsys.readouterr().out
        assert "1 word | 1 dictation | 1 session" in output
        assert "sessions" not in output

    def test_human_status_line_downgrades_ok_marker(self, monkeypatch, capsys):
        monkeypatch.setenv(console_text.ASCII_ENV, "1")
        control._print_human("output", {"ok": True, "cmd": "output", "output_mode": "type_fast"})
        output = capsys.readouterr().out
        assert output.isascii()
        assert output.startswith("+ output")

    def test_error_line_downgrades_the_cross_marker(self, monkeypatch, capsys):
        monkeypatch.setenv(console_text.ASCII_ENV, "1")
        control._print_human("output", {"ok": False, "error": "output needs a value"})
        captured = capsys.readouterr()
        assert captured.err.isascii()
        assert "x output needs a value" in captured.err
