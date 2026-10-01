"""The app version must agree everywhere it is displayed or named.

Both frontends render the same banner (``voice transcriber v… active``) and the
flake names the build, so a bump that touches only one of them ships a release
that reports the wrong version. That actually happened: ``tui-rs/src/main.rs``
hardcoded ``0.1.0`` (its crate version) so the **default** ratatui frontend told
users they were on 0.1.0 while the Rich frontend said 1.1.1.

This pins the three surfaces together. It is intentionally a source-level check:
the Rust const is compiled into the binary, and Python's is a constructor
default, so there is no runtime path that would otherwise catch the drift.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PY_TUI = REPO_ROOT / "src" / "voice_transcriber" / "tui.py"
RUST_MAIN = REPO_ROOT / "tui-rs" / "src" / "main.rs"
FLAKE = REPO_ROOT / "flake.nix"

#: The frontend crate keeps its own version and must NOT be confused with the
#: app version (it is declared in flake.nix next to `pname = "vt-tui"`).
CRATE_VERSION = "0.1.0"


def _read(path: Path) -> str:
    assert path.exists(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def _first(pattern: str, path: Path) -> str:
    match = re.search(pattern, _read(path))
    assert match, f"could not find {pattern!r} in {path}"
    return match.group(1)


def _python_version() -> str:
    return _first(r'app_version="([^"]+)"', PY_TUI)


def _rust_version() -> str:
    return _first(r'const VERSION: &str = "([^"]+)"', RUST_MAIN)


def test_frontends_report_the_same_version():
    python_version = _python_version()
    rust_version = _rust_version()
    assert python_version == rust_version, (
        f"the Rich frontend reports v{python_version} but the ratatui frontend "
        f"reports v{rust_version}; both render the same banner, so they must match"
    )


def test_flake_ships_the_frontend_version():
    """The `vt` derivation and the flake's version binding both carry it."""
    app_version = _python_version()
    flake = _read(FLAKE)
    occurrences = flake.count(f'version = "{app_version}";')
    assert occurrences == 2, (
        f"expected exactly 2 `version = \"{app_version}\";` in flake.nix "
        f"(the `let` binding and the `vt` derivation), found {occurrences}"
    )


def test_crate_version_is_kept_separate():
    """Guard the check above from silently absorbing the vt-tui crate version."""
    flake = _read(FLAKE)
    assert f'version = "{CRATE_VERSION}";' in flake, "vt-tui crate version moved"
    assert _python_version() != CRATE_VERSION, (
        "the app version and the frontend crate version must not be conflated"
    )


def test_version_looks_like_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", _python_version()), _python_version()


@pytest.mark.parametrize("surface", ["python", "rust"])
def test_version_is_not_a_placeholder(surface):
    version = _python_version() if surface == "python" else _rust_version()
    assert version not in {"0.0.0", "0.1.0"}, f"{surface} still has a dev version"
