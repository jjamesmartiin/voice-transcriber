#!/usr/bin/env python3
"""
Unit tests for the persistent lifetime dictation stats (src/voice_transcriber/stats.py).

The feature exists so the user can see how much typing time dictation has saved
them *across* sessions, which means the file must survive a restart, never
corrupt itself on a kill, never become a large file, and behave identically on
Linux, Windows and WSL.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import stats


@pytest.fixture
def stats_file(tmp_path, monkeypatch):
    """Redirect persisted stats at a throwaway file for this test only."""
    path = tmp_path / "stats.json"
    monkeypatch.setenv("VT_STATS_FILE", str(path))
    # The stale temp-file sweep runs once per process; reset it so each test
    # exercises it deterministically.
    monkeypatch.setattr(stats, "_cleaned_temp_files", False)
    return path


class TestPersistence:
    def test_missing_file_reads_as_zero_without_creating_it(self, stats_file):
        record = stats.snapshot()
        assert record["time_saved_sec"] == 0.0
        assert record["words"] == 0
        assert record["transcriptions"] == 0
        assert record["sessions"] == 0
        assert record["first_used"] is None
        assert not stats_file.exists(), "reading stats must not write a file"

    def test_ensure_creates_a_missing_file(self, stats_file):
        """ensure() is the explicit create-if-absent entry point."""
        record = stats.ensure()
        assert stats_file.exists(), "ensure() must create the file"
        assert record["words"] == 0
        assert json.loads(stats_file.read_text())["time_saved_sec"] == 0.0

    def test_ensure_is_idempotent_and_keeps_totals(self, stats_file):
        stats.record_transcription(7, 9.0)
        assert stats.ensure()["words"] == 7, "ensure() must never reset a file"
        assert stats.ensure()["words"] == 7

    def test_first_session_start_creates_the_file(self, stats_file):
        """A launch with zero dictations still leaves a stats file behind."""
        assert not stats_file.exists()
        record = stats.record_session_start()
        assert stats_file.exists()
        assert record["sessions"] == 1
        assert record["transcriptions"] == 0

    def test_first_dictation_creates_the_file(self, stats_file):
        assert not stats_file.exists()
        stats.record_transcription(3, 4.0)
        assert stats_file.exists()

    def test_transcriptions_accumulate_and_persist(self, stats_file):
        stats.record_transcription(20, 30.0)
        record = stats.record_transcription(10, 12.5)

        assert record["words"] == 30
        assert record["transcriptions"] == 2
        assert record["time_saved_sec"] == pytest.approx(42.5)
        assert record["first_used"] and record["last_used"]

        on_disk = json.loads(stats_file.read_text())
        assert on_disk["words"] == 30
        assert on_disk["transcriptions"] == 2
        assert on_disk["time_saved_sec"] == pytest.approx(42.5)

    def test_totals_survive_a_fresh_read(self, stats_file):
        """The whole point: a later process sees an earlier process' totals."""
        stats.record_transcription(5, 7.0)
        assert stats.snapshot()["time_saved_sec"] == pytest.approx(7.0)

        # snapshot() re-reads the file rather than trusting an in-memory cache
        stats_file.write_text(json.dumps({
            "time_saved_sec": 100.0, "words": 1, "transcriptions": 1, "sessions": 1,
        }))
        assert stats.snapshot()["time_saved_sec"] == pytest.approx(100.0)

    def test_session_counter_is_separate_from_transcriptions(self, stats_file):
        stats.record_session_start()
        stats.record_session_start()
        record = stats.record_transcription(1, 1.0)
        assert record["sessions"] == 2
        assert record["transcriptions"] == 1

    def test_reset_clears_and_persists(self, stats_file):
        stats.record_transcription(50, 90.0)
        record = stats.reset()
        assert record["words"] == 0
        assert record["time_saved_sec"] == 0.0
        assert json.loads(stats_file.read_text())["words"] == 0

    def test_writes_leave_no_temp_files_behind(self, stats_file):
        for _ in range(3):
            stats.record_transcription(1, 1.0)
        leftovers = [p.name for p in stats_file.parent.iterdir() if p.name != stats_file.name]
        assert leftovers == []

    def test_creates_missing_parent_directories(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VT_STATS_FILE", str(tmp_path / "deep" / "nested" / "stats.json"))
        assert stats.record_transcription(3, 4.0)["words"] == 3
        assert stats.snapshot()["words"] == 3


class TestBoundedCost:
    """The record is fixed-width and cheap: usage must never grow it or slow the app."""

    def test_file_size_is_constant_as_dictations_accumulate(self, stats_file):
        stats.record_transcription(12, 9.0)
        after_one = stats_file.stat().st_size
        for _ in range(500):
            stats.record_transcription(12, 9.0)
        after_500 = stats_file.stat().st_size
        assert after_500 < 1024, "stats must never become a large file"
        # Only the digit count may differ; a growing record would blow past this.
        assert after_500 - after_one < 64

    def test_updates_stay_fast(self, stats_file):
        """Generous ceiling: catches a pathological regression, not CI jitter."""
        start = time.perf_counter()
        for _ in range(300):
            stats.record_transcription(12, 9.0)
        assert time.perf_counter() - start < 5.0

    def test_oversized_file_is_refused_without_reading_it_all(self, stats_file):
        stats_file.write_bytes(b"x" * (stats.MAX_STATS_BYTES * 4))
        assert stats.snapshot()["words"] == 0  # no hang, no ballooning

    def test_oversized_file_is_repaired_by_the_next_write(self, stats_file):
        stats_file.write_bytes(b"x" * (stats.MAX_STATS_BYTES * 4))
        stats.record_transcription(5, 6.0)
        assert stats_file.stat().st_size < 1024
        assert stats.snapshot()["words"] == 5

    def test_stale_temp_files_from_a_crash_are_swept(self, stats_file):
        stale = stats_file.with_name(f"{stats_file.name}.tmp99999")
        stale.write_text("{}")
        old = time.time() - stats.STALE_TEMP_AGE_SEC - 60
        os.utime(stale, (old, old))
        stats.record_transcription(1, 1.0)
        assert not stale.exists(), "day-old crash leftovers should be removed"
        assert stats_file.exists()

    def test_live_temp_files_are_left_alone(self, stats_file):
        """A concurrently running engine's in-flight temp file must survive."""
        fresh = stats_file.with_name(f"{stats_file.name}.tmp4242")
        fresh.write_text("{}")
        stats.record_transcription(1, 1.0)
        assert fresh.exists()

    def test_empty_file_reads_as_zero(self, stats_file):
        """An interrupted create leaves a 0-byte file; that must not crash a read."""
        stats_file.write_text("")
        assert stats.snapshot()["words"] == 0


class TestCrossPlatform:
    """The stats file must behave identically on Linux, Windows and WSL."""

    def test_write_retries_a_windows_sharing_violation(self, stats_file, monkeypatch):
        """Windows raises PermissionError while another process holds the file."""
        real_replace = os.replace
        calls = []

        def flaky_replace(src, dst):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(32, "The process cannot access the file")
            return real_replace(src, dst)

        monkeypatch.setattr(stats.os, "replace", flaky_replace)
        record = stats.record_transcription(4, 5.0)
        assert len(calls) == 3, "should retry, not give up on the first failure"
        assert record["words"] == 4
        assert json.loads(stats_file.read_text())["words"] == 4

    def test_write_gives_up_after_bounded_retries(self, stats_file, monkeypatch):
        """Persistent contention must not spin: it degrades to a logged failure."""
        calls = []

        def always_locked(src, dst):
            calls.append(1)
            raise PermissionError(32, "The process cannot access the file")

        monkeypatch.setattr(stats.os, "replace", always_locked)
        monkeypatch.setattr(stats.time, "sleep", lambda _s: None)
        record = stats.record_transcription(4, 5.0)
        assert len(calls) == 3, "retries must be bounded"
        assert record["words"] == 4  # in-memory result still returned
        assert not stats_file.exists()

    def test_written_bytes_use_lf_newlines(self, stats_file):
        """newline="\\n" keeps output byte-identical across platforms."""
        stats.record_transcription(1, 1.0)
        raw = stats_file.read_bytes()
        assert b"\r\n" not in raw
        assert raw.endswith(b"\n")

    def test_file_is_valid_utf8(self, stats_file):
        """Explicit utf-8, never the Windows ANSI default."""
        stats.record_transcription(1, 1.0)
        stats_file.read_bytes().decode("utf-8")  # must not raise

    def test_path_with_a_weird_parent_is_created(self, tmp_path, monkeypatch):
        """Spaces/unicode/dots in the override path must not break creation."""
        target = tmp_path / "with space" / "ünïcode" / "stats.json"
        monkeypatch.setattr(stats, "_cleaned_temp_files", False)
        monkeypatch.setenv("VT_STATS_FILE", str(target))
        assert stats.record_transcription(2, 3.0)["words"] == 2
        assert stats.snapshot()["words"] == 2


class TestAdversarialInputs:
    """Cases from deliberately attacking the file handling."""

    @pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
    def test_a_fifo_is_never_opened(self, tmp_path, monkeypatch):
        """open() on a FIFO with no writer blocks *forever*: refuse non-regular files."""
        import builtins

        fifo = tmp_path / "stats.json"
        os.mkfifo(fifo)
        monkeypatch.setattr(stats, "_cleaned_temp_files", False)
        monkeypatch.setenv("VT_STATS_FILE", str(fifo))

        real_open = builtins.open
        opened = []

        def spy(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", spy)
        assert stats.snapshot()["words"] == 0
        assert opened == [], "a FIFO must never be opened (it would hang forever)"

    def test_a_directory_is_not_mistaken_for_a_stats_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VT_STATS_FILE", str(tmp_path))
        assert stats.snapshot()["words"] == 0
        assert tmp_path.is_dir(), "reading must not disturb the directory"

    def test_glob_metacharacters_in_the_filename_delete_nothing_else(self, tmp_path, monkeypatch):
        """A '[' in the filename must not turn the temp sweep into a wildcard."""
        monkeypatch.setattr(stats, "_cleaned_temp_files", False)
        monkeypatch.setenv("VT_STATS_FILE", str(tmp_path / "we[ird].json"))
        # Matches "we[ird].json.tmp*" when globbed, but is not ours.
        victim = tmp_path / "wei.json.tmp999"
        victim.write_text("not ours")
        old = time.time() - stats.STALE_TEMP_AGE_SEC - 60
        os.utime(victim, (old, old))

        stats.record_transcription(1, 1.0)

        assert victim.exists(), "the sweep must not follow a bracketed pattern"
        assert victim.read_text() == "not ours"

    def test_symlink_to_a_regular_file_is_still_read(self, tmp_path, monkeypatch):
        """is_file() follows symlinks, so a symlinked stats file keeps working."""
        real = tmp_path / "real.json"
        monkeypatch.setenv("VT_STATS_FILE", str(real))
        stats.record_transcription(9, 11.0)

        link = tmp_path / "link.json"
        try:
            link.symlink_to(real)
        except (OSError, NotImplementedError):  # Windows without privileges
            pytest.skip("symlinks unavailable")
        monkeypatch.setenv("VT_STATS_FILE", str(link))
        assert stats.snapshot()["words"] == 9

    def test_a_path_with_a_trailing_separator_is_not_a_stats_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VT_STATS_FILE", str(tmp_path) + os.sep)
        assert stats.snapshot()["words"] == 0


class TestRobustness:
    def test_corrupt_file_degrades_to_zero_and_is_repaired(self, stats_file):
        stats_file.write_text("{not json at all")
        assert stats.snapshot()["time_saved_sec"] == 0.0
        assert stats.record_transcription(2, 3.0)["words"] == 2
        assert json.loads(stats_file.read_text())["words"] == 2

    def test_non_dict_json_is_ignored(self, stats_file):
        stats_file.write_text("[1, 2, 3]")
        assert stats.snapshot()["words"] == 0

    def test_garbage_fields_are_sanitized(self, stats_file):
        stats_file.write_text(json.dumps({
            "time_saved_sec": "banana",
            "words": -5,
            "transcriptions": None,
            "sessions": "12",
            "first_used": 42,
        }))
        record = stats.snapshot()
        assert record["time_saved_sec"] == 0.0
        assert record["words"] == 0
        assert record["transcriptions"] == 0
        assert record["sessions"] == 12
        assert record["first_used"] is None

    def test_negative_and_non_finite_input_contributes_nothing(self, stats_file):
        record = stats.record_transcription(-10, float("nan"))
        assert record["words"] == 0
        assert record["time_saved_sec"] == 0.0
        assert record["transcriptions"] == 1

    def test_non_numeric_input_contributes_nothing(self, stats_file):
        record = stats.record_transcription(None, "many")
        assert record["words"] == 0
        assert record["time_saved_sec"] == 0.0

    def test_unwritable_location_does_not_raise(self, tmp_path, monkeypatch):
        # A *file* where a directory is needed: mkdir fails on every platform, and
        # nothing outside tmp_path is touched (an earlier version used /proc, which
        # is POSIX-only and resolves into the current drive root on Windows).
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory")
        monkeypatch.setenv("VT_STATS_FILE", str(blocker / "stats.json"))
        assert stats.record_transcription(1, 1.0)["words"] == 1
        assert stats.snapshot()["words"] == 0
        assert blocker.read_text() == "not a directory"

    def test_path_override_is_honoured(self, stats_file):
        assert stats.stats_path() == stats_file

    def test_write_failure_is_swallowed(self, stats_file, monkeypatch):
        monkeypatch.setattr(stats, "_write_file", lambda path, data: False)
        assert stats.record_transcription(4, 5.0)["words"] == 4
