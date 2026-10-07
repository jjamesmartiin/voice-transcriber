"""User-configurable push-to-talk bindings, written in OS-neutral terms.

A *bind* is a chord of keys plus the action it triggers::

    {"keys": ["alt", "shift"], "action": "dictate"}

The chord is spelled the way a user thinks of it -- ``alt+shift``,
``rightctrl+shift``, ``f13`` -- and this module is the single place that knows
what those names mean. The three backends (Linux ``evdev``, native Windows
``pynput`` and the WSL PowerShell host bridge) translate the *same* canonical
names through the *same* tables, so a user who rebinds cannot get three
different keyboards depending on which host they launched.

Only the ``dictate`` action ships today, but binds are stored as
``keys -> action`` so a second action (alternate model, re-transcribe) can be
added without migrating anyone's config.

Semantics every backend must honour
-----------------------------------
* A chord is satisfied **by one device at a time**: holding ``Alt`` on one
  keyboard and ``Shift`` on another is a legitimate chord, but the two keys of
  a chord never have to be attributed to the same event stream. (On Linux each
  device's event stream is reconciled against its own kernel bitmap; see
  ``platform/linux/hotkeys.py``.)
* A bare ``alt``/``ctrl``/``shift``/``meta`` matches *either* side, while
  ``leftalt``/``rightctrl``/... match exactly one. This mirrors the pre-bind
  behaviour, where ``ALT_KEYS`` was ``[KEY_LEFTALT, KEY_RIGHTALT]``.
* Mouse buttons are deliberately **not** bindable: the middle button already
  has its own toggle with tap-vs-hold semantics (``middle_click_enabled``), and
  folding it into chords would change that gesture for everyone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The key table
# ---------------------------------------------------------------------------
# One row per bindable key. This is the *only* place a key is declared: every
# table the backends resolve through -- canonical names, aliases, the Linux
# evdev codes, the Windows virtual-key codes and the pynput attributes -- is
# derived from KEYS below, so adding a key is one line here instead of an edit
# in five tables. A test asserts every row is complete, so a half-added key
# fails loudly rather than silently never matching.
#
# ``evdev`` and ``vk`` are tuples because a bare modifier means "either side":
# that is how ``ALT_KEYS = [56, 100]`` used to be spelled inline in the Linux
# backend, and the WSL bridge polls one ``GetAsyncKeyState`` per entry.
# ``pynput`` names the ``pynput.keyboard.Key`` attributes that satisfy the name
# and is empty for letters/digits, which resolve through their character.
# Sided rows come *before* the bare row so a key press resolves to the precise
# name (``rightctrl``, not ``ctrl``) and the bare name stays the either-side
# case; ``alt_gr`` is the right-hand Option/Alt on several layouts.

#: Groups a row may belong to, in the order UIs should show them.
KEY_GROUPS: tuple[str, ...] = (
    "modifiers", "function", "navigation", "editing", "system", "letters", "digits",
)


@dataclass(frozen=True)
class KeyDef:
    """One bindable key, declared once (see :data:`KEYS`)."""

    name: str                      # canonical name: what binds and configs use
    group: str                     # for grouping in UIs and error messages
    evdev: tuple[int, ...]         # Linux key codes (any-of)
    vk: tuple[int, ...]            # Windows virtual-key codes (any-of)
    pynput: tuple[str, ...] = ()   # pynput ``Key`` attributes (any-of), if any
    aliases: tuple[str, ...] = ()  # other spellings users type


#: Every bindable key. Add a row here to add a key.
KEYS: tuple[KeyDef, ...] = (
    # --- modifiers: sided first, then the bare name = either side ------------
    KeyDef("leftctrl", "modifiers", (29,), (0xA2,), ("ctrl_l",),
           ("leftcontrol", "lctrl")),
    KeyDef("rightctrl", "modifiers", (97,), (0xA3,), ("ctrl_r",),
           ("rightcontrol", "rctrl")),
    KeyDef("ctrl", "modifiers", (29, 97), (0xA2, 0xA3),
           ("ctrl_l", "ctrl_r", "ctrl"), ("control",)),
    KeyDef("leftalt", "modifiers", (56,), (0xA4,), ("alt_l",),
           ("leftoption", "lalt")),
    # On Windows pynput maps VK_RMENU onto ``Key.alt_gr`` (sharing the virtual
    # key with ``Key.alt_r``), so the right-hand Alt accepts either spelling.
    KeyDef("rightalt", "modifiers", (100,), (0xA5,), ("alt_r", "alt_gr"),
           ("rightoption", "ralt")),
    KeyDef("alt", "modifiers", (56, 100), (0xA4, 0xA5),
           ("alt_l", "alt_r", "alt_gr", "alt"), ("option",)),
    KeyDef("leftshift", "modifiers", (42,), (0xA0,), ("shift_l",), ("lshift",)),
    KeyDef("rightshift", "modifiers", (54,), (0xA1,), ("shift_r",), ("rshift",)),
    KeyDef("shift", "modifiers", (42, 54), (0xA0, 0xA1),
           ("shift_l", "shift_r", "shift")),
    KeyDef("leftmeta", "modifiers", (125,), (0x5B,), ("cmd_l",),
           ("leftsuper", "leftcmd", "leftwin", "lmeta")),
    KeyDef("rightmeta", "modifiers", (126,), (0x5C,), ("cmd_r",),
           ("rightsuper", "rightcmd", "rightwin", "rmeta")),
    KeyDef("meta", "modifiers", (125, 126), (0x5B, 0x5C),
           ("cmd_l", "cmd_r", "cmd"),
           ("super", "cmd", "command", "win", "windows", "gui")),
    # --- function keys (pynput exposes f1-f20; f21-f24 still resolve on
    #     Windows through their VK codes) -----------------------------------
    KeyDef("f1", "function", (59,), (0x70,), ("f1",)),
    KeyDef("f2", "function", (60,), (0x71,), ("f2",)),
    KeyDef("f3", "function", (61,), (0x72,), ("f3",)),
    KeyDef("f4", "function", (62,), (0x73,), ("f4",)),
    KeyDef("f5", "function", (63,), (0x74,), ("f5",)),
    KeyDef("f6", "function", (64,), (0x75,), ("f6",)),
    KeyDef("f7", "function", (65,), (0x76,), ("f7",)),
    KeyDef("f8", "function", (66,), (0x77,), ("f8",)),
    KeyDef("f9", "function", (67,), (0x78,), ("f9",)),
    KeyDef("f10", "function", (68,), (0x79,), ("f10",)),
    KeyDef("f11", "function", (87,), (0x7A,), ("f11",)),
    KeyDef("f12", "function", (88,), (0x7B,), ("f12",)),
    KeyDef("f13", "function", (183,), (0x7C,), ("f13",)),
    KeyDef("f14", "function", (184,), (0x7D,), ("f14",)),
    KeyDef("f15", "function", (185,), (0x7E,), ("f15",)),
    KeyDef("f16", "function", (186,), (0x7F,), ("f16",)),
    KeyDef("f17", "function", (187,), (0x80,), ("f17",)),
    KeyDef("f18", "function", (188,), (0x81,), ("f18",)),
    KeyDef("f19", "function", (189,), (0x82,), ("f19",)),
    KeyDef("f20", "function", (190,), (0x83,), ("f20",)),
    KeyDef("f21", "function", (191,), (0x84,), ("f21",)),
    KeyDef("f22", "function", (192,), (0x85,), ("f22",)),
    KeyDef("f23", "function", (193,), (0x86,), ("f23",)),
    KeyDef("f24", "function", (194,), (0x87,), ("f24",)),
    # --- navigation ---------------------------------------------------------
    KeyDef("up", "navigation", (103,), (0x26,), ("up",), ("arrowup",)),
    KeyDef("down", "navigation", (108,), (0x28,), ("down",), ("arrowdown",)),
    KeyDef("left", "navigation", (105,), (0x25,), ("left",), ("arrowleft",)),
    KeyDef("right", "navigation", (106,), (0x27,), ("right",), ("arrowright",)),
    KeyDef("home", "navigation", (102,), (0x24,), ("home",)),
    KeyDef("end", "navigation", (107,), (0x23,), ("end",)),
    KeyDef("pageup", "navigation", (104,), (0x21,), ("page_up",), ("pgup",)),
    KeyDef("pagedown", "navigation", (109,), (0x22,), ("page_down",), ("pgdn",)),
    # --- editing ------------------------------------------------------------
    KeyDef("space", "editing", (57,), (0x20,), ("space",)),
    KeyDef("tab", "editing", (15,), (0x09,), ("tab",)),
    KeyDef("esc", "editing", (1,), (0x1B,), ("esc",), ("escape",)),
    KeyDef("enter", "editing", (28,), (0x0D,), ("enter",), ("return",)),
    KeyDef("backspace", "editing", (14,), (0x08,), ("backspace",)),
    KeyDef("delete", "editing", (111,), (0x2E,), ("delete",), ("del",)),
    KeyDef("insert", "editing", (110,), (0x2D,), ("insert",), ("ins",)),
    # --- system keys --------------------------------------------------------
    KeyDef("capslock", "system", (58,), (0x14,), ("caps_lock",), ("caps",)),
    KeyDef("printscreen", "system", (99,), (0x2C,), ("print_screen",),
           ("print", "prtsc")),
    KeyDef("scrolllock", "system", (70,), (0x91,), ("scroll_lock",)),
    KeyDef("pause", "system", (119,), (0x13,), ("pause",)),
    KeyDef("menu", "system", (127,), (0x5D,), ("menu",), ("apps",)),
    # --- letters (Linux codes are not ASCII; VK codes are) ------------------
    KeyDef("a", "letters", (30,), (ord("A"),)),
    KeyDef("b", "letters", (48,), (ord("B"),)),
    KeyDef("c", "letters", (46,), (ord("C"),)),
    KeyDef("d", "letters", (32,), (ord("D"),)),
    KeyDef("e", "letters", (18,), (ord("E"),)),
    KeyDef("f", "letters", (33,), (ord("F"),)),
    KeyDef("g", "letters", (34,), (ord("G"),)),
    KeyDef("h", "letters", (35,), (ord("H"),)),
    KeyDef("i", "letters", (23,), (ord("I"),)),
    KeyDef("j", "letters", (36,), (ord("J"),)),
    KeyDef("k", "letters", (37,), (ord("K"),)),
    KeyDef("l", "letters", (38,), (ord("L"),)),
    KeyDef("m", "letters", (50,), (ord("M"),)),
    KeyDef("n", "letters", (49,), (ord("N"),)),
    KeyDef("o", "letters", (24,), (ord("O"),)),
    KeyDef("p", "letters", (25,), (ord("P"),)),
    KeyDef("q", "letters", (16,), (ord("Q"),)),
    KeyDef("r", "letters", (19,), (ord("R"),)),
    KeyDef("s", "letters", (31,), (ord("S"),)),
    KeyDef("t", "letters", (20,), (ord("T"),)),
    KeyDef("u", "letters", (22,), (ord("U"),)),
    KeyDef("v", "letters", (47,), (ord("V"),)),
    KeyDef("w", "letters", (17,), (ord("W"),)),
    KeyDef("x", "letters", (45,), (ord("X"),)),
    KeyDef("y", "letters", (21,), (ord("Y"),)),
    KeyDef("z", "letters", (44,), (ord("Z"),)),
    # --- digits (top row: "1" is evdev 2, "0" is evdev 11) -------------------
    KeyDef("0", "digits", (11,), (0x30,)),
    KeyDef("1", "digits", (2,), (0x31,)),
    KeyDef("2", "digits", (3,), (0x32,)),
    KeyDef("3", "digits", (4,), (0x33,)),
    KeyDef("4", "digits", (5,), (0x34,)),
    KeyDef("5", "digits", (6,), (0x35,)),
    KeyDef("6", "digits", (7,), (0x36,)),
    KeyDef("7", "digits", (8,), (0x37,)),
    KeyDef("8", "digits", (9,), (0x38,)),
    KeyDef("9", "digits", (10,), (0x39,)),
)

# ---------------------------------------------------------------------------
# Everything below is derived from KEYS -- do not restate these by hand.
# ---------------------------------------------------------------------------

#: Every name :func:`parse_chord` accepts, in declaration (display) order.
CANONICAL_NAMES: tuple[str, ...] = tuple(key.name for key in KEYS)

#: Canonical modifier names, for UI grouping.
MODIFIER_NAMES: tuple[str, ...] = tuple(
    key.name for key in KEYS if key.group == "modifiers"
)

#: Spellings users actually type, mapped onto the canonical names.
ALIASES: dict[str, str] = {
    alias: key.name for key in KEYS for alias in key.aliases
}

#: Canonical name -> Linux ``evdev`` key codes (any-of for bare modifiers).
EVDEV_CODES: dict[str, tuple[int, ...]] = {key.name: key.evdev for key in KEYS}

#: Canonical name -> Windows virtual-key codes (any-of for bare modifiers).
#: The WSL PowerShell bridge polls these with ``GetAsyncKeyState``.
VK_CODES: dict[str, tuple[int, ...]] = {key.name: key.vk for key in KEYS}

#: Canonical name -> the pynput ``Key`` attributes that satisfy it (any-of).
#: The pynput backends resolve through this, so the attribute spelling for a key
#: lives in the same row as its evdev and VK codes rather than in a per-backend
#: copy. Missing members are skipped at lookup time (``getattr(..., None)``), so
#: declaring an attribute pynput does not define on a given platform is harmless.
PYNPUT_ATTRS: dict[str, tuple[str, ...]] = {
    key.name: key.pynput for key in KEYS
}


def _reverse_pynput_map() -> dict[str, str]:
    """``pynput`` attribute -> canonical name, first declaration wins.

    Sided rows are declared before the bare ones, so a press resolves to the
    precise name (``rightctrl``, never ``ctrl``) while a bare ``alt`` stays the
    either-side case for :func:`bind_hit`.
    """
    reverse: dict[str, str] = {}
    for key in KEYS:
        for attr in key.pynput:
            reverse.setdefault(attr, key.name)
    return reverse


#: pynput ``Key`` attribute -> the canonical name it resolves to.
PYNPUT_ATTR_TO_NAME: dict[str, str] = _reverse_pynput_map()


def catalogue() -> list[dict]:
    """Every bindable key grouped for a UI: ``[{name, group, aliases}, ...]``.

    Derived from :data:`KEYS`, so the list a user sees cannot drift from the
    list the backends accept -- this is what ``hotkey keys`` returns.
    """
    return [
        {"name": key.name, "group": key.group, "aliases": list(key.aliases)}
        for key in KEYS
    ]


def describe_keys() -> str:
    """Compact comma-separated list of every canonical name, for error text."""
    return ", ".join(CANONICAL_NAMES)


def validate_keys() -> None:
    """Assert that :data:`KEYS` is complete and unambiguous.

    Runs at import (replacing the old "every name has a code" check) and from
    the test suite, so a half-added key -- one platform's code missing, an
    unknown group, an alias colliding with another name -- fails loudly instead
    of silently never matching a keypress.
    """
    names = {key.name for key in KEYS}
    aliases: dict[str, str] = {}
    for key in KEYS:
        assert key.group in KEY_GROUPS, f"{key.name}: unknown group {key.group!r}"
        assert key.evdev and key.vk, f"{key.name}: needs a code on every platform"
        for alias in key.aliases:
            assert alias not in names, f"alias {alias!r} shadows a canonical key"
            assert alias not in aliases, f"alias {alias!r} is declared twice"
            aliases[alias] = key.name
    assert len(names) == len(KEYS), "duplicate name in KEYS"


validate_keys()

#: Actions a bind may name. Only ``dictate`` exists today.
ACTIONS: tuple[str, ...] = ("dictate",)

DEFAULT_ACTION = "dictate"

#: The chord this app has always shipped with.
DEFAULT_CHORD: tuple[str, ...] = ("alt", "shift")


class KeybindError(ValueError):
    """A bind could not be understood. Message is user-facing."""


@dataclass(frozen=True)
class Bind:
    """A chord of canonical key names plus the action it triggers."""

    keys: tuple[str, ...]
    action: str = DEFAULT_ACTION

    @property
    def chord(self) -> str:
        """Canonical spelling, e.g. ``alt+shift``."""
        return format_chord(self.keys)

    def to_config(self) -> dict:
        return {"keys": list(self.keys), "action": self.action}


DEFAULT_BINDS: tuple[Bind, ...] = (Bind(DEFAULT_CHORD, DEFAULT_ACTION),)


def canonical_name(name: str) -> str:
    """Resolve one user-typed key name to its canonical spelling."""
    key = str(name).strip().lower().replace(" ", "")
    key = ALIASES.get(key, key)
    if key not in CANONICAL_NAMES:
        raise KeybindError(
            f"unknown key {name!r}; supported: {describe_keys()}"
        )
    return key


def parse_chord(spec: str | Iterable[str]) -> tuple[str, ...]:
    """Parse ``"alt+shift"`` (or a list of names) into canonical names."""
    if isinstance(spec, str):
        raw = [part for part in spec.replace("-", "+").split("+")]
    else:
        raw = list(spec)
    keys = tuple(canonical_name(part) for part in raw if str(part).strip())
    if not keys:
        raise KeybindError("a bind needs at least one key (e.g. 'alt+shift')")
    if len(set(keys)) != len(keys):
        raise KeybindError(f"duplicate key in bind {'+'.join(keys)!r}")
    return keys


def format_chord(keys: Iterable[str]) -> str:
    """Render canonical names back to the user-facing spelling."""
    return "+".join(keys)


def parse_bind(value) -> Bind:
    """Parse one entry: a chord string, a list of names, or a ``{keys, action}`` mapping.

    Also accepts anything carrying ``keys``/``action`` attributes, not just
    :class:`Bind`, because this module can be imported under two names
    (``voice_transcriber/main.py`` prepends its own directory to ``sys.path``,
    so a later bare ``import keybinds`` loads the same file a second time).
    Class identity is therefore never load-bearing here.
    """
    action = DEFAULT_ACTION
    if isinstance(value, dict):
        if "keys" not in value:
            raise KeybindError("a bind needs a 'keys' entry")
        raw_keys = value["keys"]
        action = value.get("action", DEFAULT_ACTION)
    elif isinstance(value, (str, list, tuple)):
        raw_keys = value
    elif hasattr(value, "keys"):
        raw_keys = value.keys
        action = getattr(value, "action", DEFAULT_ACTION)
    else:
        raise KeybindError(f"cannot read a bind from {value!r}")
    keys = parse_chord(raw_keys)
    action = str(action).strip().lower()
    if action not in ACTIONS:
        raise KeybindError(
            f"unknown action {action!r}; supported: {', '.join(ACTIONS)}"
        )
    return Bind(keys=keys, action=action)


def parse_binds(value) -> list[Bind]:
    """Parse the whole ``hotkeys`` config value.

    A plain list is a list of *entries*, so ``["ctrl+shift", "f13"]`` is two
    binds while ``[{"keys": ["ctrl", "shift"]}]`` and ``[["ctrl", "shift"]]``
    are one. ``None`` or an empty value means "the default chord", so a fresh
    config and an older config both end up with ``alt+shift``. Raises
    :class:`KeybindError` for the first unusable entry; callers decide whether
    that is fatal (the control API) or worth logging (config load).
    """
    if value is None:
        return list(DEFAULT_BINDS)
    if isinstance(value, (str, dict)) or (
        not isinstance(value, (list, tuple)) and hasattr(value, "keys")
    ):
        return [parse_bind(value)]
    binds = [parse_bind(entry) for entry in value]
    return binds or list(DEFAULT_BINDS)


def describe_binds(binds: Sequence[Bind]) -> str:
    """Human-readable one-liner, e.g. ``alt+shift`` or ``alt+shift, f13``."""
    return ", ".join(bind.chord for bind in binds) or "none"


# ---------------------------------------------------------------------------
# Platform lookup tables
# ---------------------------------------------------------------------------
# ``EVDEV_CODES`` and ``VK_CODES`` above are derived from :data:`KEYS`; there is
# deliberately no second copy of the codes down here.


def evdev_codes(bind: Bind) -> tuple[int, ...]:
    """Every evdev code that could take part in ``bind`` (flat *union*).

    Useful for fetching the candidate keys from a device; the decision must be
    made per name with :data:`EVDEV_CODES`, since flattening loses which code
    belongs to which key.
    """
    return tuple(code for name in bind.keys for code in EVDEV_CODES[name])


def vk_codes(bind: Bind) -> tuple[int, ...]:
    """Every Windows virtual-key code that could take part in ``bind``.

    Flat *union*, like :func:`evdev_codes`. For matching, use
    :func:`vk_groups` instead: flattened, a bare ``alt`` would appear to require
    both sides to be down.
    """
    return tuple(code for name in bind.keys for code in VK_CODES[name])


def vk_groups(bind: Bind) -> tuple[tuple[int, ...], ...]:
    """Windows VK codes grouped per key: any code in a group satisfies that key.

    A bind is satisfied when **every** group has at least one code down. The WSL
    host bridge polls these with ``GetAsyncKeyState``, and the wire format built
    from this is ``group,group|group`` -- ``,`` separates alternatives inside one
    key, ``;`` separates keys inside one bind, ``|`` separates binds. So
    ``[alt+shift, f13]`` encodes as ``164,165;160,161|124``.
    """
    return tuple(VK_CODES[name] for name in bind.keys)


def encode_vk_binds(binds: Sequence[Bind]) -> str:
    """Encode ``binds`` for the WSL bridge's ``-Binds`` flag (see :func:`vk_groups`)."""
    return "|".join(
        ";".join(",".join(str(code) for code in group) for group in vk_groups(bind))
        for bind in binds
    )


#: ``-Binds`` value an old caller (or a missing flag) falls back to: Alt+Shift.
DEFAULT_VK_BINDS = encode_vk_binds(DEFAULT_BINDS)



def canonical_pynput_name(key, Key=None) -> str | None:
    """Canonical name for a pressed ``pynput`` key, or ``None`` if unbindable.

    Resolving to a *name* (rather than comparing pynput objects) is what lets the
    pynput backends reuse :func:`bind_hit` instead of each growing its own
    chord-matching code, and keeps the platform tables in one place.
    """
    if Key is not None:
        for attr, name in PYNPUT_ATTR_TO_NAME.items():
            if hasattr(Key, attr) and key == getattr(Key, attr):
                return name
    char = getattr(key, "char", None)
    if isinstance(char, str) and len(char) == 1 and char.lower() in CANONICAL_NAMES:
        return char.lower()
    return None


def bind_hit(bind: Bind, held_names: Sequence[str]) -> bool:
    """Whether ``held_names`` (canonical) satisfy ``bind``.

    A bare modifier matches either side; a named side matches only itself.
    Backends that can resolve keys to canonical names (``pynput``) can call this
    directly; the evdev and VK backends compare codes instead.
    """
    held = set(held_names)
    for name in bind.keys:
        if name in held:
            continue
        if name in ("ctrl", "alt", "shift", "meta"):
            if not any(h in (f"left{name}", f"right{name}") for h in held):
                return False
        else:
            return False
    return True
