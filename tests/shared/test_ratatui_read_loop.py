#!/usr/bin/env python3
"""The ratatui frontend's command read loop.

Regression: the loop read with ``makefile()`` on a socket carrying a 1s timeout,
which raises ``OSError("cannot read from timed out object")`` rather than
``socket.timeout``. The poll never fired, so one idle second after the TUI
connected the loop died and its ``finally`` read that as "the user quit" —
``on_quit`` -> ``os._exit(0)``. The app vanished with no UI, no error message
and exit code 0.
"""
import json
import socket
import sys
import threading
import time
import types
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import pytest
from tui_ratatui import RatatuiTui


def make_tui(conn, exited=False):
    """A RatatuiTui with only the IPC state the read loop touches."""
    tui = RatatuiTui.__new__(RatatuiTui)
    tui._conn = conn
    tui._closed = False
    tui._proc = types.SimpleNamespace(poll=lambda: 1 if exited else None)
    tui.on_quit = None
    return tui


@pytest.mark.slow
def test_idle_socket_is_not_a_quit_request():
    local, peer = socket.socketpair()
    tui = make_tui(local)
    commands = []
    tui.on_toggle_record = lambda: commands.append("toggle_record")
    quits = []
    tui.on_quit = lambda: quits.append(True)

    reader = threading.Thread(target=tui._read_loop, daemon=True)
    reader.start()
    try:
        peer.sendall(json.dumps({"t": "cmd", "cmd": "toggle_record"}).encode() + b"\n")
        deadline = time.monotonic() + 5.0
        while not commands and time.monotonic() < deadline:
            time.sleep(0.02)
        assert commands == ["toggle_record"]

        # Idle for several poll intervals: the loop must outlive them.
        time.sleep(2.5)
        assert quits == [], "an idle socket was treated as a quit"
        assert reader.is_alive(), "the read loop died on an idle socket"
    finally:
        tui._closed = True
        reader.join(timeout=3.0)
        local.close()
        peer.close()


def test_a_frontend_that_really_exited_still_quits():
    local, peer = socket.socketpair()
    tui = make_tui(local, exited=True)
    quits = []
    tui.on_quit = lambda: quits.append(True)

    reader = threading.Thread(target=tui._read_loop, daemon=True)
    reader.start()
    try:
        peer.close()  # EOF on the socket
        reader.join(timeout=5.0)
        assert quits == [True]
    finally:
        local.close()
