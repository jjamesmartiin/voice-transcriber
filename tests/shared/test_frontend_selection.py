#!/usr/bin/env python3
"""Frontend selection: which UI a host gets, and why.

The ratatui frontend is Unix-only on both sides of the socket — `tui_available()`
requires `socket.AF_UNIX` (absent from stock CPython on Windows, bpo-33408) and
the whole `tui-rs` crate imports `std::os::unix`. That gate is what keeps
Linux/WSL-only frontend work (e.g. the mic picker's per-device level meters) from
reaching native Windows, so it is pinned here rather than assumed. See the
"Native Windows has no control API, and no ratatui frontend" note in TODO.md.
"""
import socket
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import main as main_mod
import tui_ratatui
from tui import VoiceTranscriberTUI
from tui_ratatui import RatatuiTui

HAS_AF_UNIX = hasattr(socket, "AF_UNIX")
#: A path that certainly exists, so only the AF_UNIX gate is under test.
REAL_BINARY = "/bin/sh"


@pytest.fixture
def prefer_ratatui(monkeypatch):
    """Point the app at an existing binary and let it choose the frontend."""
    monkeypatch.delenv("VT_TUI", raising=False)
    monkeypatch.setenv("VT_TUI_BIN", REAL_BINARY)


def test_without_af_unix_the_ratatui_frontend_is_unavailable(monkeypatch):
    """This is the whole Windows story: no AF_UNIX, no ratatui."""
    monkeypatch.delattr(socket, "AF_UNIX")

    assert tui_ratatui.tui_available(REAL_BINARY) is False


@pytest.mark.skipif(not HAS_AF_UNIX, reason="no AF_UNIX on this interpreter")
def test_with_af_unix_the_ratatui_frontend_is_available(prefer_ratatui):
    """Guards the test above from passing vacuously."""
    assert tui_ratatui.tui_available(REAL_BINARY) is True


def test_create_tui_falls_back_to_rich_without_af_unix(monkeypatch, prefer_ratatui):
    """A host without AF_UNIX gets the Rich TUI even with a binary on disk.

    Regression guard: frontend features that only exist in the Rust UI (the mic
    picker's live per-device levels) must stay unreachable there.
    """
    monkeypatch.delattr(socket, "AF_UNIX")

    assert isinstance(main_mod.create_tui(), VoiceTranscriberTUI)


@pytest.mark.skipif(not HAS_AF_UNIX, reason="no AF_UNIX on this interpreter")
def test_create_tui_prefers_ratatui_when_available(prefer_ratatui):
    assert isinstance(main_mod.create_tui(), RatatuiTui)


@pytest.mark.skipif(not HAS_AF_UNIX, reason="no AF_UNIX on this interpreter")
def test_vt_tui_rich_forces_the_rich_frontend(monkeypatch):
    """The documented escape hatch must keep working on every platform."""
    monkeypatch.setenv("VT_TUI", "rich")
    monkeypatch.setenv("VT_TUI_BIN", REAL_BINARY)

    assert isinstance(main_mod.create_tui(), VoiceTranscriberTUI)
