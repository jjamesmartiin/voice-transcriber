"""Shipped formatter backend: ``llama-server`` as a subprocess.

This is the primary formatting path (``docs/plan-on-device-formatter.md`` sec
6.1). It is a subprocess rather than the ``llama-cpp-python`` bindings for two
reasons, one of which is not obvious:

1. **The bindings cannot use llama.cpp's runtime CPU dispatch.** One artifact
   that is both portable and fast needs ``GGML_CPU_ALL_VARIANTS`` +
   ``GGML_BACKEND_DL``, loaded by ``ggml_backend_load_all()`` - which the Python
   bindings never expose, so a dynamic-backend build fails at model load with
   "no backends are loaded" (upstream issue #2069). The Nix in-process artifact
   is therefore baseline x86-64 (SSE2), which nixpkgs notes can be **13x slower**
   than AVX2. ``pkgs.llama-cpp`` already sets ``cpuArchDynamicDispatch = true``
   and its server calls the loader the bindings lack.
2. **Cancellation becomes trivial.** A request that overruns its budget is
   stopped by terminating the process, which is cruder than the in-process
   ``StoppingCriteria`` dance but cannot be ignored by the model.

The wire protocol is OpenAI-compatible, which is why milestone M2 can point the
*existing* ``VT_VLLM_URL`` client at this server without any new integration.

Two modes, and the attach mode matters more than it looks:

* **Attach** - ``VT_FORMATTER_SERVER_URL`` names a running server (the M2
  stepping stone, a shared instance, a dev server). We never spawn and never
  terminate it; it is not ours to kill.
* **Spawn** - we start ``llama-server`` ourselves on a free loopback port, hold
  the handle, and terminate it on timeout or at shutdown.

Importing this module starts nothing and imports no ``llama_cpp``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from typing import Any

from voice_transcriber import formatter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Point at an already-running server. Presence of this variable selects attach
#: mode, in which nothing is ever spawned or killed.
ENV_SERVER_URL = "VT_FORMATTER_SERVER_URL"

#: Override the ``llama-server`` executable (a Nix store path, a dev build, or
#: the Windows prebuilt ``llama-server.exe``).
ENV_BINARY = "VT_FORMATTER_BINARY"

#: Override the GGUF path. Without this the model registry supplies it.
ENV_MODEL_PATH = "VT_FORMATTER_MODEL_PATH"

#: Thread count. Defaults to the app's usual setting, then to a sane guess.
ENV_THREADS = "VT_FORMATTER_THREADS"

DEFAULT_HOST = "127.0.0.1"

#: How long to wait for a freshly spawned server to answer ``/health``.
SPAWN_TIMEOUT_S = 60.0

#: Poll interval while waiting for health.
_HEALTH_POLL_S = 0.05

#: Seconds to wait before retrying a failed spawn. Without this, a machine with
#: no model would attempt (and fail) a process spawn on every utterance.
_SPAWN_RETRY_COOLDOWN_S = 60.0

#: Context window. The contract's ceiling is 1000 input tokens plus
#: ``1.3 * input + 32`` output, so 4096 covers the worst case with room to
#: spare, while staying small enough to keep the KV cache cheap.
CONTEXT_SIZE = 4096

#: Pinned seed. Irrelevant at ``temperature=0`` (greedy ignores it) but required
#: the moment anyone sets a non-zero temperature, because llama.cpp's default
#: seed is *random*.
SEED = 0

#: Sampling parameters, pinned to greedy. Mirrors the server flags
#: ``--temp 0 --top-k 0 --top-p 1 --repeat-penalty 1.0``.
GREEDY = {"temperature": 0.0, "top_k": 0, "top_p": 1.0, "repeat_penalty": 1.0}

#: Strip a reasoning block if the model emitted one anyway. ``enable_thinking``
#: is disabled at both the server and the request level, so this should never
#: fire; it exists because the failure mode - the model's reasoning leaking into
#: the user's text - is bad enough to warrant belt and braces.
_THINK_RE = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think\b", re.IGNORECASE)


class BackendUnavailable(RuntimeError):
    """The server could not be reached, started, or trusted.

    Raised rather than returned so the distinction between "no formatting" and
    "formatting produced an empty string" stays unambiguous - an empty string is
    a legitimate result for filler-only input.
    """


_state: dict[str, Any] = {"proc": None, "url": None, "attached": False,
                          "spawn_failed_at": 0.0}
_lock = threading.Lock()

#: Last few lines the server wrote to stderr, kept so a startup failure can be
#: explained. A bounded deque rather than reading the pipe after the fact,
#: because the pipe belongs to the drain thread below.
_stderr_tail: deque[str] = deque(maxlen=20)


def _drain(proc: subprocess.Popen) -> None:
    """Continuously consume the server's stderr on a daemon thread.

    Not optional, and not tidiness: ``stderr=PIPE`` with nobody reading it
    deadlocks the child once it fills the ~64 KB pipe buffer, and llama-server
    logs steadily at startup. The symptom would be a formatter that hangs after
    a while with no error, which is close to undiagnosable from a bug report.
    """
    def pump() -> None:
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    _stderr_tail.append(line)
                    logger.debug("llama-server: %s", line)
        except Exception:
            pass

    threading.Thread(target=pump, name="llama-server-stderr", daemon=True).start()


def reset() -> None:
    """Terminate any spawned server and forget all state. For tests."""
    shutdown()
    with _lock:
        _state.update({"proc": None, "url": None, "attached": False,
                       "spawn_failed_at": 0.0})


def server_process() -> subprocess.Popen | None:
    """The spawned server, or ``None`` in attach mode. For tests and ``status``."""
    return _state.get("proc")


def _binary() -> str | None:
    """Locate ``llama-server``: explicit override, then ``PATH``."""
    override = (os.environ.get(ENV_BINARY) or "").strip()
    if override:
        return override if os.path.exists(override) else None
    from shutil import which
    return which("llama-server")


#: Model id -> the GGUF the release bundle installs for it. The model registry
#: (milestone C6) becomes the single declaration home for this; until then this is
#: the one place the mapping lives, so it is at least one place rather than none.
#: The ids match `formatter.MODELS` and the config choices, which a test pins.
MODEL_FILENAMES: dict[str, str] = {
    "s1-mini": "s1-mini-q4_k_m.gguf",
}

#: Used when the caller names no model, or names one this build does not know.
DEFAULT_MODEL_ID = "s1-mini"

#: Subdirectory under the app's data dir where the formatter weights live.
DEFAULT_MODEL_SUBDIR = "formatter"


def _model_filenames(model: str | None = None) -> tuple[str, ...]:
    """Filenames to look for, in order, for ``model``.

    An unknown id falls back to the default model rather than reporting nothing:
    a config that names a model this build does not know should still work if the
    shipped weights are present. Reporting "unavailable" there would make a typo
    look like a missing download.
    """
    key = str(model or DEFAULT_MODEL_ID).strip().lower()
    name = MODEL_FILENAMES.get(key) or MODEL_FILENAMES[DEFAULT_MODEL_ID]
    return (name,)


def _candidate_model_dirs() -> list[str]:
    """Directories to look in for the GGUF, most specific first.

    Each lookup is isolated: the registry accessors raise ``KeyError`` for a name
    that is not registered yet, and one shared ``try`` would let the missing spec
    abort the conventional path too - which is the only one that can succeed
    before milestone C6 adds the formatter spec.
    """
    dirs: list[str] = []
    try:
        from voice_transcriber import model_download
    except Exception:
        return dirs
    try:
        registry_dir = model_download.models_dir("formatter")
        if registry_dir:
            dirs.append(str(registry_dir))
    except Exception:
        pass  # KeyError until C6 registers a formatter spec
    try:
        dirs.append(os.path.join(str(model_download.get_data_dir()),
                                 "models", DEFAULT_MODEL_SUBDIR))
    except Exception:
        pass
    return dirs


def _model_path(model: str | None = None) -> str | None:
    """Locate the GGUF for ``model``, or ``None``.

    Explicit override, then the registry, then the conventional install path. The
    registry is the real answer, but a stock build carries no formatter spec yet
    (milestone C6). Without the conventional-location fallback a model that *is*
    installed stays unfindable, so enabling the formatter would silently do nothing
    unless the user also set ``VT_FORMATTER_MODEL_PATH`` - the same failure this
    backend's own default had before it was pointed at the shipping path.
    """
    override = (os.environ.get(ENV_MODEL_PATH) or "").strip()
    if override:
        return override if os.path.exists(override) else None

    spec = None
    try:
        from voice_transcriber import model_download
        spec = model_download.get_spec("formatter")
    except Exception:
        pass

    names = [str(name) for name in (getattr(spec, "required_local", ()) or ())
             if str(name).endswith(".gguf")]
    if not names:
        names = list(_model_filenames(model))

    for directory in _candidate_model_dirs():
        for name in names:
            candidate = os.path.join(directory, name)
            if os.path.exists(candidate):
                return candidate
    return None
    return None


def _threads() -> int:
    for variable in (ENV_THREADS, "VT_CPU_THREADS"):
        raw = (os.environ.get(variable) or "").strip()
        if raw.isdigit() and int(raw) > 0:
            return int(raw)
    return max(1, (os.cpu_count() or 4) // 2)


def available(model: str | None = None) -> bool:
    """Whether this backend could plausibly run, without starting anything.

    In attach mode a configured URL is taken at its word - the server is someone
    else's to manage, and probing it here would add a round trip to a path that is
    called on every utterance. In spawn mode both the executable and the weights
    must exist, because without them every call would pay for a failed spawn.
    """
    if (os.environ.get(ENV_SERVER_URL) or "").strip():
        return True
    if _state.get("spawn_failed_at"):
        if time.monotonic() - _state["spawn_failed_at"] < _SPAWN_RETRY_COOLDOWN_S:
            return False
    return _binary() is not None and _model_path(model) is not None


def _free_port(host: str = DEFAULT_HOST) -> int:
    """Ask the OS for an unused loopback port.

    Inherently racy - the port could be taken between the probe and the bind -
    but the alternative is a hardcoded port, which breaks the moment two
    instances run. llama-server fails loudly on a busy port, which is the
    failure we want.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _build_command(binary: str, model: str, host: str, port: int) -> list[str]:
    """The exact ``llama-server`` invocation.

    ``--jinja`` plus the ``enable_thinking`` template kwarg is the load-bearing
    part: without it the model can emit its reasoning as the answer. The
    sampling flags are pinned to greedy for determinism.
    """
    return [
        binary,
        "-m", model,
        "--host", host,
        "--port", str(port),
        "-c", str(CONTEXT_SIZE),
        "-t", str(_threads()),
        "--jinja",
        "--chat-template-kwargs", json.dumps({"enable_thinking": False}),
        "--temp", "0",
        "--seed", str(SEED),
        "--top-k", "0",
        "--top-p", "1",
        "--repeat-penalty", "1.0",
        "--no-webui",
    ]


def _health_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/health", timeout=timeout) as response:
            if response.status != 200:
                return False
            body = json.loads(response.read().decode("utf-8") or "{}")
            return body.get("status") in ("ok", "no slot available")
    except Exception:
        return False


def _terminate() -> None:
    """Kill a spawned server. Never touches an attached one."""
    proc = _state.get("proc")
    _state["proc"] = None
    _state["url"] = None
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    except Exception:
        logger.debug("could not terminate llama-server", exc_info=True)


def shutdown() -> None:
    """Terminate the spawned server, if any. Safe to call at any time."""
    with _lock:
        _terminate()


def _ensure_server(model: str | None = None) -> str:
    """Return a base URL for a healthy server, spawning one if needed.

    ``model`` is the model id the config asked for; it reaches ``llama-server`` as
    the ``-m`` path. In attach mode it is ignored - the attached server already has
    its own model loaded and it is not ours to re-point.

    Raises :class:`BackendUnavailable` rather than returning ``None`` so the
    caller cannot accidentally treat a failure as a successful empty result.
    """
    attached = (os.environ.get(ENV_SERVER_URL) or "").strip()
    if attached:
        url = attached.rstrip("/")
        with _lock:
            _state.update({"url": url, "attached": True})
        return url

    with _lock:
        if _state.get("proc") is not None and _state.get("url"):
            if _state["proc"].poll() is None:
                return _state["url"]

        binary = _binary()
        model_path = _model_path(model)
        if binary is None:
            raise BackendUnavailable(
                f"llama-server not found. Set {ENV_BINARY}, or use "
                f"{ENV_SERVER_URL} to attach to a running server.")
        if model_path is None:
            raise BackendUnavailable(
                f"no formatter model found for {model or DEFAULT_MODEL_ID!r}. Set "
                f"{ENV_MODEL_PATH}, or use {ENV_SERVER_URL} to attach to a "
                f"running server.")

        port = _free_port()
        url = f"http://{DEFAULT_HOST}:{port}"
        command = _build_command(binary, model_path, DEFAULT_HOST, port)
        logger.info("starting llama-server on port %d", port)
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as exc:
            _state["spawn_failed_at"] = time.monotonic()
            raise BackendUnavailable(f"could not start llama-server: {exc}") from exc

        _drain(proc)

        deadline = time.monotonic() + SPAWN_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = "\n".join(_stderr_tail)
                _state["spawn_failed_at"] = time.monotonic()
                raise BackendUnavailable(
                    f"llama-server exited with {proc.returncode}: {tail.strip()}")
            if _health_ok(url, timeout=0.5):
                _state.update({"proc": proc, "url": url, "attached": False,
                               "spawn_failed_at": 0.0})
                return url
            time.sleep(_HEALTH_POLL_S)

        _terminate()
        proc.kill()
        _state["spawn_failed_at"] = time.monotonic()
        raise BackendUnavailable(
            f"llama-server did not become healthy within {SPAWN_TIMEOUT_S:.0f}s")


def warm() -> None:
    """Start the server (or verify an attach target) ahead of first use.

    Must not raise: preloading is an optimisation, and the module-level
    :func:`formatter.warm` already treats a failure as "not ready".
    """
    try:
        _ensure_server()
    except BackendUnavailable:
        logger.info("formatter server not ready", exc_info=True)
    except Exception:
        logger.debug("unexpected failure while warming the formatter server",
                     exc_info=True)


def describe() -> dict[str, Any]:
    """Configured-vs-effective state, for ``status``. Model-free and side-effect free."""
    attached = bool((os.environ.get(ENV_SERVER_URL) or "").strip())
    proc = _state.get("proc")
    return {
        "backend": "llama-server",
        "mode": "attach" if attached else "spawn",
        "server_url": _state.get("url"),
        "binary": _binary(),
        "model": _model_path(),
        "threads": _threads(),
        "running": proc is not None and proc.poll() is None,
    }


def _strip_thinking(text: str) -> str:
    """Remove reasoning blocks, or reject output that was cut off mid-thought."""
    if not text:
        return text
    cleaned = _THINK_RE.sub("", text)
    if _THINK_OPEN_RE.search(cleaned):
        # An unterminated block means generation stopped inside the reasoning,
        # so there is no answer - better to fall back than to leak it.
        return ""
    return cleaned.strip()


def _post_chat(url: str, payload: dict, timeout_s: float) -> dict:
    request = urllib.request.Request(
        f"{url}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8"))


def format_text(
    text: str,
    *,
    style: str = formatter.DEFAULT_STYLE,
    structure: str = "prose",
    context: str = formatter.DEFAULT_CONTEXT,
    timeout_s: float = formatter.DEFAULT_TIMEOUT_S,
    model: str | None = None,
) -> str:
    """Rewrite ``text`` via the server.

    Returns the model's answer, or raises :class:`BackendUnavailable`. Validation
    is the caller's job - this function's contract is "produce a candidate",
    which keeps the guardrails in exactly one place.
    """
    url = _ensure_server(model)

    payload = {
        "messages": [
            {"role": "system", "content": formatter.SYSTEM_PROMPT},
            {"role": "user", "content": formatter.build_user_message(
                text, style=style, structure=structure, context=context)},
        ],
        "max_tokens": formatter.max_new_tokens(formatter.approx_token_count(text)),
        "stream": False,
        # Belt and braces with the server's --chat-template-kwargs: older server
        # builds ignore an unknown field, and a build that honours it guarantees
        # the empty <think> block the model was trained against.
        "chat_template_kwargs": {"enable_thinking": False},
        **GREEDY,
    }

    try:
        body = _post_chat(url, payload, timeout_s)
    except (socket.timeout, TimeoutError) as exc:
        # The crude-but-robust cancellation path: a server that blew its budget
        # is killed, because generation that ignores a deadline cannot be
        # reasoned with. It is restarted on next use.
        logger.warning("formatter server exceeded %.2fs; terminating", timeout_s)
        with _lock:
            if not _state.get("attached"):
                _terminate()
        raise BackendUnavailable("formatter server timed out") from exc
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise BackendUnavailable(f"formatter server returned HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise BackendUnavailable(f"formatter request failed: {exc}") from exc

    choices = body.get("choices") or []
    if not choices:
        raise BackendUnavailable("formatter server returned no choices")

    choice = choices[0]
    content = (choice.get("message") or {}).get("content")
    if not isinstance(content, str):
        raise BackendUnavailable("formatter server returned no message content")

    # A truncated rewrite can be cut mid-sentence, which validation cannot see.
    if choice.get("finish_reason") == "length":
        raise BackendUnavailable("formatter output was truncated at max_tokens")

    return _strip_thinking(content)


if __name__ == "__main__":  # pragma: no cover - manual debugging aid
    print(json.dumps(describe(), indent=2))
    if len(sys.argv) > 1:
        print(repr(format_text(sys.argv[1])))
