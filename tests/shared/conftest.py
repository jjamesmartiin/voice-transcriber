"""Hermeticity guard for the shared (model-free) tier.

The shared tests must never touch the network. A leaked call is not merely slow:
``model_download`` will happily fetch the ~2.8 GB Cohere bundle from a real GitHub
release, which on a metered connection is expensive and on an airgapped host is
impossible. This autouse fixture turns any real non-loopback TCP connect into an
immediate, loud failure so a leak is caught in milliseconds instead of streaming
gigabytes from a CDN.

AF_UNIX sockets and loopback TCP are still permitted: the control-API tests use
them, and neither leaves the machine.
"""
import socket

import pytest


class NetworkAccessBlocked(BaseException):
    """Raised when a shared test attempts a non-loopback network connection.

    Deliberately a ``BaseException`` and not an ``Exception``: the installer's
    ``except Exception`` handlers would otherwise swallow it and silently fall
    through to a genuine download.
    """


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::", ""}

#: Every blocked address seen during the current test, cleared per test.
_BLOCKED: list = []


def _is_local(address) -> bool:
    """True for AF_UNIX addresses and loopback TCP addresses."""
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX path (str/bytes) or an empty address: no network
    host = address[0]
    return isinstance(host, str) and host in _LOOPBACK_HOSTS


def _block(kind, address):
    _BLOCKED.append(f"{kind}({address!r})")
    raise NetworkAccessBlocked(
        f"tests/shared must not touch the network: blocked {kind} to {address!r}")


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection
    _BLOCKED.clear()

    def guarded_connect(self, address):
        if not _is_local(address):
            _block("connect", address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        if not _is_local(address):
            _block("connect_ex", address)
        return real_connect_ex(self, address)

    def guarded_create_connection(address, *args, **kwargs):
        if not _is_local(address):
            _block("create_connection", address)
        return real_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)
    yield
    # A background thread's exception only surfaces as a warning; make the leak
    # a hard failure for the test that caused it.
    if _BLOCKED:
        attempts = ", ".join(_BLOCKED)
        _BLOCKED.clear()
        raise AssertionError(
            f"test attempted non-loopback network access: {attempts}")
