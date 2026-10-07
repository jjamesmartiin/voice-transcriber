#!/usr/bin/env python3
"""Frontend selection: which UI a host gets, and why.

The ratatui frontend is Unix-only on both sides of the socket — `tui_available()`
requires `socket.AF_UNIX` (absent from stock CPython on Windows, bpo-33408) and
the whole `tui-rs` crate imports `std::os::unix`. That gate is what keeps
Linux/WSL-only frontend work (e.g. the mic picker's per-device level meters) from
reaching native Windows, so it is pinned here rather than assumed. Note this is a
separate gate from the control API, which now works on native Windows over
loopback TCP. See the frontend note in TODO.md.
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
    # ``raising=False`` because on stock Windows CPython the attribute is absent
    # already (bpo-33408), and ``delattr`` on a missing attribute raised
    # AttributeError — so this test failed on the one platform it describes.
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)

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
    monkeypatch.delattr(socket, "AF_UNIX", raising=False)

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


class TestFrontendsAreInterchangeable:
    """`main.py` calls whichever frontend it was given with the same keywords.

    The two TUIs are selected at runtime (`create_tui`), so they are only
    interchangeable if their shared surface actually matches. It did not: the Rich
    TUI's `set_config_state` had no `hotkeys` parameter while `_sync_tui_state`
    always passed one, so every startup that used Rich — the default on native
    Windows, and any no-TTY fallback — died with

        TypeError: set_config_state() got an unexpected keyword argument 'hotkeys'

    before the model even began loading. Nothing caught it because every CI job
    that reaches `_sync_tui_state` picks the ratatui frontend, which has the
    parameter.
    """

    def test_both_frontends_expose_every_method_main_calls(self):
        import re
        from pathlib import Path

        main_src = (Path(__file__).resolve().parents[2]
                    / "src" / "voice_transcriber" / "main.py").read_text()
        called = sorted(set(re.findall(r"self\.tui\.([a-z_]+)\(", main_src)))
        assert called, "no TUI calls found — did main.py move?"

        for name in called:
            for cls in (VoiceTranscriberTUI, RatatuiTui):
                assert hasattr(cls, name), f"{cls.__name__} has no {name}()"

    def test_rich_accepts_every_config_keyword_the_ratatui_frontend_does(self):
        """The ratatui signature is what `main.py` was written against, so the
        Rich one has to accept at least as much."""
        import inspect

        rich_params = set(
            inspect.signature(VoiceTranscriberTUI.set_config_state).parameters
        )
        ratatui_params = set(
            inspect.signature(RatatuiTui.set_config_state).parameters
        )
        missing = sorted(ratatui_params - rich_params)
        assert not missing, f"Rich set_config_state is missing: {missing}"

    def test_sync_tui_state_works_with_the_rich_frontend(self):
        """The real call site, against the frontend that used to break."""
        import main as main_mod

        engine = main_mod.SimpleVoiceTranscriber.__new__(
            main_mod.SimpleVoiceTranscriber
        )
        engine.tui = VoiceTranscriberTUI()

        engine._sync_tui_state()  # must not raise

        assert engine.tui.hotkeys, "the chords should have reached the frontend"
        assert all(isinstance(chord, str) for chord in engine.tui.hotkeys)

    def test_rich_set_config_state_stores_hotkeys(self):
        tui = VoiceTranscriberTUI()
        tui.set_config_state(hotkeys=["alt+shift", "f13"])
        assert tui.hotkeys == ["alt+shift", "f13"]

    def test_rich_set_config_state_leaves_hotkeys_alone_when_not_given(self):
        tui = VoiceTranscriberTUI()
        tui.set_config_state(hotkeys=["f13"])
        tui.set_config_state(muted=False)
        assert tui.hotkeys == ["f13"]
