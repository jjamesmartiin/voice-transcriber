"""Out-of-process control API for a running Voice Transcriber engine.

This is the programmatic equivalent of the terminal frontend: any process can
drive the engine, and the user can do it from a shell. It exists so that other
programs can trigger dictation (or change a setting) without simulating
keystrokes, and so tests can drive a *real* running engine instead of poking at
internals.

Transport
---------
The engine binds a Unix-domain socket -- separate from the ratatui frontend
socket, so the two never contend:

    $XDG_RUNTIME_DIR/vt-control-<uid>.sock     (override: $VT_CONTROL_SOCKET)

Each client connection carries exactly one request and one reply, both
newline-delimited JSON::

    {"cmd": "toggle"}
    -> {"ok": true, "state": "RECORDING", ...}

Many clients may connect concurrently; each is served on its own daemon thread,
and a malformed request can never take the engine down.

CLI
---
The user-facing entry point is ``main.py`` itself::

    python src/main.py toggle      # start/stop recording
    python src/main.py start
    python src/main.py stop
    python src/main.py status
    python src/main.py help        # every verb
    python src/main.py help --json # machine-readable verb catalogue

Running ``python src/main.py`` with no verb still launches the app. Add
``--json`` for machine-readable output, or ``--socket PATH`` to target a
specific instance.

The full reference -- protocol, reply shapes, scripting recipes and notes for
LLM agents -- lives in ``docs/control_api.md``.

Hand-typed clients
------------------
A bare verb (no JSON) is accepted, so ``socat``/``nc`` work::

    printf 'toggle\\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/vt-control-$(id -u).sock"
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import tempfile
import threading

#: Environment variable that overrides the control socket path.
CONTROL_SOCKET_ENV = "VT_CONTROL_SOCKET"

#: Per-connection read/write timeout, and the default client timeout.
DEFAULT_TIMEOUT = 5.0

#: Upper bound on a single request, so a hostile client cannot exhaust memory.
MAX_REQUEST_BYTES = 1 << 20  # 1 MiB

#: Canonical verb -> machine-readable spec. Single source of truth for the
#: ``help`` verb, CLI validation and the docs, so a user or an LLM agent can
#: discover the entire surface with ``python src/main.py help --json``.
#:
#: Keys: ``summary`` (one line), ``value`` (name of the positional value),
#: ``choices`` (closed set of accepted values), ``required`` (value must be
#: given), ``toggles`` (omitting the value flips the current state),
#: ``returns`` (extra keys in the reply beyond ``ok``/``cmd``).
VERBS: dict[str, dict] = {
    "start": {
        "summary": "Start recording (no-op if already recording)",
    },
    "stop": {
        "summary": "Stop recording and transcribe",
    },
    "toggle": {
        "summary": "Start recording if idle, stop it if recording",
    },
    "status": {
        "summary": "Report engine state and settings",
        "returns": [
            "state", "recording", "device", "model", "muted", "output_mode",
            "number_mode", "punctuation_mode", "ui_theme", "middle_click",
            "last_transcription",
        ],
    },
    "wait": {
        "summary": "Block until the engine is idle again (transcription done), then report status",
        "value": "timeout_seconds",
        "returns": ["timed_out", "last_transcription"],
    },
    "mics": {
        "summary": "List input audio devices",
        "returns": ["devices"],
    },
    "set-mic": {
        "summary": "Switch microphone by name substring (first match wins)",
        "value": "device",
        "required": True,
    },
    "settings": {
        "summary": "Open the settings modal (the only config entry point)",
    },
    "mic": {
        "summary": "Open the microphone picker modal",
        "aliases": ["microphone"],
    },
    "theme": {
        "summary": "Set the UI theme, or open the theme picker when omitted",
        "value": "theme",
        "choices": ["auto", "green", "cyan", "blue", "magenta", "yellow", "red", "white"],
    },
    "output": {
        "summary": "Set how transcriptions are delivered",
        "value": "mode",
        "choices": ["clipboard", "type", "type_fast"],
        "required": True,
    },
    "numbers": {
        "summary": "Set spoken-number formatting",
        "value": "mode",
        "choices": ["auto", "digits", "words"],
        "required": True,
    },
    "punctuation": {
        "summary": "Set the formatting mode preset",
        "value": "mode",
        "choices": [
            "full", "no_terminal_period", "no_punctuation",
            "aesthetic_lowercase", "gen_z",
        ],
        "required": True,
    },
    "trailing-space": {
        "summary": "Append a space to auto-typed text",
        "value": "state",
        "choices": ["on", "off"],
        "toggles": True,
    },
    "auto-punctuate": {
        "summary": "Enforce terminal punctuation on auto-typed text",
        "value": "state",
        "choices": ["on", "off"],
        "toggles": True,
    },
    "serial": {
        "summary": "Collapse serial numbers / codes / NATO strings into one token",
        "value": "state",
        "choices": ["on", "off"],
        "toggles": True,
    },
    "spell": {
        "summary": "Enable the verbal spell command (\"spell C A T\" -> CAT)",
        "value": "state",
        "choices": ["on", "off"],
        "toggles": True,
    },
    "wpm": {
        "summary": "Set or cycle average typing speed in WPM for time saved calculations",
        "value": "words-per-minute",
        "aliases": ["typing-wpm", "set-wpm"],
        "returns": ["typing_wpm"],
    },
    "middle-click": {
        "summary": "Middle-mouse-button push-to-talk",
        "value": "state",
        "choices": ["on", "off"],
        "toggles": True,
        "returns": ["middle_click"],
    },
    "mute": {
        "summary": "Mute the audio cues",
        "value": "state",
        "choices": ["on", "off"],
        "toggles": True,
        "returns": ["muted"],
    },
    "reset-defaults": {
        "summary": "Restore every shipped default (keeps mic + dictionary)",
    },
    "reset-terminal": {
        "summary": "Reset terminal state and the clipboard bridge",
    },
    "ping": {
        "summary": "Check the engine is alive",
        "returns": ["pid"],
    },
    "help": {
        "summary": "Return this verb catalogue",
        "returns": ["verbs"],
    },
    "doctor": {
        "summary": "Run system diagnostics (audio devices, permissions, GPU/MPS, model weights)",
        "aliases": ["check"],
    },
    "quit": {
        "summary": "Shut the engine down",
    },
}


def normalize_verb(verb: str) -> str:
    """Canonicalise a verb: case-insensitive, ``_`` and ``-`` interchangeable."""
    norm = str(verb or "").strip().lower().replace("_", "-")
    if norm in VERBS:
        return norm
    for canonical, spec in VERBS.items():
        if norm in spec.get("aliases", []):
            return canonical
    return norm


def verb_spec(verb: str) -> dict:
    """Return the spec for ``verb`` (empty dict when unknown)."""
    return VERBS.get(normalize_verb(verb), {})

def socket_supported() -> bool:
    """True when this interpreter can create ``AF_UNIX`` sockets.

    Windows 10 1803+ supports them, so this is normally true everywhere; the
    check keeps the failure mode explicit rather than an obscure ``OSError``.
    """
    return hasattr(socket, "AF_UNIX")


def _user_identifier() -> str:
    """A per-user token for the socket name, portable to Windows."""
    getuid = getattr(os, "getuid", None)
    if callable(getuid):
        try:
            return str(getuid())
        except OSError:  # pragma: no cover - defensive
            pass
    return os.environ.get("USERNAME") or os.environ.get("USER") or "user"


def default_socket_path() -> str:
    """Return the control socket path for this user."""
    override = os.environ.get(CONTROL_SOCKET_ENV)
    if override:
        return override
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir:
        base = runtime_dir
    else:
        base = os.path.join(tempfile.gettempdir(), f"vt-{_user_identifier()}")
        try:
            os.makedirs(base, mode=0o700, exist_ok=True)
        except OSError:
            base = tempfile.gettempdir()
    return os.path.join(base, f"vt-control-{_user_identifier()}.sock")


class ControlServer:
    """Serve control requests by handing them to ``target.handle_control``.

    ``target`` is normally a :class:`main.SimpleVoiceTranscriber`; the only
    requirement is a ``handle_control(cmd, request) -> dict`` method, which
    keeps this module free of engine imports (and import cycles).
    """

    def __init__(self, target, socket_path: str | None = None) -> None:
        self.target = target
        self.socket_path = socket_path or default_socket_path()
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> bool:
        """Bind and serve. Returns ``False`` (never raises) if unavailable.

        A missing control API must never stop the app from running, so every
        failure here is reported by returning ``False``.
        """
        if self._started:
            return True
        if not socket_supported():
            return False
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(self.socket_path)
            try:
                os.chmod(self.socket_path, 0o600)
            except OSError:
                pass
            server.listen(8)
            server.settimeout(0.5)  # so the accept loop can notice _stop
        except OSError:
            return False

        self._server = server
        self._started = True
        self._thread = threading.Thread(
            target=self._serve, name="vt-control", daemon=True
        )
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop serving and remove the socket."""
        self._stop.set()
        self._started = False
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except OSError:
            pass

    # -- serving -----------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._handle,
                args=(conn,),
                name="vt-control-conn",
                daemon=True,
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        response: dict = {"ok": False, "error": "no request received"}
        try:
            conn.settimeout(DEFAULT_TIMEOUT)
            data = b""
            while b"\n" not in data and len(data) < MAX_REQUEST_BYTES:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            response = self.dispatch(data.split(b"\n", 1)[0].decode("utf-8", "replace"))
        except Exception as exc:  # a bad client must not kill the engine
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def dispatch(self, line: str) -> dict:
        """Parse one request line and route it to the engine."""
        text = (line or "").strip()
        if not text:
            return {"ok": False, "error": "empty request"}

        try:
            request = json.loads(text)
        except json.JSONDecodeError:
            # A bare verb is allowed so hand-typed clients can be lazy:
            #   printf 'toggle\n' | socat - UNIX-CONNECT:...
            request = {"cmd": text}

        if not isinstance(request, dict):
            return {"ok": False, "error": "request must be a JSON object"}

        cmd = str(request.get("cmd") or request.get("command") or "").strip()
        if not cmd:
            return {"ok": False, "error": "missing 'cmd'"}

        handler = getattr(self.target, "handle_control", None)
        if not callable(handler):
            return {"ok": False, "error": "engine exposes no control handler"}

        try:
            return handler(cmd, request)
        except Exception as exc:
            return {"ok": False, "cmd": cmd, "error": f"{type(exc).__name__}: {exc}"}


def send_command(
    cmd: str,
    socket_path: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    **fields,
) -> dict:
    """Send one command to the running engine and return its JSON reply.

    Never raises: transport problems come back as ``{"ok": False, "error": ...}``
    so callers (and shell scripts) can branch on ``ok``.
    """
    if not socket_supported():
        return {"ok": False, "error": "AF_UNIX sockets are unavailable on this platform"}

    path = socket_path or default_socket_path()
    request = {"cmd": cmd}
    request.update(fields)

    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        try:
            client.connect(path)
        except OSError as exc:
            return {
                "ok": False,
                "error": f"no running Voice Transcriber at {path} ({type(exc).__name__}: {exc})",
            }
        client.sendall((json.dumps(request) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = client.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        try:
            client.close()
        except OSError:
            pass

    reply = data.split(b"\n", 1)[0].decode("utf-8", "replace").strip()
    if not reply:
        return {"ok": False, "error": "engine closed the connection without replying"}
    try:
        return json.loads(reply)
    except json.JSONDecodeError:
        return {"ok": False, "error": f"unparseable reply: {reply!r}"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python src/main.py",
        description=(
            "Voice Transcriber. Run with no arguments to launch the app, or pass "
            "a control verb to drive an already-running instance."
        ),
        epilog="Control verbs: " + ", ".join(VERBS),
    )
    parser.add_argument("verb", nargs="?", help="Control verb (see `help`)")
    parser.add_argument("value", nargs="?", help="Value for verbs that take one")
    parser.add_argument(
        "--json", action="store_true", help="Print the raw JSON reply"
    )
    parser.add_argument(
        "--socket", default=None, help=f"Control socket path (default: ${CONTROL_SOCKET_ENV} or the per-user runtime path)"
    )
    return parser


def _print_verbs(stream=None) -> None:
    stream = stream or sys.stdout
    width = max(len(v) for v in VERBS)
    print("\nControl verbs (add --json for machine-readable output):", file=stream)
    for verb, spec in VERBS.items():
        line = f"  {verb:<{width}}  {spec['summary']}"
        if spec.get("choices"):
            line += f"  [{'|'.join(spec['choices'])}]"
        if spec.get("required"):
            line += "  (value required)"
        elif spec.get("toggles"):
            line += "  (omit to toggle)"
        print(line, file=stream)


def _print_human(verb: str, response: dict) -> None:
    if not response.get("ok"):
        print(f"✗ {response.get('error', 'command failed')}", file=sys.stderr)
        return

    if verb in ("status", "wait"):
        for key in (
            "state",
            "recording",
            "device",
            "model",
            "muted",
            "output_mode",
            "number_mode",
            "punctuation_mode",
            "ui_theme",
            "last_transcription",
        ):
            if key in response:
                print(f"{key}: {response[key]}")
        return

    if verb == "mics":
        for name in response.get("devices", []):
            print(name)
        return

    if verb == "help":
        _print_verbs()
        return

    detail = response.get("state") or response.get("device") or response.get("theme")
    print(f"✓ {verb}" + (f" → {detail}" if detail else ""))


def run_cli(argv=None, stream=None) -> int | None:
    """Run the control CLI.

    Returns an exit code, or ``None`` when no verb was given and the caller
    should fall through to launching the app normally.
    """
    args = build_parser().parse_args(argv)
    if not args.verb:
        return None

    verb = normalize_verb(args.verb)

    if verb in ("help", "-h", "--help"):
        # Served locally, so the catalogue is discoverable even when no instance
        # is running. The engine's own `help` verb returns the same spec.
        if args.json:
            print(
                json.dumps({"ok": True, "cmd": "help", "verbs": VERBS}, indent=2, sort_keys=True),
                file=stream or sys.stdout,
            )
        else:
            build_parser().print_help(stream)
            _print_verbs(stream)
        return 0

    if verb in ("doctor", "check"):
        import doctor

        res = doctor.run_doctor(json_format=bool(args.json), stream=stream or sys.stdout)
        return 0 if res.get("ok") else 1

    if verb not in VERBS:
        print(f"Unknown control verb: {verb!r}", file=sys.stderr)
        _print_verbs(sys.stderr)
        return 2

    fields: dict = {}
    if args.value is not None:
        fields["value"] = args.value

    response = send_command(verb, socket_path=args.socket, **fields)

    if args.json:
        print(json.dumps(response, indent=2, sort_keys=True))
    else:
        _print_human(verb, response)
    return 0 if response.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover - thin wrapper
    code = run_cli()
    sys.exit(0 if code is None else code)
