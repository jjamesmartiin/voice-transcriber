"""What ``auto`` means, pinned once instead of per front end.

The chain in :mod:`accent` is the only thing that can silently disagree with
itself: the Rich TUI, the ratatui binary and the settings modals each used to
carry a copy of it, and two of those copies had already drifted (one honoured
``TERM_PROGRAM``, the other did not). The precedence and the desktop read are
asserted here, and the last test asserts the three front ends agree.

Every test clears the probe's cache and the three environment variables, so the
colour of the machine running the suite cannot decide the outcome.
"""
import subprocess

import pytest

import accent


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """No ambient override, and no cached probe crossing between tests."""
    for var in ("VT_UI_THEME", "ACCENT_COLOR", "TERM_PROGRAM"):
        monkeypatch.delenv(var, raising=False)
    accent.detect_desktop_accent.cache_clear()  # no probe result carried in
    yield


def _desktop_accent_is(monkeypatch, value):
    """Stub the one step that talks to the desktop."""
    monkeypatch.setattr(accent, "detect_desktop_accent", lambda: value)


# ---------------------------------------------------------------------------
# The mapping: a desktop accent has to land on a colour a front end can paint
# ---------------------------------------------------------------------------


def test_every_desktop_accent_maps_to_a_real_palette_colour():
    for desktop_name, theme in accent.ACCENT_TO_THEME.items():
        assert theme in accent.COLOR_PALETTES, (
            f"GNOME's {desktop_name!r} maps to {theme!r}, which no picker offers"
        )
        assert theme != "auto", f"{desktop_name!r} maps back to auto"


def test_the_whole_gnome_palette_is_covered():
    """GNOME 47's nine accents, verbatim from `gsettings range` output."""
    for desktop_name in ("blue", "teal", "green", "yellow", "orange",
                         "red", "pink", "purple", "slate"):
        assert desktop_name in accent.ACCENT_TO_THEME


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def test_auto_follows_the_desktop_accent(monkeypatch):
    _desktop_accent_is(monkeypatch, "red")
    assert accent.resolve("auto") == "red"
    assert accent.resolve(None) == "red"  # nothing configured is auto
    assert accent.resolve("") == "red"


def test_a_concrete_theme_ignores_the_chain(monkeypatch):
    """A theme the user picked is not second-guessed by the desktop."""
    _desktop_accent_is(monkeypatch, "blue")
    assert accent.resolve("red") == "red"
    assert accent.resolve("Cyan ") == "cyan"


def test_vt_ui_theme_wins_over_the_desktop_accent(monkeypatch):
    """The documented per-session override outranks detection."""
    _desktop_accent_is(monkeypatch, "red")
    monkeypatch.setenv("VT_UI_THEME", "cyan")
    assert accent.resolve("auto") == "cyan"


def test_an_explicit_accent_wins_over_the_desktop_accent(monkeypatch):
    _desktop_accent_is(monkeypatch, "red")
    monkeypatch.setenv("ACCENT_COLOR", "magenta")
    assert accent.resolve("auto") == "magenta"


def test_garbage_in_env_is_ignored_rather_than_painted(monkeypatch):
    """A typo'd VT_UI_THEME must not reach a renderer as a colour name."""
    monkeypatch.setenv("VT_UI_THEME", "chartreuse")
    _desktop_accent_is(monkeypatch, "green")
    assert accent.resolve("auto") == "green"


def test_the_terminal_heuristic_is_only_the_fallback(monkeypatch):
    """What auto did before the probe existed still works where it does not."""
    _desktop_accent_is(monkeypatch, None)
    monkeypatch.setenv("TERM_PROGRAM", "kitty")
    assert accent.resolve("auto") == "magenta"

    _desktop_accent_is(monkeypatch, "red")
    assert accent.resolve("auto") == "red", "the desktop outranks the terminal"


def test_nothing_detectable_falls_back_to_green(monkeypatch):
    """Nothing detectable anywhere: the long-standing fallback."""
    _desktop_accent_is(monkeypatch, None)
    assert accent.resolve("auto") == "green"


# ---------------------------------------------------------------------------
# The probe itself
# ---------------------------------------------------------------------------


def test_the_probe_reads_the_gnome_accent(monkeypatch):
    def fake_run(cmd, **kwargs):
        assert cmd == ["gsettings", "get", "org.gnome.desktop.interface",
                       "accent-color"]
        return subprocess.CompletedProcess(cmd, 0, stdout="'red'\n")

    monkeypatch.setattr(accent.subprocess, "run", fake_run)
    assert accent.detect_desktop_accent() == "red"


@pytest.mark.parametrize("failure", ["missing", "nonzero", "unknown-value"])
def test_a_probe_that_cannot_answer_returns_none(monkeypatch, failure):
    """Absent gsettings, an old GNOME, an unreadable bus: all just 'unknown'."""
    if failure == "missing":
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError("gsettings")
    elif failure == "nonzero":
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no key")
    else:
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="'chartreuse'\n")

    monkeypatch.setattr(accent.subprocess, "run", fake_run)

    assert accent.detect_desktop_accent() is None


def test_the_probe_does_not_spawn_gsettings_per_render(monkeypatch):
    """It is cached: the call is a process, and the UI resolves per frame."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="'red'\n")

    monkeypatch.setattr(accent.subprocess, "run", fake_run)
    for _ in range(5):
        accent.resolve("auto")

    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Parity: the three front ends must agree, which is the point of the module
# ---------------------------------------------------------------------------


def test_the_front_ends_resolve_auto_to_the_same_colour(monkeypatch):
    import t2
    import tui
    import tui_ratatui

    _desktop_accent_is(monkeypatch, "red")
    monkeypatch.setattr(t2, "UI_THEME", "auto")

    rich = tui.VoiceTranscriberTUI(ui_theme="auto")
    rat = tui_ratatui.RatatuiTui.__new__(tui_ratatui.RatatuiTui)
    rat.ui_theme = "auto"

    assert rich.get_effective_color() == "red"
    assert rat.get_effective_color() == "red"
    assert t2.effective_ui_theme() == "red"


def test_the_ratatui_child_is_spawned_with_the_resolved_colour(monkeypatch):
    """The binary cannot run the probe, so the engine hands it the answer.

    ``ACCENT_COLOR`` is step 2 of the chain the binary runs itself
    (``detect_system_theme``), so this is how ``auto`` reaches it.
    """
    import tui_ratatui

    _desktop_accent_is(monkeypatch, "red")
    rat = tui_ratatui.RatatuiTui.__new__(tui_ratatui.RatatuiTui)
    rat.ui_theme = "auto"

    assert rat._child_env()["ACCENT_COLOR"] == "red"


def test_the_settings_modals_paint_the_accent_not_green(monkeypatch):
    """The modals resolve through t2, or the panel would disagree with the bar."""
    import t2

    _desktop_accent_is(monkeypatch, "red")
    monkeypatch.setattr(t2, "UI_THEME", "auto")
    assert t2.effective_ui_theme() == "red"

    monkeypatch.setattr(t2, "UI_THEME", "cyan")
    assert t2.effective_ui_theme() == "cyan"
