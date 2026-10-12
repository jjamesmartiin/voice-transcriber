#!/usr/bin/env python3
"""
Tests for the shipped ``llama-server`` formatter backend (milestone C3 / M3).

The server itself is stubbed by a small script that speaks just enough of the
OpenAI-compatible API, so the parts that actually break in the field - process
lifecycle, health polling, argument construction, kill-on-timeout, the exact
request body - are all exercised for real, offline, with no weights and no
``llama_cpp``:

* the real ``subprocess.Popen`` path runs, with a real child process;
* the real ``urllib`` client runs, against a real HTTP server;
* only the model is fake.

The one thing that cannot be tested here is the latency budget (plan sec 9):
that needs the 462 MiB GGUF and is milestone C2/C7.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import formatter
from voice_transcriber.formatters import llama_server

# ---------------------------------------------------------------------------
# The stub server
# ---------------------------------------------------------------------------
STUB_SOURCE = '''#!{python}
"""A stub llama-server: speaks just enough OpenAI API to test the backend."""
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

argv = sys.argv[1:]
host, port = "127.0.0.1", 0
for index, arg in enumerate(argv):
    if arg == "--host" and index + 1 < len(argv):
        host = argv[index + 1]
    if arg == "--port" and index + 1 < len(argv):
        port = int(argv[index + 1])

REPLY = os.environ.get("VT_STUB_REPLY", "")
DELAY = float(os.environ.get("VT_STUB_DELAY") or 0)
LOG = os.environ.get("VT_STUB_LOG", "")
FINISH = os.environ.get("VT_STUB_FINISH", "stop")
HTTP_ERROR = int(os.environ.get("VT_STUB_HTTP_ERROR") or 0)
UNHEALTHY = int(os.environ.get("VT_STUB_UNHEALTHY") or 0)
EXIT_NOW = os.environ.get("VT_STUB_EXIT_NOW") == "1"
REUSE_PORT = os.environ.get("VT_STUB_REUSE_PORT") == "1"

if EXIT_NOW:
    sys.stderr.write("stub: refusing to start\\n")
    sys.stderr.flush()
    sys.exit(7)

_health_calls = 0


class _Server(ThreadingHTTPServer):
    """ThreadingHTTPServer that can opt into SO_REUSEPORT, like cpp-httplib.

    The real llama-server sets SO_REUSEPORT before binding (httplib.h), which is
    the difference between a squatter being fatal and merely annoying: with it,
    the squatter shares the port and llama-server's bind *succeeds*.
    """

    def server_bind(self):
        if REUSE_PORT and hasattr(socket, "SO_REUSEPORT"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def address_string(self):
        return self.client_address[0]

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        global _health_calls
        if self.path == "/health":
            _health_calls += 1
            if _health_calls <= UNHEALTHY:
                self._send(503, {{"status": "loading model"}})
            else:
                self._send(200, {{"status": "ok"}})
            return
        self._send(404, {{"error": "not found"}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace")
        if LOG:
            with open(LOG, "w") as handle:
                handle.write(raw)
        if HTTP_ERROR:
            self._send(HTTP_ERROR, {{"error": "stub failure"}})
            return
        if DELAY:
            time.sleep(DELAY)
        self._send(200, {{"choices": [{{"message": {{"role": "assistant",
                                                   "content": REPLY}},
                                        "finish_reason": FINISH}}]}})


ThreadingHTTPServer((host, port), Handler).serve_forever()
'''

#: Replaced below so the stub can serve through `_Server` (which knows about
#: SO_REUSEPORT) rather than the stock `ThreadingHTTPServer`.
_STUB_ENTRY = "ThreadingHTTPServer((host, port), Handler).serve_forever()"


def _write_stub(tmp_path: Path) -> str:
    path = tmp_path / "stub-llama-server.py"
    path.write_text(STUB_SOURCE.format(python=sys.executable).replace(
        _STUB_ENTRY, "_Server((host, port), Handler).serve_forever()"))
    path.chmod(0o755)
    return str(path)


def _write_fake_gguf(tmp_path: Path) -> str:
    path = tmp_path / "s1-mini.gguf"
    path.write_bytes(b"GGUF")
    return str(path)


def _start_stub(tmp_path: Path, **env) -> tuple[subprocess.Popen, str]:
    """Run the stub directly, for attach-mode tests. Caller must terminate."""
    port = llama_server._free_port()
    child_env = dict(os.environ)
    child_env.update({k: str(v) for k, v in env.items()})
    # Keep the stub's stderr instead of discarding it. When this helper gives up,
    # that is the only evidence of *why* the stub never answered, and a bare
    # "never became healthy" is what hid a real health-probe regression on macOS CI
    # for three consecutive runs.
    log = tmp_path / "attach-stub-stderr.log"
    with open(log, "wb") as sink:
        proc = subprocess.Popen(
            [sys.executable, _write_stub(tmp_path), "--host", "127.0.0.1",
             "--port", str(port)],
            env=child_env, stdout=subprocess.DEVNULL, stderr=sink)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if llama_server._health_ok(url):
                return proc, url
            if proc.poll() is not None:
                raise RuntimeError(
                    f"stub server died on port {port}: "
                    f"{log.read_text(errors='replace')[-400:]!r}")
            time.sleep(0.05)
    proc.kill()
    raise RuntimeError(
        f"stub server never became healthy on port {port}; "
        f"its stderr was {log.read_text(errors='replace')[-400:]!r}")


#: The real implementation, captured before the autouse `_isolate` fixture stubs
#: it, so a test that has `_candidate_model_dirs` as its subject can restore it.
_REAL_CANDIDATE_MODEL_DIRS = llama_server._candidate_model_dirs


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    llama_server.reset()
    for variable in (llama_server.ENV_SERVER_URL, llama_server.ENV_BINARY,
                     llama_server.ENV_MODEL_PATH, llama_server.ENV_THREADS,
                     llama_server.ENV_ALLOW_REMOTE):
        monkeypatch.delenv(variable, raising=False)
    # Neutralise the conventional install path too. Whether *this* machine happens
    # to have the weights downloaded must not change what these tests assert, or
    # they pass on a bare host and fail on a developer's, which is worse than no
    # test. `stub_env` sets ENV_MODEL_PATH, which is consulted first, so it still
    # works with this in place.
    monkeypatch.setattr(llama_server, "_candidate_model_dirs", lambda: [])
    formatter.reset_backend_cache()
    yield
    llama_server.reset()
    formatter.reset_backend_cache()


@pytest.fixture
def stub_env(tmp_path, monkeypatch):
    """Point the backend at a stub server it spawns itself."""
    monkeypatch.setenv(llama_server.ENV_BINARY, _write_stub(tmp_path))
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, _write_fake_gguf(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------
def test_importing_the_backend_does_not_import_llama_cpp():
    """A model-free test run must never pay for the runtime (plan sec 6)."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from voice_transcriber.formatters import llama_server; "
        "assert 'llama_cpp' not in sys.modules, 'llama_cpp leaked into import'; "
        "print('OK')" % str(SRC_DIR)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# available() / describe()
# ---------------------------------------------------------------------------
def test_unavailable_without_a_binary_or_model():
    assert llama_server.available() is False


def test_the_conventional_install_path_is_found(tmp_path, monkeypatch):
    """An installed model must be findable without an env var.

    Regression: `_model_path()` only consulted ``VT_FORMATTER_MODEL_PATH`` and the
    model registry, and the registry has no formatter spec until C6 - so on a
    machine where the weights *were* downloaded, `formatter: on` still did
    nothing. Same failure as the backend default, found the same way.
    """
    monkeypatch.setattr(llama_server, "_candidate_model_dirs", lambda: [str(tmp_path)])
    name = llama_server.MODEL_FILENAMES["s1-mini"]
    (tmp_path / name).write_bytes(b"GGUF")
    assert llama_server._model_path("s1-mini") == str(tmp_path / name)


def test_an_unknown_model_id_falls_back_to_the_shipped_weights(tmp_path, monkeypatch):
    """A typo in the config must not look like a missing download.

    Falling back means a machine with the shipped weights still works; reporting
    unavailable would make a config typo indistinguishable from never having
    downloaded anything.
    """
    monkeypatch.setattr(llama_server, "_candidate_model_dirs", lambda: [str(tmp_path)])
    name = llama_server.MODEL_FILENAMES[llama_server.DEFAULT_MODEL_ID]
    (tmp_path / name).write_bytes(b"GGUF")
    assert llama_server._model_path("gpt-9-turbo") == str(tmp_path / name)


def test_an_explicit_path_still_wins(tmp_path, monkeypatch):
    explicit = tmp_path / "elsewhere.gguf"
    explicit.write_bytes(b"GGUF")
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, str(explicit))
    monkeypatch.setattr(llama_server, "_candidate_model_dirs", lambda: [str(tmp_path)])
    (tmp_path / llama_server.MODEL_FILENAMES["s1-mini"]).write_bytes(b"GGUF")
    assert llama_server._model_path("s1-mini") == str(explicit)


def test_the_registry_accessors_raising_does_not_lose_the_fallback(tmp_path, monkeypatch):
    """`models_dir()` raises KeyError for an unregistered name, not returns None.

    One shared try around the registry lookups let that abort the conventional
    path - which is the only one that can succeed before C6 registers a spec.
    """
    import model_download

    def _raise(*_args, **_kwargs):
        raise KeyError("unknown model 'formatter'")

    monkeypatch.setattr(model_download, "models_dir", _raise)
    monkeypatch.setattr(model_download, "get_spec", _raise)
    monkeypatch.setattr(llama_server, "MODEL_FILENAMES", {"x-model": "x.gguf"})
    # The fixture stubbed this out; put the real one back, it is the subject here.
    monkeypatch.setattr(llama_server, "_candidate_model_dirs",
                        _REAL_CANDIDATE_MODEL_DIRS)
    dirs = llama_server._candidate_model_dirs()
    assert any(str(model_download.get_data_dir()) in directory for directory in dirs), dirs
    (tmp_path / "x.gguf").write_bytes(b"GGUF")
    monkeypatch.setattr(llama_server, "_candidate_model_dirs", lambda: [str(tmp_path)])
    assert llama_server._model_path("x-model") == str(tmp_path / "x.gguf")


def test_unavailable_with_a_binary_but_no_model(tmp_path, monkeypatch):
    monkeypatch.setenv(llama_server.ENV_BINARY, _write_stub(tmp_path))
    assert llama_server.available() is False


def test_unavailable_with_a_model_but_no_binary(tmp_path, monkeypatch):
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, _write_fake_gguf(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))
    assert llama_server.available() is False


def test_available_with_both(stub_env):
    assert llama_server.available() is True


def test_available_is_true_in_attach_mode(monkeypatch):
    """An attached server is someone else's to manage; we take the URL on trust."""
    monkeypatch.setenv(llama_server.ENV_SERVER_URL, "http://127.0.0.1:9")
    assert llama_server.available() is True


def test_a_nonexistent_override_is_not_trusted(monkeypatch):
    monkeypatch.setenv(llama_server.ENV_BINARY, "/does/not/exist/llama-server")
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, "/does/not/exist/model.gguf")
    assert llama_server.available() is False


def test_describe_reports_configured_and_effective_state(stub_env):
    described = llama_server.describe()
    assert described["backend"] == "llama-server"
    assert described["mode"] == "spawn"
    assert described["running"] is False
    assert described["binary"].endswith(("stub-llama-server", "stub-llama-server.py"))
    assert described["model"].endswith(".gguf")
    assert isinstance(described["threads"], int)


def test_describe_reports_attach_mode(monkeypatch):
    monkeypatch.setenv(llama_server.ENV_SERVER_URL, "http://127.0.0.1:8080")
    assert llama_server.describe()["mode"] == "attach"


# ---------------------------------------------------------------------------
# The command line - the load-bearing flags
# ---------------------------------------------------------------------------
def test_build_command_pins_the_prompt_and_greedy_sampling():
    command = llama_server._build_command("/bin/llama-server", "/m.gguf",
                                          "127.0.0.1", 1234)
    assert command[0] == "/bin/llama-server"
    assert "-m" in command and "/m.gguf" in command
    assert "--port" in command and "1234" in command
    assert "--host" in command and "127.0.0.1" in command

    # --jinja + the template kwarg is what stops the model emitting reasoning as
    # the answer; without it the feature silently degrades.
    assert "--jinja" in command
    assert "--chat-template-kwargs" in command
    template = command[command.index("--chat-template-kwargs") + 1]
    assert json.loads(template) == {"enable_thinking": False}

    # Determinism (plan sec 6.3).
    for flag, value in (("--temp", "0"), ("--seed", str(llama_server.SEED)),
                        ("--top-k", "0"), ("--top-p", "1"),
                        ("--repeat-penalty", "1.0")):
        assert flag in command
        assert command[command.index(flag) + 1] == value


def test_thread_count_falls_back_and_honours_overrides(monkeypatch):
    assert llama_server._threads() >= 1
    monkeypatch.setenv(llama_server.ENV_THREADS, "3")
    assert llama_server._threads() == 3
    monkeypatch.delenv(llama_server.ENV_THREADS)
    monkeypatch.setenv("VT_CPU_THREADS", "5")
    assert llama_server._threads() == 5


# ---------------------------------------------------------------------------
# End to end against the stub: spawn -> health -> POST -> parse
# ---------------------------------------------------------------------------
def test_spawn_health_and_format(stub_env, monkeypatch):
    monkeypatch.setenv("VT_STUB_REPLY", "Buy milk.")
    assert llama_server.format_text("buy milk") == "Buy milk."


def test_a_server_is_reused_across_calls(stub_env, monkeypatch):
    monkeypatch.setenv("VT_STUB_REPLY", "Buy milk.")
    llama_server.format_text("buy milk")
    first = llama_server.server_process()
    assert first is not None
    llama_server.format_text("buy milk again")
    assert llama_server.server_process() is first


def test_health_polling_tolerates_a_slow_start(stub_env, monkeypatch):
    """A real server answers 503 while it loads; we must wait, not give up."""
    monkeypatch.setenv("VT_STUB_UNHEALTHY", "3")
    monkeypatch.setenv("VT_STUB_REPLY", "Ready.")
    assert llama_server.format_text("hello") == "Ready."


def test_the_request_body_matches_the_trained_contract(stub_env, monkeypatch):
    log = stub_env / "request.json"
    monkeypatch.setenv("VT_STUB_LOG", str(log))
    monkeypatch.setenv("VT_STUB_REPLY", "ok")
    llama_server.format_text("buy milk", style="formal", structure="lists",
                             context="email")

    body = json.loads(log.read_text())
    messages = body["messages"]
    assert messages[0] == {"role": "system", "content": formatter.SYSTEM_PROMPT}
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == (
        "[Styling: formal] [Structure: lists] [Context: email]\nbuy milk")

    assert body["max_tokens"] == formatter.max_new_tokens(
        formatter.approx_token_count("buy milk"))
    assert body["stream"] is False
    assert body["temperature"] == 0.0
    assert body["chat_template_kwargs"] == {"enable_thinking": False}


def test_verbose_server_output_does_not_deadlock(tmp_path, monkeypatch):
    """stderr=PIPE with no reader deadlocks once llama-server fills the buffer.

    A regression here presents as a formatter that hangs for no visible reason,
    so the drain thread is pinned by making the stub write far more than a pipe
    buffer's worth before answering.
    """
    stub = tmp_path / "chatty-llama-server.py"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys, json\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "argv = sys.argv[1:]\n"
        "port = int(argv[argv.index('--port') + 1])\n"
        "sys.stderr.write('x' * 200 + '\\n' * 1)\n"
        "for _ in range(1500):\n"
        "    sys.stderr.write('llama_model_load: chatty line of log output\\n')\n"
        "sys.stderr.flush()\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    protocol_version = 'HTTP/1.0'\n"
        "    def address_string(self): return self.client_address[0]\n"
        "    def log_message(self, *a): pass\n"
        "    def _send(self, code, payload):\n"
        "        body = json.dumps(payload).encode()\n"
        "        self.send_response(code)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(body)))\n"
        "        self.send_header('Connection', 'close')\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "    def do_GET(self): self._send(200, {'status': 'ok'})\n"
        "    def do_POST(self):\n"
        "        self.rfile.read(int(self.headers.get('Content-Length') or 0))\n"
        "        self._send(200, {'choices': [{'message': {'content': 'ok'},\n"
        "                                      'finish_reason': 'stop'}]})\n"
        "ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()\n"
    )
    stub.chmod(0o755)
    monkeypatch.setenv(llama_server.ENV_BINARY, str(stub))
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, _write_fake_gguf(tmp_path))
    monkeypatch.setattr(llama_server, "SPAWN_TIMEOUT_S", 20.0)

    assert llama_server.format_text("buy milk", timeout_s=15.0) == "ok"


# ---------------------------------------------------------------------------
# Failure paths - every one must reach the caller as BackendUnavailable
# ---------------------------------------------------------------------------
def test_missing_binary_names_the_escape_hatches(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(llama_server.BackendUnavailable) as caught:
        llama_server.format_text("buy milk")
    assert llama_server.ENV_BINARY in str(caught.value)
    assert llama_server.ENV_SERVER_URL in str(caught.value)


def test_a_crashing_server_reports_its_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv(llama_server.ENV_BINARY, _write_stub(tmp_path))
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, _write_fake_gguf(tmp_path))
    monkeypatch.setenv("VT_STUB_EXIT_NOW", "1")
    with pytest.raises(llama_server.BackendUnavailable) as caught:
        llama_server.format_text("buy milk")
    assert "exited with 7" in str(caught.value)
    assert "refusing to start" in str(caught.value)


def test_a_server_that_never_became_healthy_times_out(stub_env, monkeypatch):
    monkeypatch.setenv("VT_STUB_UNHEALTHY", "100000")
    monkeypatch.setattr(llama_server, "SPAWN_TIMEOUT_S", 0.5)
    with pytest.raises(llama_server.BackendUnavailable) as caught:
        llama_server.format_text("buy milk")
    assert "healthy" in str(caught.value)
    assert llama_server.server_process() is None


def test_a_truncated_answer_is_rejected(stub_env, monkeypatch):
    """Validation cannot see truncation, so the backend must."""
    monkeypatch.setenv("VT_STUB_REPLY", "Buy milk and also")
    monkeypatch.setenv("VT_STUB_FINISH", "length")
    with pytest.raises(llama_server.BackendUnavailable):
        llama_server.format_text("buy milk")


def test_an_http_error_is_reported(stub_env, monkeypatch):
    """A server-side error must surface as BackendUnavailable, not be ignored."""
    proc, url = _start_stub(stub_env, VT_STUB_HTTP_ERROR="500")
    try:
        monkeypatch.setenv(llama_server.ENV_SERVER_URL, url)
        with pytest.raises(llama_server.BackendUnavailable) as caught:
            llama_server.format_text("buy milk")
        assert "HTTP 500" in str(caught.value)
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Cancellation - kill on timeout, and never kill someone else's server
# ---------------------------------------------------------------------------
def test_a_timeout_terminates_the_spawned_server(stub_env, monkeypatch):
    monkeypatch.setenv("VT_STUB_DELAY", "10")
    llama_server.warm()
    proc = llama_server.server_process()
    assert proc is not None

    started = time.monotonic()
    with pytest.raises(llama_server.BackendUnavailable):
        llama_server.format_text("buy milk", timeout_s=0.4)
    elapsed = time.monotonic() - started

    assert elapsed < 5, "the backend waited for the slow server instead of killing it"
    assert llama_server.server_process() is None
    deadline = time.monotonic() + 5
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert proc.poll() is not None, "the runaway server was left running"


def test_a_timeout_never_kills_an_attached_server(stub_env, monkeypatch):
    """We do not own an attached server, so we must not terminate it."""
    proc, url = _start_stub(stub_env, VT_STUB_DELAY="10")
    try:
        monkeypatch.setenv(llama_server.ENV_SERVER_URL, url)
        with pytest.raises(llama_server.BackendUnavailable):
            llama_server.format_text("buy milk", timeout_s=0.4)
        assert llama_server.server_process() is None
        time.sleep(0.3)
        assert proc.poll() is None, "an attached server was killed"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_attach_mode_never_spawns(stub_env, monkeypatch):
    proc, url = _start_stub(stub_env, VT_STUB_REPLY="Attached reply.")
    try:
        monkeypatch.setenv(llama_server.ENV_SERVER_URL, url)
        assert llama_server.format_text("buy milk") == "Attached reply."
        assert llama_server.server_process() is None
        assert llama_server.describe()["mode"] == "attach"
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def test_shutdown_is_idempotent(stub_env, monkeypatch):
    monkeypatch.setenv("VT_STUB_REPLY", "ok")
    llama_server.format_text("buy milk")
    llama_server.shutdown()
    llama_server.shutdown()
    assert llama_server.server_process() is None


# ---------------------------------------------------------------------------
# A failed spawn is not retried on every utterance
# ---------------------------------------------------------------------------
def test_a_failed_spawn_arms_a_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv(llama_server.ENV_BINARY, _write_stub(tmp_path))
    monkeypatch.setenv(llama_server.ENV_MODEL_PATH, _write_fake_gguf(tmp_path))
    monkeypatch.setenv("VT_STUB_EXIT_NOW", "1")

    with pytest.raises(llama_server.BackendUnavailable):
        llama_server.format_text("buy milk")
    # Without this, a machine with a broken install pays for a process spawn on
    # every single utterance.
    assert llama_server.available() is False

    llama_server.reset()
    assert llama_server.available() is True


# ---------------------------------------------------------------------------
# Output post-processing
# ---------------------------------------------------------------------------
def test_reasoning_blocks_are_stripped():
    assert llama_server._strip_thinking("<think>hmm</think>Buy milk.") == "Buy milk."
    assert llama_server._strip_thinking(
        "<think>a</think>  Buy milk.  ") == "Buy milk."


def test_an_unterminated_reasoning_block_yields_nothing():
    """Generation stopped mid-thought, so there is no answer to return."""
    assert llama_server._strip_thinking("<think>I should probably") == ""


def test_plain_text_is_untouched():
    assert llama_server._strip_thinking("Buy milk.") == "Buy milk."


# ---------------------------------------------------------------------------
# warm()
# ---------------------------------------------------------------------------
def test_warm_never_raises(stub_env, monkeypatch):
    monkeypatch.setenv("VT_STUB_REPLY", "ok")
    assert llama_server.warm() is None
    assert llama_server.server_process() is not None


def test_warm_swallows_an_unavailable_backend(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert llama_server.warm() is None


# ---------------------------------------------------------------------------
# Integration with the module-level entry point: the guardrails still apply
# ---------------------------------------------------------------------------
def test_the_module_entry_point_formats_through_the_real_backend(stub_env, monkeypatch):
    """The registry layer must pass a real backend's answer through unchanged.

    Ask the backend directly before asserting on the registry, because
    `formatter.format_text` returns the *original* text on any failure - so a bare
    output assertion cannot tell "the backend broke" from "the guardrails rejected
    the rewrite". This is the one Windows/macOS failure the split-segment health
    probe did not explain, and a silent fallback hid it for three CI runs.
    """
    monkeypatch.setenv("VT_STUB_REPLY", "Buy milk.")
    formatter.reset_backend_cache()
    backend = formatter.load_backend("llama-server")
    assert backend is not None
    # Raises BackendUnavailable *with a reason*; the assertion below never fires
    # silently on the next platform this breaks on.
    direct = backend.format_text("buy milk", timeout_s=30.0)
    assert direct == "Buy milk.", (llama_server.describe(), direct)
    assert formatter.format_text("buy milk", backend="llama-server") == "Buy milk."


def test_the_guardrails_still_reject_a_bad_rewrite(stub_env, monkeypatch):
    """The shipped backend is not exempt from validation.

    A backend returning a syntactically perfect HTTP response with invented
    content must still yield the untouched original.
    """
    monkeypatch.setenv("VT_STUB_REPLY",
                       "Buy milk. You should also buy bread and eggs tomorrow.")
    formatter.reset_backend_cache()
    original = "buy milk"
    assert formatter.format_text(original, backend="llama-server") == original


def test_an_unavailable_backend_falls_back_to_the_original(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    formatter.reset_backend_cache()
    original = "buy milk"
    assert formatter.format_text(original, backend="llama-server") == original


# ---------------------------------------------------------------------------
# request_model: the first-use download start
# ---------------------------------------------------------------------------
def test_request_model_is_inert_when_the_model_is_present(stub_env):
    """An installed model is not re-fetched."""
    assert llama_server.request_model("s1-mini") is False


def test_request_model_is_inert_without_a_binary(monkeypatch):
    """No runtime -> no point fetching weights, and no network in tests."""
    import model_download

    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.setattr(llama_server, "_model_path", lambda model=None: None)
    started = []
    monkeypatch.setattr(model_download, "ensure_model_async",
                        lambda name, **k: started.append(name) or True)
    assert llama_server.request_model("s1-mini") is False
    assert started == []


def test_request_model_is_inert_in_attach_mode(monkeypatch):
    """An attached server is not ours and does not need our weights."""
    monkeypatch.setenv(llama_server.ENV_SERVER_URL, "http://127.0.0.1:9")
    assert llama_server.request_model("s1-mini") is False


def test_request_model_starts_the_registry_install(monkeypatch):
    """A spawn-mode install with a binary but no weights asks the registry."""
    import model_download

    monkeypatch.setattr(llama_server, "_binary", lambda: "/bin/llama-server")
    monkeypatch.setattr(llama_server, "_model_path", lambda model=None: None)
    calls = []
    monkeypatch.setattr(model_download, "ensure_model_async",
                        lambda name, **k: calls.append((name, k)) or True)
    # The first-use path must NOT claim consent; under a TUI that is what makes it
    # report the size and wait instead of prompting on a background thread.
    assert llama_server.request_model("s1-mini") is True
    assert calls == [("formatter", {"consented": False})]
    # The explicit verb forwards its consent through to the installer.
    calls.clear()
    assert llama_server.request_model("s1-mini", consented=True) is True
    assert calls == [("formatter", {"consented": True})]


def test_request_model_ignores_an_unregistered_model_id(monkeypatch):
    import model_download

    monkeypatch.setattr(llama_server, "_binary", lambda: "/bin/llama-server")
    monkeypatch.setattr(llama_server, "_model_path", lambda model=None: None)
    started = []
    monkeypatch.setattr(model_download, "ensure_model_async",
                        lambda name, **k: started.append(name) or True)
    assert llama_server.request_model("gpt-9-turbo") is False
    assert started == []


def test_binary_resolves_frozen_or_bundled_executable(tmp_path, monkeypatch):
    bin_file = tmp_path / "llama-server"
    bin_file.write_text("#!/bin/sh\nexit 0\n")
    bin_file.chmod(0o755)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app"))
    monkeypatch.setenv(llama_server.ENV_BINARY, "")
    assert llama_server._binary() == str(bin_file)


def test_build_command_gpu_layers_darwin_and_env(monkeypatch):
    monkeypatch.delenv("VT_FORMATTER_GPU_LAYERS", raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    cmd = llama_server._build_command("/bin/llama-server", "/m.gguf", "127.0.0.1", 1234)
    # Default on Linux: no -ngl
    assert "-ngl" not in cmd

    monkeypatch.setenv("VT_FORMATTER_GPU_LAYERS", "33")
    cmd_env = llama_server._build_command("/bin/llama-server", "/m.gguf", "127.0.0.1", 1234)
    assert "-ngl" in cmd_env
    assert cmd_env[cmd_env.index("-ngl") + 1] == "33"

    monkeypatch.delenv("VT_FORMATTER_GPU_LAYERS")
    monkeypatch.setattr(sys, "platform", "darwin")
    cmd_mac = llama_server._build_command("/bin/llama-server", "/m.gguf", "127.0.0.1", 1234)
    assert "-ngl" in cmd_mac
    assert cmd_mac[cmd_mac.index("-ngl") + 1] == "99"



# ---------------------------------------------------------------------------
# Transport framing - the cases http.server cannot produce
# ---------------------------------------------------------------------------
class RawServer:
    """A raw TCP peer that sends exactly the bytes a test wants, when it wants.

    The stub above answers through ``http.server``, which writes a whole, well
    framed response in one call. It therefore cannot produce the shapes the
    hand-rolled reader has to survive: a body in a second segment, no
    Content-Length, a stream that never ends, a squatter that answers /health.
    """

    def __init__(self, handler, host: str = "127.0.0.1", reuse_port: bool = False):
        # getaddrinfo here, rather than the module's own literal parse, so the
        # fixture picks its family independently of the code under test.
        family, socktype, proto, _canon, _addr = socket.getaddrinfo(
            host, 0, type=socket.SOCK_STREAM)[0]
        self.host = host
        self.sock = socket.socket(family, socktype, proto)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if reuse_port and HAS_SO_REUSEPORT:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.sock.bind((host, 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.requests: list[bytes] = []
        self._handler = handler
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            body = data.split(b"\r\n\r\n", 1)[1]
            length = 0
            for line in data.split(b"\r\n")[1:]:
                if line.lower().startswith(b"content-length:"):
                    length = int(line.split(b":", 1)[1].strip())
            while len(body) < length:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                body += chunk
            self.requests.append(data)
            self._handler(conn, data)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def close(self):
        self.sock.close()


CHAT_OK = (b'HTTP/1.0 200 OK\r\nContent-Length: 74\r\n\r\n'
           b'{"choices":[{"message":{"content":"Buy milk."},"finish_reason":"stop"}]}')


def test_a_health_body_in_a_second_segment_is_still_healthy():
    """The body is part of the answer.

    Regression: the reader stopped at the header terminator, so a body that
    arrived in its own TCP segment parsed as ``{}`` and a healthy server was
    reported as dead - which in spawn mode means the formatter silently never
    comes up. The stub cannot catch this, it writes in one call.
    """
    def handler(conn, _request):
        conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n")
        time.sleep(0.08)
        conn.sendall(b'{"status":"ok"}')

    server = RawServer(handler)
    try:
        assert llama_server._health_ok(server.url, timeout=2.0) is True
    finally:
        server.close()


def test_a_body_that_never_arrives_is_not_healthy_and_does_not_hang():
    """A peer that sends headers and then stalls must fail fast, not block."""
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n"))
    try:
        started = time.monotonic()
        assert llama_server._health_ok(server.url, timeout=0.4) is False
        assert time.monotonic() - started < 2.0
    finally:
        server.close()


def test_a_response_without_content_length_is_read_until_close():
    """HTTP/1.0 signals the end of the body with the close, and this negotiates
    HTTP/1.0 - so the reader must not require Content-Length."""
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nConnection: close\r\n\r\n" + CHAT_OK.split(b"\r\n\r\n")[1]))
    try:
        assert llama_server._post_chat(server.url, {"x": 1}, 2.0)["choices"]
    finally:
        server.close()


def test_a_stream_that_never_ends_cannot_outlive_the_budget():
    """`timeout_s` must be a whole-request budget.

    Regression: the socket timeout is an *idle* timeout, so a peer that keeps
    sending resets it forever, the call never returns and the buffer grows with
    everything sent. A slow trickle is the shape that isolates the deadline from
    the size cap - see the two tests around this one for the cap itself.
    """
    def handler(conn, _request):
        conn.sendall(b"HTTP/1.0 200 OK\r\nConnection: close\r\n\r\n")
        try:
            while True:
                conn.sendall(b"x" * 1024)
                time.sleep(0.05)
        except OSError:
            pass

    server = RawServer(handler)
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            llama_server._post_chat(server.url, {"x": 1}, 0.4)
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, f"the budget was ignored, ran {elapsed:.1f}s"
    finally:
        server.close()


def test_a_response_larger_than_the_limit_is_refused():
    """Unbounded buffering is how a broken or hostile peer becomes an OOM."""
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nContent-Length: 99999999999\r\n\r\nspam"))
    try:
        with pytest.raises(llama_server.BackendUnavailable) as caught:
            llama_server._post_chat(server.url, {"x": 1}, 2.0)
        assert "exceeds" in str(caught.value)
    finally:
        server.close()


def test_a_body_without_content_length_is_capped_too():
    """The size cap must hold on the read-until-close path as well; there is no
    declared length to check up front there."""
    def handler(conn, _request):
        conn.sendall(b"HTTP/1.0 200 OK\r\nConnection: close\r\n\r\n")
        try:
            while True:
                conn.sendall(b"x" * 262144)
        except OSError:
            pass

    server = RawServer(handler)
    try:
        with pytest.raises(llama_server.BackendUnavailable) as caught:
            llama_server._post_chat(server.url, {"x": 1}, 5.0)
        assert "exceeds" in str(caught.value)
    finally:
        server.close()


def test_chunked_encoding_is_reported_plainly():
    """We ask for HTTP/1.0 so a compliant peer never chunks. If one does, say so
    rather than feeding chunk framing to json.loads."""
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"1d\r\n{\"choices\":[]}\r\n0\r\n\r\n"))
    try:
        with pytest.raises(llama_server.BackendUnavailable) as caught:
            llama_server._post_chat(server.url, {"x": 1}, 2.0)
        assert "chunked" in str(caught.value)
    finally:
        server.close()


# ---------------------------------------------------------------------------
# Attach mode - which URLs we are willing to speak
# ---------------------------------------------------------------------------
def test_an_https_attach_url_is_refused_not_downgraded(monkeypatch):
    """There is no TLS here, so `https://` used to become plaintext on port 80.

    Silently posting every transcript unencrypted to a port the URL never named
    is worse than refusing.
    """
    monkeypatch.setenv(llama_server.ENV_SERVER_URL, "https://127.0.0.1:8443")
    assert llama_server.available() is False
    with pytest.raises(llama_server.BackendUnavailable) as caught:
        llama_server.format_text("buy milk")
    assert "http://" in str(caught.value)


def test_plain_http_to_another_host_needs_an_explicit_opt_in(monkeypatch):
    """Pointing the formatter at another machine should be a deliberate act.

    Spawn mode only ever talks to loopback, so an attach URL that leaves this
    machine - including one on the same LAN - is refused unless the user asks for
    it. A copied config or a typo must not silently stream every transcript to
    another host in the clear.
    """
    monkeypatch.setenv(llama_server.ENV_SERVER_URL, "http://model-box.local:8080")
    assert llama_server.available() is False
    with pytest.raises(llama_server.BackendUnavailable) as caught:
        llama_server.format_text("buy milk")
    assert llama_server.ENV_ALLOW_REMOTE in str(caught.value)

    # The flag is the whole opt-in; nothing else changes.
    monkeypatch.setenv(llama_server.ENV_ALLOW_REMOTE, "1")
    assert llama_server.available() is True
    assert llama_server._endpoint("http://model-box.local:8080") == ("model-box.local", 8080)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:99999/",        # port out of range
    "http://127.0.0.1:abc/",          # port is not a number
    "http://[::1]@evil.com:8080/",    # malformed IPv6 literal
    "127.0.0.1:8080",                 # no scheme - used to mean host, port 80
    "http://",                        # no host
    "://nonsense",
])
def test_a_malformed_attach_url_reports_unavailable_and_never_raises(url, monkeypatch):
    """`available` is a REQUIRED_FUNCTION on the per-utterance path and is
    documented never to raise, but `urlparse` raises bare ValueError for several
    of these - and the old fallback silently turned a scheme-less URL into a
    request to port 80."""
    monkeypatch.setenv(llama_server.ENV_SERVER_URL, url)
    assert llama_server.available() is False
    with pytest.raises(llama_server.BackendUnavailable):
        llama_server.format_text("buy milk")


def test_an_explicit_port_zero_is_not_silently_port_80():
    """`parsed.port or 80` treated a deliberate `:0` as absent."""
    assert llama_server._endpoint("http://127.0.0.1:0") == ("127.0.0.1", 0)


def test_loopback_needs_no_opt_in(monkeypatch):
    """Every spelling of "this machine" is allowed by default."""
    monkeypatch.delenv(llama_server.ENV_ALLOW_REMOTE, raising=False)
    assert llama_server._is_loopback("127.0.0.1")
    assert llama_server._is_loopback("localhost")
    assert llama_server._is_loopback("LOCALHOST")
    assert llama_server._is_loopback("127.0.0.53")     # all of 127.0.0.0/8
    assert llama_server._is_loopback("::1")
    # A LAN address, and a name that is not localhost, are not loopback even
    # though the latter might well resolve to 127.0.0.1 - the check must not
    # depend on a resolver that a hostile DNS answer could steer.
    assert not llama_server._is_loopback("192.168.1.10")
    assert not llama_server._is_loopback("127.0.0.1.example.com")
    assert llama_server._endpoint("http://127.0.0.1:8080") == ("127.0.0.1", 8080)
    assert llama_server._endpoint("http://localhost:8080") == ("localhost", 8080)
    assert llama_server._endpoint("http://[::1]:8080") == ("::1", 8080)


# ---------------------------------------------------------------------------
# The port a squatter can take
# ---------------------------------------------------------------------------
def test_the_offered_port_is_picked_from_our_own_range():
    """Not the kernel's next ephemeral port, which is sequential and therefore
    guessable - and so can be held in advance by a local process."""
    low, high = llama_server._PORT_RANGE
    for _ in range(5):
        assert low <= llama_server._free_port() <= high


def test_a_port_someone_else_holds_is_skipped(monkeypatch):
    """A failed bind is information: never hand back a port we could not take."""
    held = socket.socket()
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    taken = held.getsockname()[1]
    try:
        monkeypatch.setattr(llama_server, "_PORT_RANGE", (taken, taken + 1))
        monkeypatch.setattr(llama_server, "_PORT_ATTEMPTS", 8)
        assert llama_server._free_port() != taken
    finally:
        held.close()


def test_a_squatter_that_cannot_share_the_port_is_not_trusted(stub_env, monkeypatch):
    """The settle check, and the *only* squatter it defeats.

    A squatter taking the port with SO_REUSEADDR alone cannot coexist with
    llama-server (whose own SO_REUSEPORT opt-in is irrelevant unless the other
    socket opted in too), so the child exits on bind failure - and that exit is
    what the check detects. See the test below for the case it does not cover.
    """
    def handler(conn, request):
        if request.startswith(b"GET"):
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n"
                         b'{"status":"ok"}')
        else:
            conn.sendall(CHAT_OK)   # would hand back "Buy milk." to the user

    squatter = RawServer(handler)
    monkeypatch.setattr(llama_server, "_free_port", lambda host="127.0.0.1":
                        squatter.port)
    try:
        # The squatter alone is convincing: that is exactly what makes the race
        # worth winning.
        assert llama_server._health_ok(squatter.url) is True

        with pytest.raises(llama_server.BackendUnavailable) as caught:
            llama_server.format_text("buy milk", timeout_s=5.0)

        # Our own child could not bind, so it exited - and that is the tell.
        assert "exited" in str(caught.value), caught.value
        assert not any(request.startswith(b"POST") for request in squatter.requests), (
            "the transcript was posted to a process that is not our llama-server")
    finally:
        squatter.close()


#: Winsock has no SO_REUSEPORT, so the test that models a sharing squatter cannot
#: run on Windows. The limitation it documents is Windows-relevant too, but Windows
#: cannot be made to demonstrate it.
HAS_SO_REUSEPORT = hasattr(socket, "SO_REUSEPORT")


@pytest.mark.skipif(not HAS_SO_REUSEPORT,
                    reason="Winsock has no SO_REUSEPORT to model the shared listener")
def test_a_squatter_that_shares_the_port_is_NOT_detected(stub_env, monkeypatch):
    """Characterisation test for a known limitation, not a fix.

    llama-server binds with SO_REUSEPORT (cpp-httplib sets it before bind, guarded
    by `#ifdef SO_REUSEPORT`), so a squatter that also opts into SO_REUSEPORT gets
    to *share* the port: the child's bind succeeds, the child stays alive, and the
    settle check is satisfied. The kernel then spreads connections across both
    listeners, so the squatter receives a share of the transcripts.

    This asserts the limitation rather than tolerating it silently, so that the
    day someone closes it (AF_UNIX, or verifying the listening PID) this test
    fails and has to be rewritten. docs/TODO.md carries the reasoning. It is not
    a race: a process can join *after* the server is warm, and the port is
    readable from /proc/net/tcp or `ss`.
    """
    hits = []

    def handler(conn, request):
        if request.startswith(b"POST"):
            hits.append(request)
            conn.sendall(CHAT_OK)
        else:
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n"
                         b'{"status":"ok"}')

    squatter = RawServer(handler, reuse_port=True)
    monkeypatch.setenv("VT_STUB_REUSE_PORT", "1")   # the stub opts in too
    monkeypatch.setattr(llama_server, "_free_port", lambda host="127.0.0.1":
                        squatter.port)
    try:
        llama_server.warm()
        # The crux, and deterministic: the settle check accepted a server whose
        # child survived sharing the port, so it detected nothing.
        assert llama_server.server_process() is not None, (
            "if this fails the limitation is gone and this test should be deleted")
        for index in range(20):
            try:
                llama_server.format_text(f"secret transcript {index}", timeout_s=5.0)
            except llama_server.BackendUnavailable:
                pass
        if not hits:
            # Proving the impact needs the kernel to hand at least one connection
            # to the squatter, which is hash-based and not ours to control. Skip
            # rather than flake: the guard's failure above is already asserted.
            pytest.skip("no connection was distributed to the squatter here")
    finally:
        squatter.close()


def test_attach_mode_can_reach_an_ipv6_loopback_server():
    """`http://[::1]:port` used to be unreachable: the transport was IPv4-only.

    The address family now comes from parsing the host as a literal - no
    resolver, which is the whole reason this module does not use
    `socket.create_connection`.
    """
    def handler(conn, request):
        if request.startswith(b"GET"):
            conn.sendall(b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n"
                         b'{"status":"ok"}')
        else:
            conn.sendall(CHAT_OK)

    try:
        server = RawServer(handler, host="::1")
    except OSError as exc:      # pragma: no cover - only on a host with no ::1
        pytest.skip(f"no IPv6 loopback on this host: {exc}")
    try:
        assert server.url.startswith("http://[::1]:"), server.url
        assert llama_server._health_ok(server.url, timeout=2.0) is True
        body = llama_server._post_chat(server.url, {"x": 1}, 2.0)
        assert body["choices"][0]["message"]["content"] == "Buy milk."
        # A Host header carrying a bare IPv6 literal is malformed HTTP.
        assert b"Host: [::1]" in server.requests[-1]
    finally:
        server.close()


def test_an_ipv4_literal_still_gets_an_unbracketed_host_header():
    """The bracketing must be conditional, not unconditional."""
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n" + b'{"status":"ok"}'))
    try:
        assert llama_server._health_ok(server.url, timeout=2.0) is True
        assert b"Host: 127.0.0.1" in server.requests[-1]
    finally:
        server.close()


def test_attach_mode_resolves_a_hostname():
    """A name is resolved, and across both families rather than IPv4 only.

    RawServer listens on 127.0.0.1; the client is handed `localhost`, which on
    many systems resolves to `::1` first. That is the point: the address must be
    tried, not assumed, so an IPv6-only host reaches too.
    """
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n" + b'{"status":"ok"}'))
    try:
        assert llama_server._is_literal("localhost") is False
        assert llama_server._is_literal("127.0.0.1") is True
        assert llama_server._is_literal("::1") is True
        assert llama_server._health_ok(
            f"http://localhost:{server.port}", timeout=5.0) is True
    finally:
        server.close()


def test_a_literal_host_is_not_resolved(monkeypatch):
    """The loopback path must not touch a resolver - that is why a literal gets
    its own branch instead of going through `create_connection`."""
    server = RawServer(lambda conn, _r: conn.sendall(
        b"HTTP/1.0 200 OK\r\nContent-Length: 15\r\n\r\n" + b'{"status":"ok"}'))

    def boom(*_args, **_kwargs):
        raise AssertionError("resolver consulted for a literal host")

    monkeypatch.setattr(llama_server.socket, "getaddrinfo", boom)
    try:
        assert llama_server._health_ok(server.url, timeout=2.0) is True
        assert llama_server._post_chat(server.url, {"x": 1}, 2.0)
    finally:
        server.close()
