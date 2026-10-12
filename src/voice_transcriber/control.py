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
import logging
import math
import os
import secrets
import socket
import sys
import tempfile
import threading

import console_text

logger = logging.getLogger(__name__)

#: Environment variable that overrides the control socket path (AF_UNIX).
CONTROL_SOCKET_ENV = "VT_CONTROL_SOCKET"

#: Force a transport: ``unix`` or ``tcp``. Mostly for tests and for a Windows
#: host that wants to prove which one it is using.
CONTROL_TRANSPORT_ENV = "VT_CONTROL_TRANSPORT"

#: Where a TCP-transport engine publishes host/port/token.
CONTROL_ENDPOINT_ENV = "VT_CONTROL_ENDPOINT_FILE"

#: Transport names.
UNIX = "unix"
TCP = "tcp"

#: Loopback only. The control API must never be reachable off-host.
TCP_HOST = "127.0.0.1"

#: Per-connection read/write timeout, and the default client timeout.
DEFAULT_TIMEOUT = 5.0

#: Upper bound on a single request, so a hostile client cannot exhaust memory.
MAX_REQUEST_BYTES = 1 << 20  # 1 MiB

#: Cap on control connections served at once. A local process can open sockets
#: in a loop, and each one costs a thread plus up to MAX_REQUEST_BYTES buffered
#: before it has even proved it holds the token, so past this we close the
#: connection rather than spawn another thread.
MAX_CONCURRENT_CONNECTIONS = 32

#: A ``wait`` longer than this pins a control thread for no good reason, and an
#: unbounded value must not cost one for days. An hour is far above any real
#: transcription (a 90-minute meeting finishes well inside it) while still
#: bounding a hostile or fat-fingered request.
MAX_WAIT_SECONDS = 3600.0

#: Verbs whose ``value`` is stored or parsed verbatim: the speakers map and the
#: hotkey command. Bounded so a same-user client cannot make the engine handle
#: an arbitrarily large string. Well above any real speaker list or key chord,
#: so legitimate use never meets it; over-long input is rejected loudly, never
#: truncated. The 1 MiB ``MAX_REQUEST_BYTES`` cap already bounds the read.
MAX_VALUE_CHARS = 4096
_VERBATIM_VALUE_VERBS = frozenset({"speakers", "hotkey"})

#: Opt-in ceiling on a meeting capture, in seconds. UNSET by default, which is
#: today's behaviour exactly (no timer, no log line): a multi-hour workshop is
#: exactly what meeting mode exists for, so a default cap would harm the
#: legitimate case more than the accident it prevents. When set, the control API
#: stops the capture at the deadline and says in the log why. See docs/TODO.md.
ENV_MEETING_MAX_SECONDS = "VT_MEETING_MAX_SECONDS"

#: The smallest ceiling that is not obviously a typo.
MIN_MEETING_MAX_SECONDS = 60.0

#: Counting-down slots, one per live connection thread (see ``_serve``).
_CONNECTION_SLOTS = threading.Semaphore(MAX_CONCURRENT_CONNECTIONS)

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
            "number_mode", "punctuation_mode", "structure_mode",
            "structure_setting", "cleanup_mode", "formatter", "formatter_setting",
            "formatter_model", "formatter_style", "formatter_context",
            "meeting", "meeting_setting", "meeting_state", "meeting_elapsed_s",
            "meeting_progress", "meeting_spill_minutes", "meeting_stage",
            "meeting_transcript", "meeting_transcript_path", "meeting_speakers",
            "meeting_live_transcript",
            "diarization", "diarization_setting", "diarization_speakers",
            "diarization_model",
            "meeting_output_dir", "meeting_output_format", "speakers",
            "ui_theme", "middle_click",
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
    "rescan-mics": {
        "summary": "Re-scan input devices and re-resolve the selected mic (recovers one held by another app at startup)",
        "returns": ["devices", "device", "missing"],
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
    "structure": {
        "summary": "Set how spoken lists are formatted (flat, inline markers, or real bullets)",
        "value": "mode",
        "choices": ["off", "inline", "blocks"],
        "required": True,
        "returns": ["structure_mode", "structure_setting"],
    },
    "cleanup": {
        "summary": "Set how much the cleanup pass may change the words (keep everything, remove noise, or also resolve corrections)",
        "value": "mode",
        "choices": ["off", "artifacts", "full"],
        "required": True,
        "returns": ["cleanup_mode"],
    },
    "meeting": {
        "summary": "Enable long, non-injecting meeting capture (see docs/meeting_mode.md)",
        "value": "state",
        "choices": ["off", "on"],
        "toggles": True,
        "returns": ["meeting"],
    },
    "meeting-spill": {
        "summary": "Set the meeting capture in-memory spill threshold in minutes (1-240)",
        "value": "minutes",
        "returns": ["meeting_spill_minutes"],
    },
    "diarization": {
        "summary": "Label speakers in meeting transcripts (diarization); off never loads the model",
        "value": "state",
        "choices": ["off", "on"],
        "toggles": True,
        "returns": ["diarization", "diarization_setting"],
    },
    "diarization-speakers": {
        "summary": "Set the expected speaker count hint (auto, or 2-8); omit to cycle",
        "value": "count",
        "choices": ["auto", "2", "3", "4", "5", "6", "7", "8"],
        "returns": ["diarization_speakers"],
    },
    "diarization-model": {
        "summary": "Choose the diarization model (startup-loaded; needs a restart)",
        "value": "model",
        "choices": ["diarization"],
        "required": True,
        "returns": ["diarization_model"],
    },
    "meeting-output": {
        "summary": "Set (or report) the folder meeting transcripts are written to",
        "value": "folder",
        "aliases": ["meeting-output-dir", "meeting-output-folder"],
        "returns": ["meeting_output_dir"],
    },
    "meeting-format": {
        "summary": "Set the transcript form (text, json, or markdown); omit to cycle",
        "value": "format",
        "choices": ["text", "json", "markdown"],
        "returns": ["meeting_output_format"],
    },
    "speakers": {
        "summary": "List or set the meeting speaker names (ordered, comma-separated metadata)",
        "value": "names",
        "aliases": ["speaker-names", "speaker-map"],
        "returns": ["speakers"],
    },
    "meeting-start": {
        "summary": "Start a meeting capture (requires meeting mode on; does not inject text)",
        "returns": ["meeting_state", "meeting_elapsed_s", "meeting_progress"],
    },
    "meeting-stop": {
        "summary": "Stop a meeting capture and read its audio back for the batch pipeline",
        "returns": ["meeting_state", "meeting_elapsed_s", "meeting_progress"],
    },
    "formatter": {
        "summary": "Rewrite the transcript with an optional on-device model",
        "value": "state",
        "choices": ["off", "on"],
        "required": True,
        "returns": ["formatter", "formatter_setting"],
    },
    "formatter-model": {
        "summary": "Choose the formatter model",
        "value": "model",
        "choices": ["s1-mini"],
        "required": True,
        "returns": ["formatter_model"],
    },
    "formatter-style": {
        "summary": "Choose the formatter's writing style",
        "value": "style",
        "choices": ["casual", "semi-casual", "semi-formal", "formal"],
        "required": True,
        "returns": ["formatter_style"],
    },
    "formatter-context": {
        "summary": "Tell the formatter what the text is for",
        "value": "context",
        "choices": ["general", "email"],
        "required": True,
        "returns": ["formatter_context"],
    },
    "formatter-download": {
        "summary": ("Download the formatter backend (llama-server) and model now "
                    "(explicit consent; the first-use path will not prompt under "
                    "a TUI)"),
        "returns": ["formatter_download"],
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
    "hotkey": {
        "summary": "List, add, remove or reset the push-to-talk chord binds",
        "aliases": ["hotkeys", "binds"],
        "value": "command",
        "choices": [
            "list",
            "keys",
            "add <chord>",
            "remove <chord>",
            "reset",
        ],
        "returns": ["hotkeys", "keys"],
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
        "summary": "Run system diagnostics (audio devices, mic mute state, permissions, GPU/MPS, model weights)",
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

def unix_sockets_supported() -> bool:
    """True when this interpreter can create ``AF_UNIX`` sockets.

    Stock CPython on Windows never exposes ``socket.AF_UNIX`` (bpo-33408), even
    though the OS has supported it since Windows 10 1803. That is a CPython
    limitation, not a Windows one.
    """
    return hasattr(socket, "AF_UNIX")


def default_transport() -> str:
    """The transport this platform should serve over.

    ``AF_UNIX`` wherever it exists: the socket is addressed by a path, so
    filesystem permissions are the whole access-control story and nothing is
    exposed over the network stack. Native Windows falls back to loopback TCP,
    which is why the control API is no longer Unix-only.
    """
    explicit = (os.environ.get(CONTROL_TRANSPORT_ENV) or "").strip().lower()
    if explicit in (UNIX, TCP):
        return explicit
    if os.environ.get(CONTROL_SOCKET_ENV) and unix_sockets_supported():
        # An explicit socket *path* can only mean AF_UNIX, so honour it rather
        # than silently ignoring it in favour of the TCP transport. On a host
        # without AF_UNIX (stock Windows CPython) the path is meaningless, so
        # fall through to TCP instead of failing to start the API.
        return UNIX
    return UNIX if unix_sockets_supported() else TCP


def socket_supported() -> bool:
    """True when the control API can be served on this host.

    Every platform CPython supports can open a loopback TCP socket, so this is
    effectively always true now — it is kept because callers and the docs use it
    as "is the control API available here", and because a future transport-less
    platform should have one place to say so.
    """
    return unix_sockets_supported() or hasattr(socket, "socket")


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
    """Return the control socket path for this user (``AF_UNIX`` transport)."""
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


def default_endpoint_file() -> str:
    """Where a TCP-transport engine advertises host, port and token.

    A path alone cannot address a TCP socket, and the port is assigned by the OS
    so it cannot be guessed, so the running engine publishes an endpoint file next
    to where the ``AF_UNIX`` socket would have lived.
    """
    override = os.environ.get(CONTROL_ENDPOINT_ENV)
    if override:
        return override
    return os.path.join(
        tempfile.gettempdir(), f"vt-control-{_user_identifier()}.json"
    )


def _write_endpoint_file(path: str, host: str, port: int, token: str) -> None:
    """Publish the TCP endpoint, owner-only.

    Written to a private temp file and renamed into place instead of opened with
    ``O_TRUNC``: the endpoint lives in the shared temp dir, where another local
    user can pre-create it as a symlink (so a direct open corrupts an arbitrary
    file) or as a world-readable file (so the token is readable until the
    ``chmod``). ``os.replace`` swaps the *name*, so nothing is ever written
    through whatever was there.
    """
    payload = json.dumps(
        {"transport": TCP, "host": host, "port": int(port), "token": token}
    ).encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=".vt-control-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        try:
            os.chmod(tmp, 0o600)
        except OSError:  # pragma: no cover - Windows ACLs are not POSIX modes
            pass
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_endpoint_file(path: str | None = None) -> dict | None:
    """Read a published TCP endpoint, or ``None`` if there is not one."""
    target = path or default_endpoint_file()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    # Validate the port here so ``send_command``'s later use cannot raise
    # ``ValueError`` out of a function whose contract is to return an error reply.
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    # ``isinstance(True, int)`` is True, so a JSON ``true`` port must be excluded
    # explicitly; the host is used to build a connection, so it must be a name.
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    if not isinstance(data.get("host"), str) or not data["host"]:
        return None
    return data


def _remove_endpoint_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.unlink(path)
    except OSError:  # pragma: no cover - defensive
        pass


def _bound_wait(request: dict) -> dict:
    """Clamp a ``wait`` timeout to :data:`MAX_WAIT_SECONDS`.

    A caller can ask to block for days; nothing legitimately takes that long, and
    a control thread must not be pinned that long. A non-numeric value is left
    alone so the engine reports it as the bad input it is.
    """
    raw = request.get("value")
    if raw is None:
        raw = request.get("timeout_seconds")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return request
    if seconds <= MAX_WAIT_SECONDS:
        return request
    request = dict(request)
    if request.get("value") is not None:
        request["value"] = str(MAX_WAIT_SECONDS)
    else:
        request["timeout_seconds"] = str(MAX_WAIT_SECONDS)
    return request


def _meeting_max_seconds() -> float | None:
    """The opt-in meeting ceiling in seconds, or ``None`` when it is unset.

    Raises :class:`ValueError` for a set-but-unusable value so ``meeting-start``
    is refused loudly instead of silently running without the cap the user asked
    for. ``nan``/``inf`` are rejected too: a non-finite timer would blow up when
    scheduled, which is not a limit anyone meant to set.
    """
    raw = (os.environ.get(ENV_MEETING_MAX_SECONDS) or "").strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        raise ValueError(
            f"{ENV_MEETING_MAX_SECONDS} must be a number of seconds "
            f"(at least {MIN_MEETING_MAX_SECONDS:.0f}); got {raw!r}"
        ) from None
    if not math.isfinite(seconds) or seconds < MIN_MEETING_MAX_SECONDS:
        raise ValueError(
            f"{ENV_MEETING_MAX_SECONDS} must be at least "
            f"{MIN_MEETING_MAX_SECONDS:.0f} seconds; got {raw!r}"
        )
    return seconds


class ControlServer:
    """Serve control requests by handing them to ``target.handle_control``.

    ``target`` is normally a :class:`main.SimpleVoiceTranscriber`; the only
    requirement is a ``handle_control(cmd, request) -> dict`` method, which
    keeps this module free of engine imports (and import cycles).
    """

    def __init__(
        self,
        target,
        socket_path: str | None = None,
        transport: str | None = None,
        endpoint_file: str | None = None,
        token: str | None = None,
    ) -> None:
        self.target = target
        # An explicit ``socket_path`` can only mean the AF_UNIX transport; only
        # fall back to the platform default when neither is given.
        if transport:
            self.transport = transport
        elif socket_path:
            self.transport = UNIX
        else:
            self.transport = default_transport()
        self.socket_path = socket_path or default_socket_path()
        self.endpoint_file = endpoint_file or default_endpoint_file()
        #: Only meaningful for the TCP transport; generated at ``start`` if absent.
        self.token = token
        self.host = TCP_HOST
        self.port: int | None = None
        self._server: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False
        #: Opt-in meeting ceiling state (see ``VT_MEETING_MAX_SECONDS``).
        self._meeting_timer: threading.Timer | None = None
        self._meeting_generation = 0
        self._meeting_limit: float | None = None

    @property
    def address(self) -> str:
        """A human-readable address for logs and error messages."""
        if self.transport == TCP:
            return f"{self.host}:{self.port}" if self.port else self.endpoint_file
        return self.socket_path

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
        if self.transport == TCP:
            return self._start_tcp()
        return self._start_unix()

    def _start_unix(self) -> bool:
        if not unix_sockets_supported():
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
        except OSError as e:
            # Never raise: a missing control API must not stop the app. But say
            # *why* — the usual cause is a socket path longer than sun_path
            # (104 bytes on macOS, 108 on Linux), which is otherwise invisible.
            logger.warning(
                "Control API unavailable at %s (%s); the app runs, but control "
                "verbs are off for this session.",
                self.socket_path,
                e,
            )
            return False
        self._publish(server)
        return True

    def _start_tcp(self) -> bool:
        """Bind loopback TCP and publish the port and token.

        The port is assigned by the OS (``port 0``) rather than guessed, and the
        token is required on every request. Binding is to ``127.0.0.1`` only, so
        the only way in is from this host — the same trust boundary as the
        ``AF_UNIX`` socket's filesystem permissions.
        """
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, 0))
            server.listen(8)
            server.settimeout(0.5)
        except OSError as e:
            logger.warning(
                "Control API unavailable on %s (%s); the app runs, but control "
                "verbs are off for this session.",
                self.host,
                e,
            )
            return False

        self.port = server.getsockname()[1]
        self.token = self.token or secrets.token_urlsafe(32)
        try:
            _write_endpoint_file(
                self.endpoint_file, self.host, self.port, self.token
            )
        except OSError as e:
            logger.warning(
                "Control API could not publish its endpoint at %s (%s); control "
                "verbs would be unaddressable, so the server is stopping.",
                self.endpoint_file,
                e,
            )
            try:
                server.close()
            except OSError:
                pass
            return False
        self._publish(server)
        return True

    def _publish(self, server: socket.socket) -> None:
        self._server = server
        self._started = True
        self._thread = threading.Thread(
            target=self._serve, name="vt-control", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop serving and remove the socket."""
        self._stop.set()
        self._cancel_meeting_watchdog()
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
        if self.transport == TCP:
            _remove_endpoint_file(self.endpoint_file)
            self.port = None

    # -- the opt-in meeting ceiling ---------------------------------------
    def _arm_meeting_watchdog(self) -> None:
        """Start the ``VT_MEETING_MAX_SECONDS`` timer for a fresh meeting.

        Unset means nothing happens here at all, so the shipped behaviour is
        unchanged. The generation counter is what keeps a timer belonging to a
        previous meeting - cancelled, or merely slow to be scheduled - from
        stopping the next one.
        """
        try:
            seconds = _meeting_max_seconds()
        except ValueError:
            # ``dispatch`` already refused the request before the handler ran.
            return
        if seconds is None:
            return
        # A second successful start (the engine normally rejects one) must not
        # leave the previous timer running unwatched.
        self._cancel_meeting_watchdog()
        self._meeting_generation += 1
        self._meeting_limit = seconds
        timer = threading.Timer(
            seconds, self._meeting_timeout, args=(self._meeting_generation,)
        )
        timer.daemon = True
        self._meeting_timer = timer
        timer.start()

    def _cancel_meeting_watchdog(self) -> None:
        """Cancel a pending ceiling timer. Safe to call when there is none."""
        timer = self._meeting_timer
        self._meeting_timer = None
        if timer is not None:
            timer.cancel()

    def _meeting_timeout(self, generation: int) -> None:
        """Stop a meeting that outran the opt-in ceiling.

        Refuses to touch a later meeting: a ``generation`` that is no longer
        current means this timer belonged to one that already ended, so it stays
        out of the way. Says in the log why it acted, so the stop is never
        silent.
        """
        if generation != self._meeting_generation:
            return
        self._meeting_timer = None
        logger.warning(
            "meeting capture reached the %s cap (%s seconds); stopping it so a "
            "forgotten capture cannot fill the disk. Raise or unset %s to remove "
            "the cap.",
            ENV_MEETING_MAX_SECONDS, self._meeting_limit, ENV_MEETING_MAX_SECONDS,
        )
        try:
            self.target.handle_control("meeting-stop", {"cmd": "meeting-stop"})
        except Exception:
            logger.warning(
                "could not stop the over-long meeting capture", exc_info=True
            )

    # -- serving -----------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not _CONNECTION_SLOTS.acquire(blocking=False):
                # At the cap: refuse immediately rather than spawn a thread that
                # would read up to MAX_REQUEST_BYTES before authenticating.
                try:
                    conn.close()
                except OSError:
                    pass
                continue
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
            # Release the slot only after the connection is done, so the cap
            # tracks live work rather than threads merely spawned.
            _CONNECTION_SLOTS.release()

    def _authorized(self, request: dict) -> bool:
        """Gate the TCP transport on a shared token.

        Loopback is not a security boundary on a shared machine — any process can
        connect to 127.0.0.1 — so the token is what stands in for the ``AF_UNIX``
        socket's file permissions. It is compared in constant time, and a
        mismatch reports only "unauthorized" so a caller learns nothing from
        probing.
        """
        if self.transport != TCP:
            return True
        expected = self.token or ""
        supplied = str(request.get("token") or "")
        # Compared as UTF-8 bytes: ``compare_digest`` raises ``TypeError`` on a
        # non-ASCII ``str``, which would leak an exception into a pre-auth reply
        # instead of answering with the documented "unauthorized".
        return bool(expected) and secrets.compare_digest(
            expected.encode("utf-8"), supplied.encode("utf-8"))

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

        if not self._authorized(request):
            return {"ok": False, "error": "unauthorized"}

        cmd = str(request.get("cmd") or request.get("command") or "").strip()
        if not cmd:
            return {"ok": False, "error": "missing 'cmd'"}

        verb = normalize_verb(cmd)
        if verb == "wait":
            request = _bound_wait(request)
        elif verb in _VERBATIM_VALUE_VERBS:
            raw = request.get("value")
            length = len(str(raw)) if raw is not None else 0
            if length > MAX_VALUE_CHARS:
                return {
                    "ok": False,
                    "cmd": cmd,
                    "error": (
                        f"{verb} value is {length} characters, over the "
                        f"{MAX_VALUE_CHARS}-character control-API limit"
                    ),
                }
        elif verb == "meeting-start":
            # Refuse a ceiling we cannot honour before starting the capture, so a
            # typo does not silently run unbounded (or unguarded).
            try:
                _meeting_max_seconds()
            except ValueError as exc:
                return {"ok": False, "cmd": cmd, "error": str(exc)}

        handler = getattr(self.target, "handle_control", None)
        if not callable(handler):
            return {"ok": False, "error": "engine exposes no control handler"}

        try:
            reply = handler(cmd, request)
        except Exception as exc:
            return {"ok": False, "cmd": cmd, "error": f"{type(exc).__name__}: {exc}"}

        # Move the ceiling only around a *successful* lifecycle change, so a
        # rejected request never shifts the watchdog's aim at the next meeting.
        if isinstance(reply, dict) and reply.get("ok"):
            if verb == "meeting-start":
                self._arm_meeting_watchdog()
            elif verb == "meeting-stop":
                self._cancel_meeting_watchdog()
        return reply


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
        return {"ok": False, "error": "the control API is unavailable on this platform"}

    request = {"cmd": cmd}
    request.update(fields)

    # An explicit path always means the AF_UNIX transport; otherwise this platform
    # picks. On native Windows that is loopback TCP, because CPython there has no
    # socket.AF_UNIX.
    transport = UNIX if socket_path else default_transport()
    if not socket_path and transport == UNIX:
        # A published TCP endpoint wins over a unix socket that is not there: the
        # engine may be on the other side of a WSL/Windows boundary, where this
        # client has AF_UNIX but the engine does not.
        if not os.path.exists(default_socket_path()) and read_endpoint_file():
            transport = TCP

    use_tcp = not socket_path and transport == TCP
    if use_tcp:
        endpoint = read_endpoint_file()
        if not endpoint:
            return {
                "ok": False,
                "error": (
                    "no running Voice Transcriber found (no control endpoint "
                    f"published at {default_endpoint_file()})"
                ),
            }
        host = str(endpoint.get("host") or TCP_HOST)
        port = endpoint["port"]  # validated as an int by read_endpoint_file
        request["token"] = endpoint.get("token") or ""
        address = f"{host}:{port}"
        family: int = socket.AF_INET
        destination = (host, port)
    else:
        if not unix_sockets_supported():
            return {
                "ok": False,
                "error": "AF_UNIX sockets are unavailable on this platform",
            }
        address = socket_path or default_socket_path()
        family = socket.AF_UNIX
        destination = address

    client = socket.socket(family, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        try:
            client.connect(destination)
        except OSError as exc:
            return {
                "ok": False,
                "error": (
                    f"no running Voice Transcriber at {address} "
                    f"({type(exc).__name__}: {exc})"
                ),
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
        "--fix",
        action="store_true",
        help="doctor: repair what it safely can (currently: unmute a muted default microphone)",
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


def _plural(count: int, singular: str) -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


def _print_stats_summary(response: dict) -> None:
    """One line of lifetime dictation totals (absent on engines that predate them)."""
    if "lifetime_time_saved" not in response:
        return
    parts = [f"time saved: {response.get('lifetime_time_saved', '0s')} all-time"]
    if response.get("lifetime_words"):
        parts.append(_plural(response["lifetime_words"], "word"))
    if response.get("lifetime_transcriptions"):
        parts.append(_plural(response["lifetime_transcriptions"], "dictation"))
    if response.get("lifetime_sessions"):
        parts.append(_plural(response["lifetime_sessions"], "session"))
    session_sec = response.get("session_time_saved_sec")
    if session_sec:
        parts.append(f"{round(session_sec)}s this session")
    # safe_print: a stream that cannot encode "·" gets "|" instead of a crash.
    console_text.safe_print(" · ".join(parts))


def _print_human(verb: str, response: dict) -> None:
    if not response.get("ok"):
        console_text.safe_print(f"✗ {response.get('error', 'command failed')}", file=sys.stderr)
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
                console_text.safe_print(f"{key}: {response[key]}")
        _print_stats_summary(response)
        return

    if verb == "mics":
        for name in response.get("devices", []):
            console_text.safe_print(name)
        return

    if verb == "help":
        _print_verbs()
        return

    detail = response.get("state") or response.get("device") or response.get("theme")
    console_text.safe_print(f"✓ {verb}" + (f" → {detail}" if detail else ""))


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

        res = doctor.run_doctor(
            json_format=bool(args.json),
            stream=stream or sys.stdout,
            fix=bool(getattr(args, "fix", False)),
        )
        return 0 if res.get("ok") else 1

    if verb not in VERBS:
        console_text.safe_print(f"Unknown control verb: {verb!r}", file=sys.stderr)
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
