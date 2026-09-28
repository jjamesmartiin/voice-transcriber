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
import os
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import control  # noqa: E402


#: Verbs that require a value, mapped to a valid one.
_VALUE_VERBS = {
    "output": "clipboard",
    "numbers": "auto",
    "punctuation": "full",
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


def _server(tmp_path, engine=None):
    return control.ControlServer(
        engine or _FakeEngine(), socket_path=str(tmp_path / "vt-control-test.sock")
    )


# ---------------------------------------------------------------------------
# Protocol (no sockets involved)
# ---------------------------------------------------------------------------
class TestDispatch:
    def test_bare_verb_is_accepted(self):
        """Hand-typed clients may send a bare verb instead of JSON."""
        engine = _FakeEngine()
        response = control.ControlServer(engine).dispatch("toggle")
        assert response == {"ok": True, "cmd": "toggle"}
        assert engine.calls == [("toggle", {"cmd": "toggle"})]

    def test_json_request_fields_reach_the_handler(self):
        engine = _FakeEngine()
        control.ControlServer(engine).dispatch('{"cmd": "output", "value": "type"}')
        cmd, request = engine.calls[0]
        assert cmd == "output"
        assert request["value"] == "type"

    def test_surrounding_whitespace_is_tolerated(self):
        engine = _FakeEngine()
        control.ControlServer(engine).dispatch("  \n status \n ")
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
        response = control.ControlServer(_FakeEngine()).dispatch(line)
        assert response["ok"] is False
        assert "error" in response

    def test_handler_exception_becomes_an_error_reply(self):
        response = control.ControlServer(_FakeEngine(explode=True)).dispatch("toggle")
        assert response["ok"] is False
        assert "boom" in response["error"]

    def test_target_without_handler_reports_clearly(self):
        class NoHandler:
            pass

        response = control.ControlServer(NoHandler()).dispatch("toggle")
        assert response["ok"] is False
        assert "control handler" in response["error"]


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------
#: ``socket.AF_UNIX`` is absent from stock CPython on Windows: AF_UNIX is an OS
#: feature (Windows 10 1803+) that CPython never exposed (bpo-33408). On such an
#: interpreter the whole control transport is unavailable, so these tests skip
#: instead of failing. The degradation path itself is pinned on every platform
#: by :class:`TestUnsupportedTransport` below.
requires_af_unix = pytest.mark.skipif(
    not control.socket_supported(),
    reason="socket.AF_UNIX is unavailable on this interpreter (stock CPython on Windows)",
)


@requires_af_unix
class TestSocketTransport:
    def test_start_is_idempotent_and_creates_the_socket(self, tmp_path):
        server = _server(tmp_path)
        assert server.start() is True
        try:
            assert os.path.exists(server.socket_path)
            assert server.start() is True  # second call is a no-op, not an error
        finally:
            server.stop()
        assert not os.path.exists(server.socket_path)

    def test_round_trip_over_a_real_socket(self, tmp_path):
        engine = _FakeEngine()
        server = _server(tmp_path, engine)
        assert server.start() is True
        try:
            response = control.send_command("status", socket_path=server.socket_path)
            assert response == {"ok": True, "cmd": "status"}
            assert engine.calls[0][0] == "status"
        finally:
            server.stop()

    def test_several_clients_are_served_concurrently(self, tmp_path):
        """The engine socket is not single-client (unlike the TUI socket)."""
        engine = _FakeEngine()
        server = _server(tmp_path, engine)
        assert server.start() is True
        try:
            for _ in range(5):
                assert control.send_command("ping", socket_path=server.socket_path)["ok"]
            assert len(engine.calls) == 5
        finally:
            server.stop()

    def test_raw_line_client_is_supported(self, tmp_path):
        """No JSON, no library: connect, write a verb, read a reply."""
        server = _server(tmp_path)
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


class TestUnsupportedTransport:
    """The no-AF_UNIX path is a supported degradation, not a crash.

    Stock CPython on Windows never exposes ``socket.AF_UNIX`` (bpo-33408), so
    native Windows always takes this path: the engine must not bind, and every
    verb must fail cleanly and visibly rather than raising into the app.
    """

    @pytest.fixture
    def no_af_unix(self, monkeypatch):
        monkeypatch.setattr(control, "socket_supported", lambda: False)

    def test_socket_supported_asks_the_interpreter(self):
        assert control.socket_supported() == hasattr(socket, "AF_UNIX")

    def test_server_start_refuses_without_raising(self, no_af_unix, tmp_path):
        server = _server(tmp_path)
        assert server.start() is False
        assert server.started is False

    def test_send_command_returns_a_clean_error(self, no_af_unix, tmp_path):
        response = control.send_command("toggle", socket_path=str(tmp_path / "x.sock"))
        assert response["ok"] is False
        assert "AF_UNIX" in response["error"]

    def test_cli_exits_one_with_a_human_message(self, no_af_unix, tmp_path, capsys):
        code = control.run_cli(["status", "--socket", str(tmp_path / "x.sock")])
        assert code == 1
        assert "AF_UNIX" in capsys.readouterr().err


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
    def test_json_flag_prints_the_raw_reply(self, tmp_path, capsys):
        server = _server(tmp_path)
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
    def test_socket_path_can_come_from_the_environment(self, tmp_path, monkeypatch):
        server = _server(tmp_path)
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
        ],
    )
    def test_setters_apply_and_echo_the_new_value(self, engine, verb, value, key):
        response = engine.handle_control(verb, {"value": value})
        assert response["ok"] is True
        assert key in response

    def test_mute_toggles_without_a_value(self, engine):
        import t2

        before = t2.IS_MUTED
        engine.handle_control("mute", {})
        assert t2.IS_MUTED is not before

    def test_setters_require_a_value(self, engine):
        with pytest.raises(ValueError, match="needs a value"):
            engine.handle_control("output", {})

    def test_bad_on_off_value_is_rejected(self, engine):
        with pytest.raises(ValueError, match="expected on/off"):
            engine.handle_control("spell", {"value": "maybe"})

    def test_set_mic_without_a_match_reports_the_failure(self, engine):
        """A substring that matches nothing must error, not silently no-op."""
        with pytest.raises(ValueError, match="no input device matching"):
            engine.handle_control("set-mic", {"value": "zzz-not-a-real-device-zzz"})

    def test_every_documented_verb_is_recognised(self, engine):
        """Guard against docs drift: no documented verb may be unknown.

        Verbs that would touch hardware or block on a modal are exercised only
        for *recognition* here -- the unknown-verb error must not be raised.
        """
        blocking = {
            "start", "stop", "toggle", "settings", "mic", "theme",
            "reset-defaults", "reset-terminal", "quit", "set-mic", "mics",
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
        # A free-text value must not claim a closed set.
        assert "choices" not in control.VERBS["set-mic"]
        assert "choices" not in control.VERBS["theme"] or "auto" in control.VERBS["theme"]["choices"]

    def test_required_and_toggling_verbs_are_marked(self):
        assert control.VERBS["output"]["required"] is True
        assert control.VERBS["set-mic"]["required"] is True
        assert "required" not in control.VERBS["start"]
        assert control.VERBS["mute"]["toggles"] is True
        assert control.VERBS["spell"]["toggles"] is True

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
