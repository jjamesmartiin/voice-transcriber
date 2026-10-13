"""Control API: transport, protocol, CLI, and engine verb coverage.

The control API (``src/control.py``) is how an external program -- or a shell --
drives a running engine: ``python src/main.py toggle``, ``status``, ``output
type`` and so on. These tests pin the parts that must not drift:

* the wire protocol (one JSON line each way, bare verbs allowed),
* that a bad/hostile client can never take the engine down,
* the CLI's exit codes (including "no verb means launch the app"),
* that every documented verb is either handled or explicitly rejected.
"""
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import control  # noqa: E402


#: Verbs that require a value, mapped to a valid one.
_VALUE_VERBS = {
    "output": "clipboard",
    "numbers": "auto",
    "punctuation": "full",
    "structure": "inline",
    "cleanup": "artifacts",
    "diarization": "on",
    "diarization-speakers": "auto",
    "diarization-model": "diarization",
    "meeting-output": "/tmp/vt-transcripts",
    "meeting-format": "text",
    "speakers": "Priya, Sam",
}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _FakeEngine:
    """Minimal stand-in exposing the one method ControlServer requires."""

    def __init__(self, *, explode: bool = False):
        self.calls = []
        self.explode = explode

    def handle_control(self, cmd, request):
        self.calls.append((cmd, request))
        if self.explode:
            raise RuntimeError("boom")
        return {"ok": True, "cmd": cmd}


#: ``sun_path`` in ``sockaddr_un`` is 104 bytes on macOS and 108 on Linux, and
#: ``bind()`` fails with ENAMETOOLONG past that. pytest's ``tmp_path`` on macOS is
#: ``/private/var/folders/<...>/T/pytest-of-<user>/pytest-0/<test name>/``, which
#: overflows it — so on macOS every socket test in this file failed with
#: ``start() is False`` and it looked like the control API was broken there. The
#: test harness was the problem, so the socket lives somewhere short instead.
@pytest.fixture
def sock_path():
    directory = tempfile.mkdtemp(prefix="vtsock-")
    try:
        yield Path(directory) / "c.sock"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _router(engine=None):
    """A server for *routing* tests only.

    The transport is pinned to AF_UNIX so these tests exercise the request
    protocol regardless of what the host would actually bind. On native Windows
    the default is loopback TCP, which correctly demands a token — and that is a
    transport concern, not a routing one.
    """
    return control.ControlServer(engine or _FakeEngine(), transport=control.UNIX)


def _server(sock_path, engine=None):
    return control.ControlServer(engine or _FakeEngine(), socket_path=str(sock_path))


# ---------------------------------------------------------------------------
# Protocol (no sockets involved)
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_bare_verb_is_accepted(self):
        """Hand-typed clients may send a bare verb instead of JSON."""
        engine = _FakeEngine()
        response = _router(engine).dispatch("toggle")
        assert response == {"ok": True, "cmd": "toggle"}
        assert engine.calls == [("toggle", {"cmd": "toggle"})]

    def test_json_request_fields_reach_the_handler(self):
        engine = _FakeEngine()
        _router(engine).dispatch('{"cmd": "output", "value": "type"}')
        cmd, request = engine.calls[0]
        assert cmd == "output"
        assert request["value"] == "type"

    def test_surrounding_whitespace_is_tolerated(self):
        engine = _FakeEngine()
        _router(engine).dispatch("  \n status \n ")
        assert engine.calls[0][0] == "status"

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "[1, 2, 3]",  # valid JSON, wrong shape
            '"just a string"',  # valid JSON, wrong shape
            '{"value": "no cmd here"}',
        ],
    )
    def test_malformed_requests_are_rejected_not_raised(self, line):
        response = _router().dispatch(line)
        assert response["ok"] is False
        assert "error" in response

    def test_handler_exception_becomes_an_error_reply(self):
        response = _router(_FakeEngine(explode=True)).dispatch("toggle")
        assert response["ok"] is False
        assert "boom" in response["error"]

    def test_target_without_handler_reports_clearly(self):
        class NoHandler:
            pass

        response = _router(NoHandler()).dispatch("toggle")
        assert response["ok"] is False
        assert "control handler" in response["error"]

    def test_a_wait_timeout_is_bounded(self):
        """A days-long wait must not pin a control thread for days."""
        engine = _FakeEngine()
        _router(engine).dispatch(json.dumps({"cmd": "wait", "value": "1000000"}))
        _, request = engine.calls[0]
        assert float(request["value"]) <= control.MAX_WAIT_SECONDS

    def test_an_overlong_speakers_value_is_rejected_not_truncated(self):
        """The speakers map is stored verbatim; cap it at the control layer."""
        engine = _FakeEngine()
        reply = _router(engine).dispatch(
            json.dumps({"cmd": "speakers", "value": "x" * 100_000})
        )
        assert reply["ok"] is False
        assert "limit" in reply["error"].lower()
        assert engine.calls == [], "an over-long value must not reach the engine"

    def test_an_overlong_hotkey_value_is_rejected(self):
        engine = _FakeEngine()
        reply = _router(engine).dispatch(
            json.dumps({"cmd": "hotkey", "value": "add " + "x" * 100_000})
        )
        assert reply["ok"] is False
        assert engine.calls == []

    def test_a_normal_speakers_value_still_reaches_the_handler(self):
        """The new cap must not touch an ordinary speaker list."""
        engine = _FakeEngine()
        _router(engine).dispatch(
            json.dumps({"cmd": "speakers", "value": "Priya, Sam"})
        )
        assert engine.calls[0][1]["value"] == "Priya, Sam"


# ---------------------------------------------------------------------------
# The opt-in meeting ceiling (VT_MEETING_MAX_SECONDS)
# ---------------------------------------------------------------------------
# Unset is the shipped behaviour: no timer, no log line, a meeting runs until
# stopped. Set, it bounds a forgotten capture without changing the default.
def _wait_for(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestMeetingCeiling:
    def test_the_ceiling_stops_an_overlong_meeting(self, monkeypatch, caplog):
        monkeypatch.setattr(control, "MIN_MEETING_MAX_SECONDS", 0.0, raising=False)
        monkeypatch.setenv("VT_MEETING_MAX_SECONDS", "0.05")
        engine = _FakeEngine()
        with caplog.at_level(logging.WARNING):
            _router(engine).dispatch("meeting-start")
            stopped = _wait_for(
                lambda: any(c[0] == "meeting-stop" for c in engine.calls)
            )
        assert stopped, engine.calls
        assert "VT_MEETING_MAX_SECONDS" in caplog.text

    def test_a_stale_timer_cannot_stop_a_later_meeting(self, monkeypatch):
        """start -> stop -> start: the first meeting's deadline must not touch
        the second. This is why the timer carries a generation, not just a
        cancel.
        """
        monkeypatch.setattr(control, "MIN_MEETING_MAX_SECONDS", 0.0, raising=False)
        monkeypatch.setenv("VT_MEETING_MAX_SECONDS", "3600")
        engine = _FakeEngine()
        server = _router(engine)
        server.dispatch("meeting-start")     # generation 1, timer armed
        server.dispatch("meeting-stop")      # timer cancelled
        server.dispatch("meeting-start")     # generation 2
        engine.calls.clear()
        # The cancelled timer can still win the race; run its callback with the
        # generation it was armed for, as if the deadline had passed.
        server._meeting_timeout(1)
        assert all(cmd != "meeting-stop" for cmd, _ in engine.calls), engine.calls
        server._cancel_meeting_watchdog()

    @pytest.mark.parametrize("bad", ["nonsense", "30", "-5", "nan", "inf"])
    def test_a_nonsense_ceiling_is_rejected_loudly(self, monkeypatch, bad):
        monkeypatch.setenv("VT_MEETING_MAX_SECONDS", bad)
        engine = _FakeEngine()
        reply = _router(engine).dispatch("meeting-start")
        assert reply["ok"] is False
        assert "VT_MEETING_MAX_SECONDS" in reply["error"]
        assert engine.calls == []

    def test_unset_changes_nothing(self, monkeypatch, caplog):
        """Unset must be inert: no timer armed, no new log output."""
        monkeypatch.delenv("VT_MEETING_MAX_SECONDS", raising=False)
        engine = _FakeEngine()
        server = _router(engine)
        with caplog.at_level(logging.WARNING):
            server.dispatch("meeting-start")
            assert server._meeting_timer is None
        assert "VT_MEETING_MAX_SECONDS" not in caplog.text

    def test_a_sane_ceiling_accepts_the_start(self, monkeypatch):
        monkeypatch.setenv("VT_MEETING_MAX_SECONDS", "3600")
        engine = _FakeEngine()
        server = _router(engine)
        try:
            assert server.dispatch("meeting-start")["ok"] is True
            assert server._meeting_timer is not None
        finally:
            server._cancel_meeting_watchdog()


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
#: The AF_UNIX transport needs ``socket.AF_UNIX``, which stock CPython on Windows
#: never exposes (bpo-33408) — an OS feature CPython did not wire up. Those tests
#: skip there; the TCP transport, which is what Windows uses instead, is covered
#: by :class:`TestTcpTransport` and runs on every platform.
requires_af_unix = pytest.mark.skipif(
    not control.unix_sockets_supported(),
    reason="socket.AF_UNIX is unavailable on this interpreter (stock CPython on Windows)",
)


@requires_af_unix
class TestSocketTransport:
    def test_start_is_idempotent_and_creates_the_socket(self, sock_path):
        server = _server(sock_path)
        assert server.start() is True
        try:
            assert os.path.exists(server.socket_path)
            assert server.start() is True  # second call is a no-op, not an error
        finally:
            server.stop()
        assert not os.path.exists(server.socket_path)

    def test_socket_permissions_are_restricted_to_owner(self, sock_path):
        """Control socket must be 0600 on POSIX so other users cannot connect."""
        if os.name != "posix":
            pytest.skip("POSIX only")
        import stat
        server = _server(sock_path)
        assert server.start() is True
        try:
            mode = stat.S_IMODE(os.stat(server.socket_path).st_mode)
            assert mode == 0o600, f"Expected 0600, got {oct(mode)}"
        finally:
            server.stop()

    def test_round_trip_over_a_real_socket(self, sock_path):
        engine = _FakeEngine()
        server = _server(sock_path, engine)
        assert server.start() is True
        try:
            response = control.send_command("status", socket_path=server.socket_path)
            assert response == {"ok": True, "cmd": "status"}
            assert engine.calls[0][0] == "status"
        finally:
            server.stop()

    def test_several_clients_are_served_concurrently(self, sock_path):
        """The engine socket is not single-client (unlike the TUI socket)."""
        engine = _FakeEngine()
        server = _server(sock_path, engine)
        assert server.start() is True
        try:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                results = list(
                    pool.map(
                        lambda _: control.send_command(
                            "ping", socket_path=server.socket_path
                        ),
                        range(8),
                    )
                )
            assert all(r["ok"] for r in results)
        finally:
            server.stop()

    def test_raw_line_client_is_supported(self, sock_path):
        """No JSON, no library: connect, write a verb, read a reply."""
        server = _server(sock_path)
        assert server.start() is True
        try:
            import socket as socketlib

            client = socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM)
            client.settimeout(5.0)
            client.connect(server.socket_path)
            client.sendall(b"toggle\n")
            reply = json.loads(client.recv(4096).decode().strip())
            client.close()
            assert reply["ok"] is True
        finally:
            server.stop()

    def test_send_command_without_an_engine_is_a_clean_failure(self, tmp_path):
        missing = str(tmp_path / "nobody-home.sock")
        response = control.send_command("toggle", socket_path=missing)
        assert response["ok"] is False
        assert "no running Voice Transcriber" in response["error"]

    def test_an_explicit_path_implies_the_unix_transport(self, sock_path):
        server = _server(sock_path)
        assert server.transport == control.UNIX

    def test_connections_past_the_cap_are_closed(self, sock_path, monkeypatch):
        """A local process must not be able to pin unbounded engine threads."""
        gate = threading.Event()
        entered = threading.Event()

        class BlockingEngine(_FakeEngine):
            def handle_control(self, cmd, request):
                entered.set()
                gate.wait(5.0)
                return {"ok": True, "cmd": cmd}

        # One slot: the first connection takes it and blocks in the handler, so
        # the second must be refused instead of spawning another thread.
        monkeypatch.setattr(control, "_CONNECTION_SLOTS", threading.Semaphore(1))
        server = _server(sock_path, BlockingEngine())
        assert server.start() is True
        first = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        first.settimeout(5.0)
        first.connect(server.socket_path)
        first.sendall(b"status\n")
        try:
            assert entered.wait(5.0), "the first client never reached the handler"
            second = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            second.settimeout(5.0)
            second.connect(server.socket_path)
            assert second.recv(4096) == b""  # refused and closed
            second.close()
        finally:
            gate.set()
            try:
                first.recv(4096)  # let the handler finish before we restore
            except OSError:
                pass
            first.close()
            server.stop()

    def test_slow_clients_release_slots_by_deadline(self, sock_path, monkeypatch):
        """Unauthenticated clients trickling bytes must not hold slots past the wall-clock deadline."""
        monkeypatch.setattr(control, "DEFAULT_TIMEOUT", 0.2)
        server = _server(sock_path, _FakeEngine())
        assert server.start() is True

        stop_trickle = threading.Event()
        clients = []

        def trickle(s):
            try:
                while not stop_trickle.is_set():
                    s.sendall(b" ")
                    time.sleep(0.08)
            except OSError:
                pass

        threads = []
        try:
            # Fill all connection slots with slow trickling clients
            for _ in range(control.MAX_CONCURRENT_CONNECTIONS):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(server.socket_path)
                clients.append(s)
                t = threading.Thread(target=trickle, args=(s,), daemon=True)
                t.start()
                threads.append(t)

            # Wait past the wall-clock deadline (0.2s)
            time.sleep(0.35)

            # A legitimate client must now succeed because trickling clients were cut off
            legit = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            legit.settimeout(1.0)
            legit.connect(server.socket_path)
            legit.sendall(b"status\n")
            raw = legit.recv(4096)
            res = json.loads(raw.decode("utf-8"))
            assert res.get("ok") is True
            legit.close()
        finally:
            stop_trickle.set()
            for s in clients:
                try:
                    s.close()
                except OSError:
                    pass
            for t in threads:
                t.join(timeout=0.1)
            server.stop()

    def test_stop_does_not_unlink_regular_file(self, sock_path):
        """stop() must only unlink socket files, never arbitrary regular files."""
        sock_path.write_text("regular file", encoding="utf-8")
        server = _server(sock_path, _FakeEngine())
        server.stop()
        assert sock_path.exists(), "stop() must not unlink a regular file"
        assert sock_path.read_text(encoding="utf-8") == "regular file"



class TestSocketPathLength:
    """``sun_path`` is 104 bytes on macOS and 108 on Linux, NUL included.

    Past that, ``bind()`` fails with ENAMETOOLONG. That is what made the macOS CI
    job red: pytest's ``tmp_path`` there is ``/private/var/folders/...``, which
    overflows the limit, so every socket test failed with ``start() is False``
    and it looked like the control API was broken on macOS.
    """

    def test_the_default_path_fits_in_sun_path(self):
        """The derived path must be bindable on the strictest platform."""
        assert len(control.default_socket_path().encode()) <= 103

    @requires_af_unix
    def test_an_over_long_path_fails_cleanly_and_says_why(self, caplog):
        server = control.ControlServer(_FakeEngine(), socket_path="/tmp/" + "x" * 200 + ".sock")
        with caplog.at_level("WARNING"):
            assert server.start() is False  # never raises into the app
        assert "Control API unavailable" in caplog.text

    @requires_af_unix
    def test_the_short_fixture_path_is_actually_bindable(self, sock_path):
        """Guards the fixture above: if it ever grows past the limit, the socket
        tests would start failing with a misleading 'start() is False' again."""
        assert len(str(sock_path).encode()) <= 103


class TestTransportSelection:
    """Which transport this host serves over, and why.

    AF_UNIX wherever it exists — the socket is addressed by a path, so filesystem
    permissions are the whole access-control story and nothing touches the network
    stack. Native Windows falls back to loopback TCP, which is what makes the
    control API available there at all.
    """

    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch):
        monkeypatch.delenv(control.CONTROL_TRANSPORT_ENV, raising=False)

    def test_unix_is_preferred_when_available(self, monkeypatch):
        monkeypatch.setattr(control, "unix_sockets_supported", lambda: True)
        assert control.default_transport() == control.UNIX

    def test_tcp_is_used_when_af_unix_is_absent(self, monkeypatch):
        monkeypatch.setattr(control, "unix_sockets_supported", lambda: False)
        assert control.default_transport() == control.TCP

    def test_a_socket_path_does_not_disable_the_api_without_af_unix(self, monkeypatch):
        """VT_CONTROL_SOCKET must not force AF_UNIX where the OS lacks it."""
        monkeypatch.setattr(control, "unix_sockets_supported", lambda: False)
        monkeypatch.setenv(control.CONTROL_SOCKET_ENV, "/tmp/whatever.sock")
        assert control.default_transport() == control.TCP

    def test_the_transport_can_be_forced(self, monkeypatch):
        monkeypatch.setenv(control.CONTROL_TRANSPORT_ENV, "tcp")
        assert control.default_transport() == control.TCP
        monkeypatch.setenv(control.CONTROL_TRANSPORT_ENV, "unix")
        assert control.default_transport() == control.UNIX

    def test_an_unknown_transport_name_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setattr(control, "unix_sockets_supported", lambda: True)
        monkeypatch.setenv(control.CONTROL_TRANSPORT_ENV, "carrier-pigeon")
        assert control.default_transport() == control.UNIX

    def test_the_control_api_is_available_without_af_unix(self, monkeypatch):
        """The platform capability is now about *a* transport, not AF_UNIX."""
        monkeypatch.setattr(control, "unix_sockets_supported", lambda: False)
        assert control.socket_supported() is True

    def test_unix_sockets_supported_asks_the_interpreter(self):
        assert control.unix_sockets_supported() == hasattr(socket, "AF_UNIX")


class TestTcpTransport:
    """The loopback transport that native Windows uses — exercised everywhere.

    Loopback is not a security boundary on a shared machine, so every request
    carries a token that stands in for the AF_UNIX socket's file permissions.
    """

    @pytest.fixture
    def endpoint_file(self, tmp_path, monkeypatch):
        path = tmp_path / "endpoint.json"
        monkeypatch.setattr(control, "default_endpoint_file", lambda: str(path))
        # The client chooses its transport the same way the server does, so force
        # it here too — otherwise send_command would (correctly) prefer the
        # AF_UNIX socket on a host that has one.
        monkeypatch.setenv(control.CONTROL_TRANSPORT_ENV, control.TCP)
        return path

    @pytest.fixture
    def server(self, endpoint_file):
        engine = _FakeEngine()
        server = control.ControlServer(
            engine, transport=control.TCP, endpoint_file=str(endpoint_file)
        )
        assert server.start() is True
        try:
            yield server
        finally:
            server.stop()

    def test_round_trip(self, server, endpoint_file):
        response = control.send_command("status")
        assert response == {"ok": True, "cmd": "status"}

    def test_it_binds_loopback_only(self, server):
        """A control API reachable off-host would be a security bug."""
        assert server.host == control.TCP_HOST
        assert server._server.getsockname()[0] == "127.0.0.1"

    def test_it_publishes_host_port_and_token(self, server, endpoint_file):
        published = json.loads(endpoint_file.read_text())
        assert published["transport"] == control.TCP
        assert published["host"] == "127.0.0.1"
        assert published["port"] == server.port
        assert published["token"] == server.token

    def test_the_endpoint_file_is_owner_only(self, server, endpoint_file):
        if os.name != "posix":
            pytest.skip("POSIX modes only")
        import stat
        mode = stat.S_IMODE(os.stat(endpoint_file).st_mode)
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_a_planted_endpoint_path_is_replaced_not_written_through(
            self, endpoint_file, tmp_path):
        """The endpoint lives in the shared temp dir, so a pre-existing path is
        attacker-controlled.

        Regression: ``os.open(path, O_CREAT | O_TRUNC)`` follows a symlink, so
        another local user could point the name at any file this user can write
        and have it truncated and overwritten with the endpoint JSON. Writing a
        private temp file and renaming it into place swaps the *name* instead.
        """
        victim = tmp_path / "victim.txt"
        victim.write_text("do not touch")
        if os.name == "posix":
            endpoint_file.symlink_to(victim)
        else:
            endpoint_file.write_text("do not touch")

        server = control.ControlServer(
            _FakeEngine(), transport=control.TCP, endpoint_file=str(endpoint_file)
        )
        assert server.start() is True
        try:
            assert victim.read_text() == "do not touch"
            published = json.loads(endpoint_file.read_text())
            assert published["token"] == server.token
        finally:
            server.stop()

    def test_read_endpoint_refuses_non_loopback_host(self, endpoint_file):
        endpoint_file.write_text(json.dumps({"port": 1234, "host": "127.0.0.2", "token": "tok"}))
        assert control.read_endpoint_file(str(endpoint_file)) is None

    def test_read_endpoint_refuses_foreign_owner(self, endpoint_file, monkeypatch):
        if not hasattr(os, "getuid"):
            pytest.skip("POSIX only")
        endpoint_file.write_text(json.dumps({"port": 1234, "host": "127.0.0.1", "token": "tok"}))
        real_lstat = os.lstat
        def fake_lstat(path):
            st = real_lstat(path)
            if str(path) == str(endpoint_file):
                return os.stat_result((st.st_mode, st.st_ino, st.st_dev, st.st_nlink,
                                       os.getuid() + 100, st.st_gid, st.st_size,
                                       st.st_atime, st.st_mtime, st.st_ctime))
            return st
        monkeypatch.setattr(os, "lstat", fake_lstat)
        assert control.read_endpoint_file(str(endpoint_file)) is None

    def test_read_endpoint_refuses_world_readable_file(self, endpoint_file):
        if os.name != "posix":
            pytest.skip("POSIX only")
        endpoint_file.write_text(json.dumps({"port": 1234, "host": "127.0.0.1", "token": "tok"}))
        os.chmod(endpoint_file, 0o644)
        assert control.read_endpoint_file(str(endpoint_file)) is None

    def test_windows_uses_exclusive_addr_use(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock
        fake_sock = MagicMock()
        fake_sock.getsockname.return_value = ("127.0.0.1", 1234)
        fake_sock.accept.side_effect = socket.timeout
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", 4, raising=False)
        monkeypatch.setattr(socket, "socket", lambda *a, **k: fake_sock)
        ep = tmp_path / "ep.json"
        server = control.ControlServer(_FakeEngine(), transport=control.TCP, endpoint_file=str(ep))
        server._start_tcp()
        fake_sock.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)

    def test_the_endpoint_is_removed_on_stop(self, endpoint_file):
        server = control.ControlServer(
            _FakeEngine(), transport=control.TCP, endpoint_file=str(endpoint_file)
        )
        assert server.start() is True
        assert endpoint_file.exists()
        server.stop()
        assert not endpoint_file.exists()

    def test_a_request_without_the_token_is_rejected(self, server):
        reply = server.dispatch(json.dumps({"cmd": "status"}))
        assert reply["ok"] is False
        assert reply["error"] == "unauthorized"

    def test_a_request_with_the_wrong_token_is_rejected(self, server):
        reply = server.dispatch(json.dumps({"cmd": "status", "token": "not-it"}))
        assert reply["ok"] is False
        assert reply["error"] == "unauthorized"

    def test_a_non_ascii_token_is_rejected_not_raised(self, server):
        """``compare_digest`` raises on non-ASCII str; it must answer instead."""
        reply = server.dispatch(json.dumps({"cmd": "status", "token": "t\u00f6k\u00e9n"}))
        assert reply["ok"] is False
        assert reply["error"] == "unauthorized"

    def test_a_request_with_the_token_is_served(self, server):
        reply = server.dispatch(json.dumps({"cmd": "status", "token": server.token}))
        assert reply["ok"] is True

    def test_the_token_is_not_recoverable_from_the_rejection(self, server):
        """A probe must not be able to learn anything from the reply."""
        reply = server.dispatch(json.dumps({"cmd": "status", "token": ""}))
        assert server.token not in json.dumps(reply)

    def test_a_missing_endpoint_is_a_clean_error(self, endpoint_file):
        assert not endpoint_file.exists()
        response = control.send_command("status")
        assert response["ok"] is False
        assert "no running Voice Transcriber" in response["error"]

    def test_a_corrupt_endpoint_file_is_a_clean_error(self, endpoint_file):
        endpoint_file.write_text("{not json")
        response = control.send_command("status")
        assert response["ok"] is False
        assert "no running Voice Transcriber" in response["error"]

        # A structurally-valid file with a non-integer port must not raise
        # ``ValueError`` out of ``send_command``.
        endpoint_file.write_text('{"port": "abc", "token": "x"}')
        response = control.send_command("status")
        assert response["ok"] is False
        assert "no running Voice Transcriber" in response["error"]

        # ``isinstance(True, int)`` is True, so a JSON ``true`` port is rejected
        # explicitly, and the host must be a name ``connect`` can use.
        endpoint_file.write_text('{"port": true, "host": "127.0.0.1", "token": "x"}')
        assert control.read_endpoint_file(str(endpoint_file)) is None
        endpoint_file.write_text('{"port": 1234, "host": ["a"], "token": "x"}')
        assert control.read_endpoint_file(str(endpoint_file)) is None

    def test_the_token_never_reaches_the_engine(self, endpoint_file):
        """The token is transport plumbing, not part of the verb surface."""
        seen = {}

        class RecordingEngine:
            def handle_control(self, cmd, request):
                seen.update(request)
                return {"ok": True, "cmd": cmd}

        server = control.ControlServer(
            RecordingEngine(), transport=control.TCP, endpoint_file=str(endpoint_file)
        )
        assert server.start() is True
        try:
            control.send_command("status")
        finally:
            server.stop()
        assert seen.get("cmd") == "status"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class TestCli:
    def test_no_verb_means_launch_the_app(self):
        """`python src/main.py` must keep falling through to the normal launch."""
        assert control.run_cli([]) is None

    def test_help_exits_zero_and_lists_every_verb(self, capsys):
        assert control.run_cli(["help"]) == 0
        out = capsys.readouterr().out
        for verb in control.VERBS:
            assert verb in out

    def test_unknown_verb_exits_two(self, capsys):
        assert control.run_cli(["definitely-not-a-verb"]) == 2
        assert "Unknown control verb" in capsys.readouterr().err

    def test_underscores_are_normalised_to_dashes(self, tmp_path, capsys):
        """`set_mic` and `set-mic` address the same verb."""
        code = control.run_cli(["set_mic", "--socket", str(tmp_path / "absent.sock")])
        assert code == 1  # recognised verb; it just found no engine
        assert "Unknown control verb" not in capsys.readouterr().err

    @requires_af_unix
    def test_client_exits_one_when_no_engine_is_listening(self, tmp_path, capsys):
        code = control.run_cli(
            ["status", "--socket", str(tmp_path / "absent.sock")]
        )
        assert code == 1
        assert "no running Voice Transcriber" in capsys.readouterr().err

    @requires_af_unix
    def test_json_flag_prints_the_raw_reply(self, sock_path, capsys):
        server = _server(sock_path)
        assert server.start() is True
        try:
            code = control.run_cli(
                ["status", "--json", "--socket", server.socket_path]
            )
            assert code == 0
            payload = json.loads(capsys.readouterr().out)
            assert payload == {"ok": True, "cmd": "status"}
        finally:
            server.stop()

    @requires_af_unix
    def test_socket_path_can_come_from_the_environment(self, sock_path, monkeypatch):
        server = _server(sock_path)
        assert server.start() is True
        try:
            monkeypatch.setenv(control.CONTROL_SOCKET_ENV, server.socket_path)
            assert control.default_socket_path() == server.socket_path
            assert control.send_command("ping")["ok"] is True
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Engine verb coverage
# ---------------------------------------------------------------------------
class TestEngineVerbs:
    """Drive a real ``SimpleVoiceTranscriber`` without starting audio or TUI."""

    @pytest.fixture()
    def engine(self, tmp_path, monkeypatch):
        main = pytest.importorskip("main")
        t2 = pytest.importorskip("t2")
        # Never touch the user's real config file.
        monkeypatch.setattr(t2, "CONFIG_FILE", str(tmp_path / "config.yaml"))
        from unittest.mock import MagicMock

        engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
        engine.tui = MagicMock()
        engine.tui.state = "READY"
        engine.recording = False
        engine.hotkey_system = None
        return engine

    def test_ping_and_status_report_engine_state(self, engine):
        assert engine.handle_control("ping", {})["pid"] == os.getpid()

        status = engine.handle_control("status", {})
        assert status["ok"] is True
        assert status["recording"] is False
        assert status["state"] == "READY"
        for key in ("device", "model", "muted", "output_mode", "number_mode"):
            assert key in status

    def test_help_lists_the_documented_verbs(self, engine):
        response = engine.handle_control("help", {})
        assert response["verbs"] == control.VERBS

    def test_unknown_verb_is_rejected(self, engine):
        with pytest.raises(ValueError, match="unknown control verb"):
            engine.handle_control("make-me-a-sandwich", {})

    @pytest.mark.parametrize(
        "verb,value,key",
        [
            ("output", "type", "output_mode"),
            ("output", "clipboard", "output_mode"),
            ("numbers", "digits", "number_mode"),
            ("numbers", "auto", "number_mode"),
            ("punctuation", "no_punctuation", "punctuation_mode"),
            ("trailing-space", "off", "trailing_space"),
            ("auto-punctuate", "off", "auto_punctuate"),
            ("serial", "off", "serial_collapse"),
            ("spell", "off", "spell_command"),
            ("middle-click", "on", "middle_click"),
            ("wpm", "60", "typing_wpm"),
        ],
    )
    def test_setters_apply_and_echo_the_new_value(self, engine, verb, value, key):
        response = engine.handle_control(verb, {"value": value})
        assert response["ok"] is True
        assert key in response

    @pytest.mark.parametrize(
        "verb,value",
        [
            ("diarization", "on"),
            ("diarization-speakers", "3"),
            ("diarization-model", "diarization"),
            ("meeting-format", "json"),
            ("meeting-output", "meetings-out"),
        ],
    )
    def test_new_setting_verbs_save_the_config_exactly_once(
        self, engine, verb, value, monkeypatch
    ):
        """The setters own the save; the control layer must not save again."""
        import t2

        saves = []
        monkeypatch.setattr(t2, "save_audio_config", lambda: saves.append(1))

        response = engine.handle_control(verb, {"value": value})

        assert response["ok"] is True
        assert len(saves) == 1, f"{verb} saved the config {len(saves)} times"

    def test_mute_toggles_without_a_value(self, engine):
        import t2

        before = t2.IS_MUTED
        engine.handle_control("mute", {})
        assert t2.IS_MUTED is not before

    def test_wpm_cycles_without_a_value(self, engine):
        import t2

        engine.handle_control("wpm", {"value": "40"})
        assert t2.TYPING_WPM == 40
        resp = engine.handle_control("wpm", {})
        assert resp["ok"] is True
        assert resp["typing_wpm"] == 50
        assert t2.TYPING_WPM == 50

    def test_setters_require_a_value(self, engine):
        with pytest.raises(ValueError, match="needs a value"):
            engine.handle_control("output", {})

    def test_bad_on_off_value_is_rejected(self, engine):
        with pytest.raises(ValueError, match="expected on/off"):
            engine.handle_control("spell", {"value": "maybe"})

    def test_rescan_mics_reports_devices_and_holders(self, engine, monkeypatch):
        """`rescan-mics` is the scriptable form of the settings item."""
        t2 = pytest.importorskip("t2")
        monkeypatch.setattr(
            t2,
            "rescan_audio_devices",
            lambda: {
                "ok": True,
                "count": 5,
                "devices": ["default", "pipewire"],
                "device": "default",
                "missing": [{"name": "Blue Snowball", "holder": "GNOME Settings"}],
                "notice": "Blue Snowball is being used by GNOME Settings",
                "message": "5 input devices found · still missing: GNOME Settings",
            },
        )

        reply = engine.handle_control("rescan-mics", {})

        assert reply["ok"] is True
        assert reply["devices"] == ["default", "pipewire"]
        assert reply["device"] == "default"
        assert reply["missing"][0]["holder"] == "GNOME Settings"

    def test_set_mic_without_a_match_reports_the_failure(self, engine):
        """A substring that matches nothing must error, not silently no-op."""
        with pytest.raises(ValueError, match="no input device matching"):
            engine.handle_control("set-mic", {"value": "zzz-not-a-real-device-zzz"})

    def test_formatter_download_is_explicit_consent(self, engine, monkeypatch):
        """The first-use path will not fetch under a TUI; this verb will.

        It must reach the backend with ``consented=True`` - otherwise it would hit
        the same refusal it exists to bypass.
        """
        from voice_transcriber.formatters import llama_server
        t2 = pytest.importorskip("t2")

        calls = []
        monkeypatch.setattr(
            llama_server, "request_model",
            lambda model=None, **kw: calls.append((model, kw)) or True)

        reply = engine.handle_control("formatter-download", {})

        assert reply["ok"] is True
        assert reply["formatter_download"] is True
        assert calls == [(t2.get_formatter_model(), {"consented": True})]

    def test_every_documented_verb_is_recognised(self, engine):
        """Guard against docs drift: no documented verb may be unknown.

        Verbs that would touch hardware or block on a modal are exercised only
        for *recognition* here -- the unknown-verb error must not be raised.
        """
        blocking = {
            "start", "stop", "toggle", "settings", "mic", "theme",
            "reset-defaults", "reset-terminal", "quit", "set-mic", "mics",
            "rescan-mics", "meeting-start", "meeting-stop", "formatter-download",
        }
        for verb in control.VERBS:
            if verb in blocking:
                continue
            request = {"value": _VALUE_VERBS[verb]} if verb in _VALUE_VERBS else {}
            try:
                engine.handle_control(verb, request)
            except ValueError as exc:  # pragma: no cover - failure path
                assert "unknown control verb" not in str(exc), verb


class TestEngineWiring:
    def test_control_socket_binds_after_the_state_its_verbs_use(self):
        """Regression: binding the socket too early broke `start`.

        ``ControlServer`` is started inside ``SimpleVoiceTranscriber.__init__``.
        Every attribute a control verb touches must already exist by then:
        ``start_recording`` reads ``_model_ready_event`` and
        ``visual_notification``, so binding the socket before those were created
        let a perfectly valid ``start`` command fail with
        ``AttributeError: 'SimpleVoiceTranscriber' object has no attribute
        '_model_ready_event'``.
        """
        main = pytest.importorskip("main")
        source = Path(main.__file__).read_text(encoding="utf-8")
        init = source.split("    def __init__(self):", 1)[1].split(
            "    def _safe_tui_start", 1
        )[0]

        bind = "self.control_server = control.ControlServer(self)"
        assert bind in init
        bind_at = init.index(bind)

        for dependency in (
            "self._model_ready_event =",
            "self.visual_notification =",
            "self.audio_cues =",
            "self.tui =",
            "self.recording =",
            "self.hotkey_system = None",
        ):
            assert dependency in init, f"{dependency} not found in __init__"
            assert init.index(dependency) < bind_at, (
                f"{dependency} must be assigned before the control socket binds, "
                "otherwise an early control verb races against __init__"
            )


# ---------------------------------------------------------------------------
# The machine-readable catalogue (what an LLM agent reads first)
# ---------------------------------------------------------------------------
class TestVerbCatalogue:
    def test_every_verb_documents_itself(self):
        for name, spec in control.VERBS.items():
            assert spec.get("summary"), f"{name} has no summary"
            assert name == control.normalize_verb(name), f"{name} is not canonical"

    def test_choices_are_declared_where_the_set_is_closed(self):
        assert control.VERBS["output"]["choices"] == ["clipboard", "type", "type_fast"]
        assert control.VERBS["numbers"]["choices"] == ["auto", "digits", "words"]
        assert control.VERBS["middle-click"]["choices"] == ["on", "off"]
        assert control.VERBS["structure"]["choices"] == ["off", "inline", "blocks"]
        assert control.VERBS["cleanup"]["choices"] == ["off", "artifacts", "full"]
        assert control.VERBS["meeting"]["choices"] == ["off", "on"]
        # A free-text value must not claim a closed set.
        assert "choices" not in control.VERBS["set-mic"]
        assert "choices" not in control.VERBS["theme"] or "auto" in control.VERBS["theme"]["choices"]

    def test_required_and_toggling_verbs_are_marked(self):
        assert control.VERBS["output"]["required"] is True
        assert control.VERBS["set-mic"]["required"] is True
        assert "required" not in control.VERBS["start"]
        assert control.VERBS["mute"]["toggles"] is True
        assert control.VERBS["spell"]["toggles"] is True
        assert control.VERBS["structure"]["required"] is True
        assert control.VERBS["cleanup"]["required"] is True
        assert control.VERBS["meeting"]["toggles"] is True

    def test_meeting_verbs_are_in_the_catalogue(self):
        """Meeting mode is scriptable regardless of the UI (plan sec 12.4)."""
        assert control.VERBS["meeting"]["value"] == "state"
        assert control.VERBS["meeting-spill"]["value"] == "minutes"
        assert "required" not in control.VERBS["meeting"]
        assert control.VERBS["meeting-start"]["returns"] == [
            "meeting_state", "meeting_elapsed_s", "meeting_progress"
        ]
        assert "meeting-stop" in control.VERBS
        for key in (
            "meeting", "meeting_setting", "meeting_state", "meeting_elapsed_s",
            "meeting_progress", "meeting_spill_minutes",
        ):
            assert key in control.VERBS["status"]["returns"]

    def test_diarization_verbs_are_in_the_catalogue(self):
        """Speaker labels are scriptable, and the status advertises the keys."""
        assert control.VERBS["diarization"]["choices"] == ["off", "on"]
        assert control.VERBS["diarization"]["toggles"] is True
        assert control.VERBS["diarization-speakers"]["choices"] == [
            "auto", "2", "3", "4", "5", "6", "7", "8",
        ]
        assert control.VERBS["diarization-model"]["required"] is True
        for key in (
            "diarization", "diarization_setting", "diarization_speakers",
            "diarization_model",
        ):
            assert key in control.VERBS["status"]["returns"]

    def test_meeting_artifact_and_speaker_verbs_are_in_the_catalogue(self):
        """D5: the output location/form and the speakers map are scriptable."""
        assert control.VERBS["meeting-output"]["value"] == "folder"
        assert control.VERBS["meeting-format"]["choices"] == ["text", "json", "markdown"]
        assert control.VERBS["speakers"]["value"] == "names"
        for key in ("meeting_output_dir", "meeting_output_format", "speakers"):
            assert key in control.VERBS["status"]["returns"]

    def test_structure_verb_reports_both_modes(self):
        """The reply distinguishes the configured mode from the effective one.

        They differ while typing: "blocks" emits real line breaks, and a newline
        is an Enter keypress, so the engine types "inline" instead.
        """
        spec = control.VERBS["structure"]
        assert spec["returns"] == ["structure_mode", "structure_setting"]
        assert "structure_mode" in control.VERBS["status"]["returns"]
        assert "structure_setting" in control.VERBS["status"]["returns"]

    def test_cleanup_verb_is_in_the_catalogue(self):
        """The cleanup modes are part of the verb surface, like every setting."""
        spec = control.VERBS["cleanup"]
        assert spec["value"] == "mode"
        assert spec["returns"] == ["cleanup_mode"]
        assert "cleanup_mode" in control.VERBS["status"]["returns"]

    def test_normalize_verb_is_forgiving(self):
        assert control.normalize_verb("  SET_MIC ") == "set-mic"
        assert control.normalize_verb("Output") == "output"
        assert control.verb_spec("set_mic") == control.VERBS["set-mic"]
        assert control.verb_spec("nope") == {}

    def test_cli_help_lists_summaries_and_choices(self, capsys):
        assert control.run_cli(["help"]) == 0
        out = capsys.readouterr().out
        assert "clipboard|type|type_fast" in out
        assert "value required" in out
        assert "omit to toggle" in out


class TestStatusAndWait:
    """`status`/`wait` are how a caller reads results back."""

    @pytest.fixture()
    def engine(self, tmp_path, monkeypatch):
        main = pytest.importorskip("main")
        t2 = pytest.importorskip("t2")
        monkeypatch.setattr(t2, "CONFIG_FILE", str(tmp_path / "config.yaml"))
        from unittest.mock import MagicMock

        engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
        engine.tui = MagicMock()
        engine.tui.state = "READY"
        engine.recording = False
        engine.hotkey_system = None
        engine.last_transcription = ""
        return engine

    def test_status_exposes_the_last_transcript(self, engine):
        engine.last_transcription = "I deployed on NixOS using kubectl."
        status = engine.handle_control("status", {})
        assert status["last_transcription"] == "I deployed on NixOS using kubectl."

    def test_status_never_leaks_a_missing_attribute(self, engine):
        del engine.last_transcription  # e.g. a partially-constructed engine
        assert engine.handle_control("status", {})["last_transcription"] == ""

    def test_wait_returns_immediately_when_idle(self, engine):
        response = engine.handle_control("wait", {"value": "5"})
        assert response["ok"] is True
        assert response["timed_out"] is False

    def test_wait_blocks_while_processing_then_reports(self, engine):
        import threading

        engine.tui.state = "PROCESSING"
        engine.last_transcription = "done"

        def finish():
            import time

            time.sleep(0.15)
            engine.tui.state = "READY"

        threading.Thread(target=finish, daemon=True).start()
        response = engine.handle_control("wait", {"value": "5"})
        assert response["timed_out"] is False
        assert response["last_transcription"] == "done"

    def test_wait_times_out_without_hanging(self, engine):
        engine.tui.state = "PROCESSING"
        response = engine.handle_control("wait", {"value": "0.2"})
        assert response["timed_out"] is True

    def test_wait_rejects_a_non_numeric_timeout(self, engine):
        with pytest.raises(ValueError, match="wait expects seconds"):
            engine.handle_control("wait", {"value": "soon"})
