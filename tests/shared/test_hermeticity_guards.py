#!/usr/bin/env python3
"""The hermeticity guards must actually fire.

A guard nobody exercises is indistinguishable from no guard, and that is not a
hypothetical here. ``tests/shared/conftest.py`` grew a *second* guard because a
test documented as "model-free" silently loaded a 462 MiB model, ran real
inference and leaked a ``llama-server`` process on every run - and it survived
several green suites, because it only failed when the machine was quiet enough
to get past the formatter's 60 s spawn-retry cooldown. The original network guard
had never been exercised either.

So these tests provoke each guard on purpose. They are what makes a green run of
this tier evidence rather than decoration.

Each network test clears ``conftest._BLOCKED`` in a ``finally``: the fixture's
teardown treats a non-empty list as an *accidental* leak and fails the test, so a
deliberate provocation has to put the tally back.
"""
import socket
import subprocess
import sys

import pytest


def _shared_conftest():
    """The *already-loaded* ``tests/shared/conftest.py`` module.

    Found through ``sys.modules`` rather than ``import conftest``: pytest loads
    it under a rootdir-derived name, so a plain import fails - and re-importing
    it from its path would build a *second* module object whose guard classes
    are not the ones the fixtures actually raise, making every assertion below
    vacuously true.
    """
    for name, module in list(sys.modules.items()):
        if name.rsplit(".", 1)[-1] == "conftest" and hasattr(
                module, "NetworkAccessBlocked"):
            return module
    raise RuntimeError("tests/shared/conftest.py is not loaded")


guards = _shared_conftest()


# ---------------------------------------------------------------------------
# The process guard (X6): the shared tier must not spawn a real llama-server
# ---------------------------------------------------------------------------
def test_the_real_llama_server_spawn_guard_fires():
    """Spawning the production binary by bare name is refused."""
    with pytest.raises(guards.RealLlamaServerSpawnBlocked):
        subprocess.Popen(["llama-server", "--version"])


def test_the_spawn_guard_matches_the_basename_not_the_arguments():
    """The guard keys on the program, not on how it was invoked."""
    with pytest.raises(guards.RealLlamaServerSpawnBlocked):
        subprocess.Popen(["/run/current-system/sw/bin/llama-server"])
    with pytest.raises(guards.RealLlamaServerSpawnBlocked):
        subprocess.run(["llama-server", "--version"], check=False)


def test_the_spawn_guard_does_not_block_other_programs():
    """A guard that blocked everything would pass the tests above and break the
    stub servers the backend tests legitimately spawn."""
    proc = subprocess.Popen(["true"])
    assert proc.wait(timeout=30) == 0


# ---------------------------------------------------------------------------
# The network guard (X5): no non-loopback connection, ever
# ---------------------------------------------------------------------------
def test_the_network_guard_blocks_a_non_loopback_connect():
    try:
        with pytest.raises(guards.NetworkAccessBlocked):
            # A literal address, so the guard is what raises - not a resolver
            # failure being mistaken for the guard working.
            socket.create_connection(("1.1.1.1", 80), timeout=1)
    finally:
        guards._BLOCKED.clear()


def test_the_network_guard_blocks_connect_ex_too():
    sock = socket.socket()
    try:
        with pytest.raises(guards.NetworkAccessBlocked):
            sock.connect_ex(("1.1.1.1", 80))
    finally:
        sock.close()
        guards._BLOCKED.clear()


def test_the_network_guard_still_allows_loopback():
    """Loopback stays legal: the control-API tests need it."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=1)
        client.close()
    finally:
        server.close()
