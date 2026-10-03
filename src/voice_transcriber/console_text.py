"""Encoding-safe console output.

The app renders emoji and box-drawing glyphs: the ``⚡ saved`` badge, ``│`` and
``─`` rules, ``✓``/``✗`` CLI markers, the braille spinner. If the target stream
cannot encode them — a legacy Windows codepage on a redirected stream, ``LANG=C``
behind a pipe, or ``PYTHONIOENCODING=ascii`` — then ``print()`` raises
``UnicodeEncodeError``, and Rich *re-raises* it rather than degrading. The
transcription divider is printed inside a ``try/except``, so that failure is
silent: the divider and its timing/saved badge simply never appear.

Two layers prevent it:

1. :func:`harden_standard_streams` (called from the entry points) sets
   ``errors="replace"`` on the real stdout/stderr, so **no** write can raise,
   whatever anyone prints — including third-party code such as Rich.
2. :func:`ascii_text` and :class:`EncodingSafeStream` downgrade the glyphs *we*
   render to ASCII stand-ins (``⚡`` -> ``*``, ``│`` -> ``|``) so a non-UTF-8
   stream gets readable output instead of ``?``. The translation table is built
   per encoding and cached, and is empty for UTF-8, so the common path costs one
   dict lookup.

Only the glyphs this app actually renders are mapped; anything else exotic falls
through to layer 1's ``?`` rather than crashing.

Set ``VT_ASCII=1`` to force the full ASCII set regardless of the detected
encoding — useful when output is piped into a file that will later be read on a
legacy system.
"""

from __future__ import annotations

import os
import sys
import threading

ASCII_ENV = "VT_ASCII"

# Unicode glyph -> ASCII stand-in. Keys are the glyphs this app renders.
_GLYPHS = {
    "⚡": "*",          # time-saved badge
    "·": "|",           # badge separator
    "•": "-",
    "—": "--",
    "–": "-",
    "…": "...",
    "│": "|",
    "┃": "|",
    "─": "-",
    "━": "=",
    "❯": ">",
    "→": "->",
    "←": "<-",
    "✓": "+",
    "✕": "x",
    "✗": "x",
    "❌": "x",
    "⚠": "!",
    "🤖": "[SLM]",
    "🎤": "[mic]",
    "⚙": "[cfg]",
    "🎨": "[theme]",
    "░": ".",
    "█": "#",
    "\ufe0f": "",       # emoji variation selectors: drop them
    "\u200d": "",       # zero-width joiner
}

# Spinner frames (see SPINNER_FRAMES in tui.py) -> a rotating ASCII equivalent, so
# the spinner still animates instead of freezing on one character.
_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_SPINNER_ASCII = "|/-\\" * 3
for _i, _frame in enumerate(_SPINNER_FRAMES):
    _GLYPHS[_frame] = _SPINNER_ASCII[_i]

_tables: dict[tuple[str, bool], dict[int, str]] = {}
_lock = threading.Lock()


def encoding_of(stream=None) -> str:
    """The stream's encoding, defaulting to UTF-8 when it does not report one."""
    return getattr(stream if stream is not None else sys.stdout, "encoding", None) or "utf-8"


def _can_encode(text: str, encoding: str) -> bool:
    try:
        text.encode(encoding)
        return True
    except UnicodeEncodeError:
        return False
    except LookupError:  # unknown codec name: be conservative and downgrade
        return False


def force_ascii() -> bool:
    """True when ``VT_ASCII`` asks for the ASCII set unconditionally."""
    return os.environ.get(ASCII_ENV, "").strip().lower() in ("1", "true", "yes", "on", "always")


def _table(encoding: str, force: bool) -> dict[int, str]:
    key = (encoding, force)
    with _lock:
        table = _tables.get(key)
        if table is None:
            table = {
                ord(glyph): replacement
                for glyph, replacement in _GLYPHS.items()
                if force or not _can_encode(glyph, encoding)
            }
            _tables[key] = table
        return table


def ascii_text(text: str, stream=None) -> str:
    """Replace glyphs the target stream cannot encode with ASCII stand-ins.

    Cheap enough to call on every write: for UTF-8 the table is empty and this
    is a no-op lookup. Anything still un-encodable afterwards is left for the
    ``errors="replace"`` handler installed by :func:`harden_stream`.
    """
    if not text:
        return text
    table = _table(encoding_of(stream), force_ascii())
    if not table:
        return text
    return text.translate(table)


def write(text: str, stream=None) -> None:
    """Write ``text`` without ever raising ``UnicodeEncodeError``.

    Mirrors ``print`` in one more way: a console-less interpreter (``pythonw.exe``,
    a PyInstaller ``--noconsole`` build) has ``sys.stdout is None``, and ``print``
    silently does nothing there. So does this, rather than raising
    ``AttributeError``.
    """
    stream = stream if stream is not None else sys.stdout
    if stream is None:
        return
    downgraded = ascii_text(text, stream)
    try:
        stream.write(downgraded)
    except UnicodeEncodeError:
        # Last resort: something outside the glyph table is un-encodable.
        encoding = encoding_of(stream)
        stream.write(downgraded.encode(encoding, "replace").decode(encoding, "replace"))


def safe_print(*args, sep: str = " ", end: str = "\n", file=None, flush: bool = False) -> None:
    """A ``print`` that cannot raise on un-encodable text. Same signature as print."""
    stream = file if file is not None else sys.stdout
    if stream is None:
        return
    write(sep.join(str(arg) for arg in args) + end, stream)
    if flush:
        try:
            stream.flush()
        except Exception:
            pass


def harden_stream(stream) -> bool:
    """Make ``stream`` unable to raise on un-encodable text (``errors="replace"``).

    Returns True when the stream accepted the change. Streams that are not a
    real ``TextIOWrapper`` (pytest's capture objects, plain ``StringIO``) cannot
    fail on encoding anyway, so a no-op is fine.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    try:
        reconfigure(errors="replace")
        return True
    except Exception:
        return False


def harden_standard_streams() -> None:
    """Harden the process' stdout/stderr. Idempotent; safe to call repeatedly."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            harden_stream(stream)


class EncodingSafeStream:
    """File-like wrapper that downgrades glyphs before Rich writes them.

    Rich *re-raises* ``UnicodeEncodeError``, so one un-encodable glyph (``⚡``)
    makes ``Console.print`` fail and the whole divider disappears. Wrapping the
    console's file fixes every Rich write at once, with no changes to the drawing
    code.

    The wrapped stream is resolved on each write rather than captured, so
    replacing ``sys.stdout`` (pytest, a pager, the app itself) is still honoured.
    """

    def __init__(self, stream=None):
        self._stream = stream

    @property
    def stream(self):
        return self._stream if self._stream is not None else sys.stdout

    @property
    def encoding(self) -> str:
        return encoding_of(self.stream)

    def write(self, text: str) -> int:
        write(text, self.stream)
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        try:
            self.stream.flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return bool(getattr(self.stream, "isatty", lambda: False)())

    def __getattr__(self, name):
        # fileno() and friends must keep working: Rich uses them for terminal
        # detection and for the legacy-Windows rendering path. Underscore names
        # are not delegated, which also prevents __getattr__ recursing before
        # ``_stream`` is set.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self.stream, name)
