#!/usr/bin/env python3
"""Tests for ``src/logging_setup.py`` — console + per-user log file."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import logging_setup  # noqa: E402


@pytest.fixture(autouse=True)
def restore_root_logger():
    """configure_logging() mutates the root logger; put it back for other tests."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


class TestDefaultLogPath:
    def test_explicit_override_wins(self, monkeypatch, tmp_path):
        target = tmp_path / "custom.log"
        monkeypatch.setenv("VT_LOG_FILE", str(target))
        assert logging_setup.default_log_path() == target

    @pytest.mark.skipif(os.name != "posix", reason="POSIX-only branch")
    def test_posix_default_uses_xdg_data_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("VT_LOG_FILE", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert logging_setup.default_log_path() == tmp_path / "vt" / "vt.log"

    @pytest.mark.skipif(os.name != "nt", reason="Windows-only branch")
    def test_windows_default_uses_localappdata(self, monkeypatch, tmp_path):
        monkeypatch.delenv("VT_LOG_FILE", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert logging_setup.default_log_path() == tmp_path / "vt" / "vt.log"


class TestConfigureLogging:
    def test_writes_to_the_file(self, tmp_path):
        target = tmp_path / "vt.log"
        path = logging_setup.configure_logging(log_file=target, console=False)
        assert path == target
        logging.getLogger("vt.test").warning("hello log")
        assert "hello log" in target.read_text(encoding="utf-8")

    def test_disable_flag_skips_the_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VT_LOG_DISABLE", "1")
        target = tmp_path / "vt.log"
        assert logging_setup.configure_logging(log_file=target, console=False) is None
        assert not target.exists()

    def test_unwritable_location_does_not_raise(self, tmp_path):
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        # Parent is a file, so mkdir/open must fail; configure_logging swallows it.
        assert logging_setup.configure_logging(log_file=blocker / "vt.log", console=False) is None

    def test_level_is_configurable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("VT_LOG_LEVEL", "INFO")
        logging_setup.configure_logging(log_file=tmp_path / "vt.log", console=False)
        assert logging.getLogger().level == logging.INFO
