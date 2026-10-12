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

The transport is a raw ``socket``, not ``http.client`` or ``urllib``: the loopback
request must consult nothing - no proxy, no resolver, no macOS ``_scproxy`` - so
the module speaks HTTP/1.0 by hand and understands only ``Content-Length`` and
the close-to-signal-EOF rule. ``https://`` is refused rather than silently
downgraded, because there is no TLS here to honour it with. Attach mode is
loopback-only unless ``VT_FORMATTER_ALLOW_REMOTE=1`` says otherwise, and a host
that is an IP literal is dialled without consulting a resolver at all.

Spawn mode's TCP port is a *local trust boundary, not an authenticated one* -
anything on the machine can share the port and answer ``/health`` for us, and the
settle check below only defeats a squatter that cannot share it. Treat the server
as trusted-local-only; ``docs/TODO.md`` records what is left open and why
switching spawn mode to an AF_UNIX socket is the way to close it.
"""

from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from collections import deque
from pathlib import Path
from typing import Any

from voice_transcriber import formatter
from voice_transcriber import llama_server_bundle as BUNDLE

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Point at an already-running server. Presence of this variable selects attach
#: mode, in which nothing is ever spawned or killed.
ENV_SERVER_URL = "VT_FORMATTER_SERVER_URL"

#: Override the ``llama-server`` executable (a Nix store path, a dev build, or
#: the Windows prebuilt ``llama-server.exe``). This stays the bring-your-own
#: escape hatch for someone running their own llama.cpp: an explicit value is
#: authoritative and is consulted ahead of the fetched bundle and ``PATH``.
ENV_BINARY = "VT_FORMATTER_BINARY"

#: Override the GGUF path. Without this the model registry supplies it.
ENV_MODEL_PATH = "VT_FORMATTER_MODEL_PATH"

#: Attach mode is loopback-only unless this is set. A formatter URL pointing at
#: another machine sends every transcript there in the clear; that should be a
#: deliberate act, not a typo or a copied config.
ENV_ALLOW_REMOTE = "VT_FORMATTER_ALLOW_REMOTE"

#: Thread count. Defaults to the app's usual setting, then to a sane guess.
ENV_THREADS = "VT_FORMATTER_THREADS"

DEFAULT_HOST = "127.0.0.1"

#: How long to wait for a freshly spawned server to answer ``/health``.
SPAWN_TIMEOUT_S = 60.0

#: Poll interval while waiting for health.
_HEALTH_POLL_S = 0.05

#: How long our child must have been alive before a healthy ``/health`` is
#: trusted. Anything on the machine can bind the port between ``_free_port()``
#: releasing it and the child binding it; see ``_ensure_server``.
_BIND_SETTLE_S = 0.2

#: Loopback ports offered to llama-server, and how many candidates to try. This
#: is IANA's dynamic range, which Linux, macOS and Windows all place their own
#: ephemeral ports in. Random rather than the kernel's next one: see `_free_port`.
_PORT_RANGE = (49152, 65535)
_PORT_ATTEMPTS = 16

#: Ceilings on a buffered response head and body. The contract's output is ~1000
#: tokens, so this is orders of magnitude of headroom; it exists so that a
#: hostile or broken server cannot be answered with unbounded memory.
_MAX_HEAD_BYTES = 64 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024

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


#: Subdirectory under the app's data dir where a fetched -- or hand-unpacked --
#: ``llama-server`` bundle lives. ``VT_FORMATTER_BINARY`` still wins, so a user
#: running their own llama.cpp build is unaffected.
DEFAULT_BINARY_SUBDIR = "formatter"


def binary_install_dir() -> str:
    """Where the pinned backend bundle belongs, without creating anything.

    Consulted while resolving on the per-utterance path, so it must stay
    side-effect free apart from the data dir ``get_data_dir`` already creates.
    """
    try:
        from voice_transcriber import model_download
        return os.path.join(str(model_download.get_data_dir()),
                            DEFAULT_BINARY_SUBDIR)
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".local", "share", "vt",
                            DEFAULT_BINARY_SUBDIR)


def _is_executable(path: str) -> bool:
    if not path or not os.path.isfile(path):
        return False
    # Windows has no executable bit: an ``.exe`` is executable by extension, and
    # a zip-extracted one on a POSIX host would otherwise need an X bit it never
    # gets. The member-set check, not the mode, decides usability there.
    if path.lower().endswith(".exe"):
        return True
    return os.access(path, os.X_OK)


def _member_names(path: str) -> tuple[str, ...]:
    """Siblings a resolved executable needs to start.

    Empty for anything that is not a Windows ``.exe``: a POSIX or Nix
    ``llama-server`` is a self-contained executable, and demanding Windows DLLs
    beside it would break every existing Linux/macOS setup. The pinned Windows
    artifact is a stub plus DLLs, so its members are why this is a check at all.
    """
    if not path.lower().endswith(".exe"):
        return ()
    base = os.path.basename(path).lower()
    return tuple(member for member in BUNDLE.REQUIRED_MEMBERS
                 if member.lower() != base)


def _missing_members(path: str) -> list[str]:
    """Which required siblings are absent beside ``path`` (empty when usable)."""
    directory = os.path.dirname(os.path.abspath(path))
    return [member for member in _member_names(path)
            if not os.path.isfile(os.path.join(directory, member))]


def _candidate_binary_dirs() -> list[tuple[str, str]]:
    """``(directory, source)`` pairs to search, most specific first.

    The install location sits between the frozen bundle and ``PATH``: a fetched
    build resolves without an env var, but an executable shipped beside a frozen
    app still wins.
    """
    dirs: list[tuple[str, str]] = []
    if getattr(sys, "frozen", False):
        dirs.append((str(Path(sys.executable).parent), "bundled"))
    if hasattr(sys, "_MEIPASS"):
        dirs.append((str(Path(sys._MEIPASS)), "bundled"))
    try:
        dirs.append((binary_install_dir(), "bundle"))
    except Exception:
        pass
    return dirs


def _exe_in_dir(directory: str) -> str | None:
    for name in ("llama-server", "llama-server.exe"):
        candidate = os.path.join(directory, name)
        if _is_executable(candidate):
            return candidate
    return None


def resolve_binary() -> dict[str, Any]:
    """Resolve the executable *set* without starting, fetching or raising.

    Returns the path or ``None``, where it came from, every directory searched,
    and the members missing beside a found Windows stub. :func:`_binary` is the
    path-only view; ``doctor`` and the unavailable message use the detail.

    An explicit ``VT_FORMATTER_BINARY`` is authoritative: when it does not
    resolve we do not fall through to another binary, because silently running a
    different one than the user pointed at is not what they asked for.
    """
    searched: list[str] = []
    missing: list[str] = []
    found: str | None = None
    source: str | None = None

    def consider(path: str) -> bool:
        nonlocal found, missing
        if not _is_executable(path):
            return False
        absent = _missing_members(path)
        if absent:
            # First incomplete candidate wins the report: it is the one the user
            # is most likely to have just placed there.
            missing = missing or absent
            return False
        found = path
        return True

    override = (os.environ.get(ENV_BINARY) or "").strip()
    if override:
        searched.append(override)
        if consider(override):
            source = "override"
        return _binary_report(found, source, searched, missing, override=True)

    for directory, label in _candidate_binary_dirs():
        searched.append(directory)
        candidate = _exe_in_dir(directory)
        if candidate is not None and consider(candidate):
            source = label
            break

    if found is None:
        from shutil import which
        on_path = which("llama-server") or which("llama-server.exe")
        if on_path:
            searched.append(on_path)
            if consider(on_path):
                source = "path"

    return _binary_report(found, source, searched, missing, override=False)


def _bundle_url() -> str:
    """The runtime fetch URL: *our* pre-release, never upstream's copy.

    The slug comes from ``model_download.REPO_SLUG``, the single declaration of
    which repo hosts our releases; the pin module deliberately declines to
    declare a second one. A publisher's ``pin.UPSTREAM_URL`` must not reach this
    path: redistribution is what makes the hosted copy, its ``SHA256SUMS`` and
    the ``config/licenses/`` notices ours.
    """
    try:
        from voice_transcriber import model_download
        return BUNDLE.bundle_url(model_download.REPO_SLUG)
    except Exception:
        return ""


def _binary_report(found, source, searched, missing, *, override) -> dict[str, Any]:
    return {
        "path": found,
        "source": source,
        "directory": os.path.dirname(os.path.abspath(found)) if found else None,
        "searched": list(searched),
        "missing": list(missing),
        "override": override,
        "install_dir": binary_install_dir(),
        "download_url": _bundle_url(),
        "asset_bytes": BUNDLE.ASSET_BYTES,
        "asset_sha256": BUNDLE.ASSET_SHA256,
        "bundle_tag": BUNDLE.BUNDLE_TAG,
        "runtime_prerequisites": list(BUNDLE.RUNTIME_PREREQUISITES),
    }


def _binary() -> str | None:
    """Locate a *usable* ``llama-server``: override, bundle dir, then ``PATH``.

    A bare ``llama-server.exe`` is not usable on its own -- see
    :func:`resolve_binary` -- so the returned path always has its required
    siblings beside it.
    """
    return resolve_binary()["path"]


def format_size(n: int) -> str:
    """Human-readable download size (binary units, one decimal)."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def _runtime_prerequisite_note() -> str:
    names = ", ".join(BUNDLE.RUNTIME_PREREQUISITES)
    return (
        f"The pinned build is Windows x64 and also needs the Microsoft VC++ "
        f"runtime ({names}), which the archive does not contain: install the "
        f"\"Microsoft Visual C++ Redistributable\" if the server will not start."
    )


def binary_error_message() -> str:
    """The actionable ``llama-server`` dead end: where it goes and how to get it.

    Names the directories actually searched, says a Windows bundle is the
    executable *plus its DLLs*, points at the fetch command, and records the
    VC++ runtime as a prerequisite rather than a file to copy.
    """
    report = resolve_binary()
    install = report["install_dir"]
    parts: list[str] = []
    if report["override"]:
        override = (os.environ.get(ENV_BINARY) or "").strip()
        parts.append(
            f"{ENV_BINARY} points at {override!r}, which is not a usable "
            f"llama-server.")
        if report["missing"]:
            parts.append("Missing beside it: " + ", ".join(report["missing"]) + ".")
    else:
        parts.append("llama-server not found.")
        if report["missing"]:
            parts.append(
                "A llama-server was found but is incomplete: missing "
                + ", ".join(report["missing"]) + " beside it.")
        if report["searched"]:
            parts.append("Searched: " + ", ".join(report["searched"]) + ".")
    parts.append(
        f"Put a complete llama-server -- the executable *and* its DLLs -- next "
        f"to the app, in {install}, or set {ENV_BINARY} to an existing llama.cpp "
        f"build.")
    parts.append(
        f"Run the \"formatter-download\" command to fetch the pinned build, or "
        f"download {_bundle_url()} ({format_size(BUNDLE.ASSET_BYTES)}) and "
        f"unpack it into {install}.")
    parts.append(
        f"To attach to a server that is already running, set {ENV_SERVER_URL}.")
    parts.append(_runtime_prerequisite_note())
    return " ".join(parts)


#: In-flight/notified guards for the backend fetch, mirroring the model
#: installer's: a second utterance while a download runs must not start a
#: duplicate or re-report the size on every dictation.
_binary_inflight: set[str] = set()
_binary_consent_notified: set[str] = set()
_binary_async_lock = threading.Lock()
_BINARY_KEY = "llama-server"


def _fetch_archive(url: str, dest: str, descr: str) -> None:
    """Stream ``url`` to ``dest``. Indirection keeps tests off the network."""
    from voice_transcriber import model_download
    model_download._http_get(url, dest, descr)


def _sha256_file(path: str) -> str:
    from voice_transcriber import model_download
    return model_download._sha256_file(path)


def _check_archive_member(name: str) -> None:
    normalized = name.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    if os.path.isabs(normalized) or ".." in parts:
        raise BackendUnavailable(f"refusing unsafe path in the archive: {name!r}")


def _extract_archive(archive: str, dest: str) -> None:
    lower = archive.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            for name in handle.namelist():
                _check_archive_member(name)
            handle.extractall(dest)  # noqa: S202 - members validated above
        return
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.xz", ".txz")):
        import tarfile
        with tarfile.open(archive) as handle:
            for member in handle.getmembers():
                _check_archive_member(member.name)
            handle.extractall(dest)  # noqa: S202 - members validated above
        return
    raise BackendUnavailable(f"unsupported llama-server archive: {archive}")


def _find_exe_dir(root: str) -> str | None:
    for directory, _subdirs, files in os.walk(root):
        if "llama-server.exe" in files or "llama-server" in files:
            return directory
    return None


def _install_tree(source: str, target: str) -> None:
    for entry in os.listdir(source):
        src = os.path.join(source, entry)
        dst = os.path.join(target, entry)
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)


def ensure_binary(dest: str | None = None) -> str:
    """Fetch, digest-verify and unpack the pinned build; return the executable.

    The archive is checked against ``ASSET_SHA256`` *before* anything is
    unpacked, so a tampered or truncated download installs nothing. Refuses with
    :class:`BackendUnavailable` rather than returning a partial bundle: a stub
    without its DLLs is worse than no binary at all.

    Synchronous on purpose; callers use :func:`ensure_binary_async` to keep it
    off the hotkey/UI threads.
    """
    target = os.path.abspath(dest or binary_install_dir())
    existing = _exe_in_dir(target)
    if existing is not None and not _missing_members(existing):
        return existing

    url = _bundle_url()
    if not url:
        raise BackendUnavailable(
            f"the {BUNDLE.BUNDLE_TAG} release URL could not be resolved; set "
            f"{ENV_BINARY} or unpack the archive into {target} by hand.")

    os.makedirs(target, exist_ok=True)
    work = tempfile.mkdtemp(prefix="vt-llama-server-dl-", dir=target)
    try:
        archive = os.path.join(work, BUNDLE.ASSET_NAME)
        print(f"Downloading llama-server backend ({format_size(BUNDLE.ASSET_BYTES)}) "
              f"from {url}", flush=True)
        _fetch_archive(url, archive, BUNDLE.ASSET_NAME)
        digest = _sha256_file(archive)
        if digest != BUNDLE.ASSET_SHA256:
            raise BackendUnavailable(
                f"llama-server download failed its SHA-256 check (expected "
                f"{BUNDLE.ASSET_SHA256}, got {digest}); refusing to install it.")
        unpacked = os.path.join(work, "unpacked")
        os.makedirs(unpacked, exist_ok=True)
        _extract_archive(archive, unpacked)
        source = _find_exe_dir(unpacked)
        if source is None:
            raise BackendUnavailable(
                "the llama-server archive did not contain llama-server.exe")
        _install_tree(source, target)
        installed = _exe_in_dir(target)
        absent = (list(BUNDLE.REQUIRED_MEMBERS) if installed is None
                  else _missing_members(installed))
        if installed is None or absent:
            raise BackendUnavailable(
                "the llama-server bundle is incomplete after unpacking; missing "
                + ", ".join(absent))
        return installed
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ensure_binary_async(consented: bool = False, *, then=None) -> bool:
    """Start the backend fetch on a daemon thread; never blocks the caller.

    Consent mirrors the formatter *model* installer for the same reason: a
    background thread under a full-screen TUI has nowhere to ask. ``consented``
    is the explicit ``formatter-download`` verb; without it (and without a
    promptable stdin or ``VT_AUTO_DOWNLOAD_MODEL``) this reports the size and
    stops. ``then`` runs after a successful install (used to chain the weights).

    Returns whether an install was started (or is already running).
    """
    if (os.environ.get(ENV_SERVER_URL) or "").strip():
        return False
    if _binary() is not None:
        return False

    from voice_transcriber import model_download
    if model_download._status_handler is None:
        return False

    with _binary_async_lock:
        if _BINARY_KEY in _binary_inflight:
            return True
        needs_consent = (not consented and not model_download._can_prompt()
                         and not model_download._auto_download_enabled())
        if needs_consent:
            first_notice = _BINARY_KEY not in _binary_consent_notified
            _binary_consent_notified.add(_BINARY_KEY)
        else:
            _binary_inflight.add(_BINARY_KEY)
            first_notice = False

    if needs_consent:
        if first_notice:
            model_download._notify(
                "Formatter backend", 0,
                f"llama-server backend not installed "
                f"({format_size(BUNDLE.ASSET_BYTES)}); run \"formatter-download\" "
                f"to install it.")
        return False

    def _install() -> None:
        installed = None
        try:
            installed = ensure_binary()
        except Exception as exc:  # never raise into a daemon thread
            print(f"Background install of llama-server failed: {exc}", flush=True)
        finally:
            with _binary_async_lock:
                _binary_inflight.discard(_BINARY_KEY)
                _binary_consent_notified.discard(_BINARY_KEY)
        if installed is not None and then is not None:
            try:
                then()
            except Exception:
                logger.debug("post-install hook failed", exc_info=True)

    threading.Thread(target=_install, name="vt-llama-server-install",
                     daemon=True).start()
    return True


#: Model id -> the GGUF the release bundle installs for it. The model registry is
#: now the single declaration home for this (``FORMATTER.required_local``); this
#: map is the fallback for a build whose registry import fails, and a test pins it
#: to the registry entry so the two cannot drift.
MODEL_FILENAMES: dict[str, str] = {
    "s1-mini": "s1-mini-q4_k_m.gguf",
}

#: Used when the caller names no model, or names one this build does not know.
DEFAULT_MODEL_ID = "s1-mini"

#: Subdirectory under the app's data dir where the formatter weights live.
DEFAULT_MODEL_SUBDIR = "formatter"


def _registry_model_name() -> str:
    """Registry key for the shipped formatter model.

    Isolated from the rest of ``_candidate_model_dirs`` because a missing
    registry entry (a build without it, or an import failure) must not abort the
    conventional install path - which is the one that finds weights installed
    outside a checkout.
    """
    try:
        from voice_transcriber import model_download
        name = model_download.registry_name_for_model(DEFAULT_MODEL_ID)
        if name:
            return name
    except Exception:
        pass
    return DEFAULT_MODEL_SUBDIR


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

    Every lookup is isolated: a registry accessor can raise (a name this build
    does not know), and one shared ``try`` would let that abort the conventional
    per-user path, which is what finds weights installed outside a checkout.
    """
    dirs: list[str] = []
    try:
        from voice_transcriber import model_download
    except Exception:
        return dirs
    try:
        registry_dir = model_download.models_dir(_registry_model_name())
        if registry_dir:
            dirs.append(str(registry_dir))
    except Exception:
        pass
    try:
        dirs.append(os.path.join(str(model_download.get_data_dir()),
                                 "models", DEFAULT_MODEL_SUBDIR))
    except Exception:
        pass
    return dirs


def _model_path(model: str | None = None) -> str | None:
    """Locate the GGUF for ``model``, or ``None``.

    Explicit override, then the model registry, then the conventional install
    path. The registry is the real answer (``FORMATTER`` declares the filename
    and digest); the filename map and the per-user fallback stay so a build whose
    registry import fails - or weights installed outside the checkout - still
    resolves.
    """
    override = (os.environ.get(ENV_MODEL_PATH) or "").strip()
    if override:
        return override if os.path.exists(override) else None

    spec = None
    try:
        from voice_transcriber import model_download
        registry_name = model_download.registry_name_for_model(
            str(model or DEFAULT_MODEL_ID).strip().lower())
        if registry_name:
            spec = model_download.get_spec(registry_name)
    except Exception:
        spec = None

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


def _start_model_install(model: str | None, *, consented: bool) -> bool:
    """Ask the registry installer for the formatter weights, if it owns them."""
    try:
        from voice_transcriber import model_download
        registry_name = model_download.registry_name_for_model(
            str(model or DEFAULT_MODEL_ID).strip().lower())
        if not registry_name:
            return False
        return bool(model_download.ensure_model_async(
            registry_name, consented=consented))
    except Exception:
        return False


def request_model(model: str | None = None, *, consented: bool = False) -> bool:
    """Start a non-blocking install of the formatter backend and weights.

    Called when the formatter is enabled and in use but cannot run. Returns
    ``True`` when an install was started, ``False`` when there is nothing to do.
    Every guard is deliberate:

    * attach mode: the server is not ours and needs nothing;
    * no usable ``llama-server``: fetch the pinned backend first (size stated
      before anything downloads), then the weights, rather than giving up -- the
      old behaviour here was a dead end;
    * a model already resolves: nothing to fetch;
    * the model id is not a redistributable registry entry: not ours to fetch.

    ``consented=True`` means the user explicitly asked for the download (the
    ``formatter-download`` control verb). The first-use path leaves it False, so
    under a TUI the request reports the size and waits instead of prompting on
    stdin from a background thread (see :func:`model_download.ensure_model_async`).
    """
    if (os.environ.get(ENV_SERVER_URL) or "").strip():
        return False
    if _binary() is None:
        # Weights are useless without the runtime, so fetch it first; a completed
        # backend install then chains the weights with the same consent.
        return ensure_binary_async(
            consented=consented,
            then=lambda: _start_model_install(model, consented=True))
    if _model_path(model) is not None:
        return False
    return _start_model_install(model, consented=consented)


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

    Never raises: a malformed ``VT_FORMATTER_SERVER_URL`` reports this backend as
    unavailable rather than failing on every utterance.
    """
    if (os.environ.get(ENV_SERVER_URL) or "").strip():
        # Validate the URL even though we do not probe the server: one we cannot
        # speak must not be reported as available, or a typo becomes a failed
        # request every time instead of one honest "unavailable".
        try:
            _endpoint((os.environ.get(ENV_SERVER_URL) or "").strip())
        except Exception:
            return False
        return True
    if _state.get("spawn_failed_at"):
        if time.monotonic() - _state["spawn_failed_at"] < _SPAWN_RETRY_COOLDOWN_S:
            return False
    return _binary() is not None and _model_path(model) is not None


def _family(host: str) -> int:
    """Socket family for a literal host. IPv4 for anything that is not one.

    An address family, not a name service: ``getaddrinfo`` would answer this too,
    but it consults a resolver and this transport exists to consult nothing when
    it need not. See `_is_literal` for the check that decides whether a name
    needs resolving at all.
    """
    try:
        return (socket.AF_INET6 if ipaddress.ip_address(host).version == 6
                else socket.AF_INET)
    except ValueError:
        return socket.AF_INET


def _free_port(host: str = DEFAULT_HOST) -> int:
    """Uncontended loopback port, picked so a local process cannot predict it.

    Asking the kernel for port 0 hands back the *next* ephemeral port, which is
    sequential and therefore guessable - a local process can hold the port we are
    about to want. Random candidates cost the same, and a bind that fails is
    itself information: it tells us someone already holds that port, which the
    sequential form discards.

    Still racy - the port is released before llama-server binds it - but the port
    is no longer predictable and a squatter's own listener makes us skip it.
    ``_ensure_server`` covers what remains.
    """
    for _ in range(_PORT_ATTEMPTS):
        candidate = _PORT_RANGE[0] + secrets.randbelow(
            _PORT_RANGE[1] - _PORT_RANGE[0] + 1)
        try:
            with socket.socket(_family(host), socket.SOCK_STREAM) as sock:
                sock.bind((host, candidate))
            return candidate
        except OSError:
            continue
    # Every candidate was held. Start anyway on a kernel-picked port rather than
    # refuse: the settle check in `_ensure_server` is the real guard.
    with socket.socket(_family(host), socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _build_command(binary: str, model: str, host: str, port: int) -> list[str]:
    """The exact ``llama-server`` invocation.

    ``--jinja`` plus the ``enable_thinking`` template kwarg is the load-bearing
    part: without it the model can emit its reasoning as the answer. The
    sampling flags are pinned to greedy for determinism.
    """
    cmd = [
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
    ngl = (os.environ.get("VT_FORMATTER_GPU_LAYERS") or "").strip()
    if ngl:
        cmd.extend(["-ngl", ngl])
    elif sys.platform == "darwin":
        cmd.extend(["-ngl", "99"])
    return cmd


def _is_loopback(host: str) -> bool:
    """Whether ``host`` names this machine's own loopback.

    A name other than ``localhost`` is not accepted even if it happens to
    resolve to ``127.0.0.1``: the point of the check is to be decidable without a
    resolver, so that it cannot be talked around by a hostile DNS answer.
    """
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _endpoint(url: str) -> tuple[str, int]:
    """``(host, port)`` for a server URL, or raise if we should not talk to it.

    Refusals, all on the grounds that the failure would otherwise be silent:

    * anything that is not an ``http://host[:port]`` URL - a bare ``http://`` or a
      scheme-less ``127.0.0.1:8080`` used to fall back to host and port 80, so a
      typo became a request to a port nobody named;
    * ``https://`` - nothing here speaks TLS, so honouring the scheme would post
      the transcript in plaintext to port 80, minus the encryption it promised;
    * anything not on this machine - which is a deliberate act, and so needs
      ``VT_FORMATTER_ALLOW_REMOTE=1``. Spawn mode is already loopback; this is
      what makes "talks only to 127.0.0.1" a property of the code and not just of
      the default configuration.

    Raises :class:`BackendUnavailable` for every malformed input, never
    ``ValueError`` from ``urlparse``: this is called from :func:`available`, which
    is on the per-utterance path and is documented never to raise.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        port = parsed.port
        host = parsed.hostname
    except ValueError as exc:     # bad port, malformed IPv6 literal, ...
        raise BackendUnavailable(f"{url!r} is not a usable server URL: {exc}") from exc
    if parsed.scheme != "http" or not parsed.netloc:
        raise BackendUnavailable(
            f"{url!r} is not an http://host[:port] URL")
    if not host:
        raise BackendUnavailable(f"{url!r} names no host")
    if not _is_loopback(host) and (os.environ.get(ENV_ALLOW_REMOTE) or "").strip() != "1":
        raise BackendUnavailable(
            f"{host} is not this machine; the formatter talks to loopback only. "
            f"Set {ENV_ALLOW_REMOTE}=1 to send transcripts over the network "
            f"(in the clear - tunnel it if that matters).")
    # `port or 80` would turn an explicit `:0` into port 80.
    return host, 80 if port is None else port


def _host_header(host: str) -> bytes:
    """The ``Host:`` value. An IPv6 literal has to be bracketed to be valid."""
    return f"[{host}]".encode() if ":" in host else host.encode()


def _is_literal(host: str) -> bool:
    """Whether ``host`` is an IP literal, decided without a resolver."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _dial(host: str, port: int, timeout: float) -> socket.socket:
    """A connected socket to ``host:port``, with ``timeout`` set.

    A literal - which is every loopback case - is connected to directly, so the
    common path touches no resolver at all; that is the reason this module does
    not simply build on ``create_connection``. A *name* is resolved, as
    ``connect()`` always did behind our backs, but now across both address
    families instead of IPv4 only, so an IPv6-only host reaches too.
    """
    if not _is_literal(host):
        return socket.create_connection((host, port), timeout)
    sock = socket.socket(_family(host), socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((host, port))
    except Exception:
        sock.close()
        raise
    return sock


def _recv(sock: socket.socket, deadline: float, want: int) -> bytes:
    """One ``recv``, against a whole-request deadline rather than an idle gap.

    ``sock.settimeout`` alone never ends a read: a peer that keeps sending resets
    it every time, so the caller's budget is unreachable without this.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("formatter response exceeded its budget")
    sock.settimeout(remaining)
    return sock.recv(want)


def _read_response(sock: socket.socket, timeout_s: float,
                   limit: int = _MAX_RESPONSE_BYTES) -> tuple[int, bytes]:
    """Read one HTTP response as ``(status, body)``.

    Hand-rolled deliberately, like the rest of this module's transport: the whole
    point of not using ``http.client`` is that it consults nothing - no proxy, no
    resolver - and llama-server needs no more than ``Content-Length`` and the
    HTTP/1.0 close-to-signal-EOF rule.

    Two things a naive ``recv`` loop gets wrong and this does not: it stops at
    EOF, so a peer that holds the connection open hangs the caller; and it treats
    the timeout as an idle gap, so an endless stream never ends.
    """
    deadline = time.monotonic() + timeout_s
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = _recv(sock, deadline, 4096)
        if not chunk:
            raise BackendUnavailable("formatter server closed the connection early")
        head += chunk
        if len(head) > _MAX_HEAD_BYTES:
            raise BackendUnavailable("formatter response header is too large")

    raw_head, _, body = head.partition(b"\r\n\r\n")
    lines = raw_head.split(b"\r\n")
    tokens = lines[0].split()
    status = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else 500
    headers: dict[bytes, bytes] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(b":")
        if sep:
            headers[name.strip().lower()] = value.strip().lower()

    if headers.get(b"transfer-encoding") == b"chunked":
        # We ask for HTTP/1.0, so a compliant peer never does this. Say so
        # plainly instead of feeding chunk framing to json.loads.
        raise BackendUnavailable(
            "formatter server used chunked encoding, which is not supported")

    declared = headers.get(b"content-length")
    if declared is not None and declared.isdigit():
        if int(declared) > limit:
            raise BackendUnavailable(f"formatter response exceeds {limit} bytes")
        while len(body) < int(declared):
            chunk = _recv(sock, deadline, 4096)
            if not chunk:
                break
            body += chunk
        return status, body[:int(declared)]

    # No Content-Length: HTTP/1.0 ends the body by closing the connection.
    while True:
        chunk = _recv(sock, deadline, 4096)
        if not chunk:
            return status, body
        body += chunk
        if len(body) > limit:
            raise BackendUnavailable(f"formatter response exceeds {limit} bytes")


def _health_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        host, port = _endpoint(url)
        with _dial(host, port, timeout) as sock:
            sock.sendall(b"GET /health HTTP/1.0\r\nHost: " + _host_header(host)
                         + b"\r\n\r\n")
            # The body counts: stopping at the header terminator reads an empty
            # body whenever the two arrive in separate segments, and reports a
            # healthy server as dead.
            status, body = _read_response(sock, timeout, limit=_MAX_HEAD_BYTES)
        if status != 200:
            return False
        payload = json.loads(body.decode("utf-8") or "{}")
        return payload.get("status") in ("ok", "no slot available")
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
        # Refuse a scheme we cannot speak before reporting the server as usable.
        _endpoint(url)
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
            raise BackendUnavailable(binary_error_message())
        if model_path is None:
            raise BackendUnavailable(
                f"no formatter model found for {model or DEFAULT_MODEL_ID!r}. Set "
                f"{ENV_MODEL_PATH}, or use {ENV_SERVER_URL} to attach to a "
                f"running server.")

        port = _free_port()
        url = f"http://{DEFAULT_HOST}:{port}"
        command = _build_command(binary, model_path, DEFAULT_HOST, port)
        if binary.endswith(".py"):
            command = [sys.executable] + command
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

        spawned_at = time.monotonic()
        deadline = spawned_at + SPAWN_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = "\n".join(_stderr_tail)
                _state["spawn_failed_at"] = time.monotonic()
                raise BackendUnavailable(
                    f"llama-server exited with {proc.returncode}: {tail.strip()}")
            if _health_ok(url, timeout=0.5):
                # A local process can answer /health for us if it holds the port
                # first, and llama.cpp binds with SO_REUSEPORT (see httplib.h), so
                # holding the port with SO_REUSEPORT does *not* make it exit - it
                # shares the port. This check therefore only catches a squatter
                # that could not share it (SO_REUSEADDR alone), which llama-server
                # exits on. It is not identity verification; docs/TODO.md records
                # what is left open, and AF_UNIX is the fix when it matters.
                if time.monotonic() - spawned_at < _BIND_SETTLE_S:
                    time.sleep(_BIND_SETTLE_S - (time.monotonic() - spawned_at))
                    continue
                _state.update({"proc": proc, "url": url, "attached": False,
                               "spawn_failed_at": 0.0})
                return url
            time.sleep(_HEALTH_POLL_S)

        _terminate()
        proc.kill()
        _state["spawn_failed_at"] = time.monotonic()
        # Include what the child last said. This message is the *only* evidence on
        # a platform we cannot run (it cost two CI rounds on macOS to learn that
        # nothing was being reported here).
        tail = "\n".join(_stderr_tail)[-400:]
        raise BackendUnavailable(
            f"llama-server did not become healthy within {SPAWN_TIMEOUT_S:.0f}s; "
            f"its last output was {tail!r}")


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
    resolved = resolve_binary()
    return {
        "backend": "llama-server",
        "mode": "attach" if attached else "spawn",
        "server_url": _state.get("url"),
        "binary": resolved["path"],
        "binary_source": resolved["source"],
        "install_dir": resolved["install_dir"],
        "missing_members": resolved["missing"],
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
    host, port = _endpoint(url)
    with _dial(host, port, timeout_s) as sock:
        body = json.dumps(payload).encode("utf-8")
        sock.sendall(
            b"POST /v1/chat/completions HTTP/1.0\r\n"
            + b"Host: " + _host_header(host) + b"\r\n"
            + b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
            + body
        )
        status, raw = _read_response(sock, timeout_s)
    if status >= 400:
        detail = ""
        try:
            err = json.loads(raw.decode("utf-8", "replace"))
            err_val = err.get("error", "")
            detail = err_val if isinstance(err_val, str) else err_val.get("message", "")
        except Exception:
            pass
        raise BackendUnavailable(
            f"formatter HTTP {status}" + (f": {detail}" if detail else ""))
    return json.loads(raw.decode("utf-8"))


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
