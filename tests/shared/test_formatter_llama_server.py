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
import subprocess
import sys
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

if EXIT_NOW:
    sys.stderr.write("stub: refusing to start\\n")
    sys.stderr.flush()
    sys.exit(7)

_health_calls = 0


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
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


def _write_stub(tmp_path: Path) -> str:
    path = tmp_path / "stub-llama-server"
    path.write_text(STUB_SOURCE.format(python=sys.executable))
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
    proc = subprocess.Popen(
        [sys.executable, _write_stub(tmp_path), "--host", "127.0.0.1",
         "--port", str(port)],
        env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if llama_server._health_ok(url):
            return proc, url
        if proc.poll() is not None:
            raise RuntimeError("stub server died")
        time.sleep(0.05)
    proc.kill()
    raise RuntimeError("stub server never became healthy")


#: The real implementation, captured before the autouse `_isolate` fixture stubs
#: it, so a test that has `_candidate_model_dirs` as its subject can restore it.
_REAL_CANDIDATE_MODEL_DIRS = llama_server._candidate_model_dirs


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    llama_server.reset()
    for variable in (llama_server.ENV_SERVER_URL, llama_server.ENV_BINARY,
                     llama_server.ENV_MODEL_PATH, llama_server.ENV_THREADS):
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
    assert described["binary"].endswith("stub-llama-server")
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
    stub = tmp_path / "chatty-llama-server"
    stub.write_text(
        f"#!{sys.executable}\n"
        "import sys, json\n"
        "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer\n"
        "argv = sys.argv[1:]\n"
        "port = int(argv[argv.index('--port') + 1])\n"
        "sys.stderr.write('x' * 200 + '\\n' * 1)\n"
        "for _ in range(5000):\n"
        "    sys.stderr.write('llama_model_load: chatty line of log output\\n')\n"
        "sys.stderr.flush()\n"
        "class H(BaseHTTPRequestHandler):\n"
        "    protocol_version = 'HTTP/1.1'\n"
        "    def log_message(self, *a): pass\n"
        "    def _send(self, code, payload):\n"
        "        body = json.dumps(payload).encode()\n"
        "        self.send_response(code)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(body)))\n"
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
    monkeypatch.setenv("VT_STUB_REPLY", "Buy milk.")
    formatter.reset_backend_cache()
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

