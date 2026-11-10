#!/usr/bin/env python3
"""The nixpkgs expression must list the same runtime dependencies as pyproject.

`packaging/nix/package.nix` is a second, upstream-facing build of the same app.
Nothing in `flake.nix`, CI or the test suite builds it, so it drifted: it listed
no `sherpa-onnx`, which means anyone installing the nixpkgs package gets meeting
mode failing at import (the diarizer imports it). This pins the two lists
together so the next dependency cannot land in one and miss the other.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NIX = REPO_ROOT / "packaging" / "nix" / "package.nix"
PYPROJECT = REPO_ROOT / "pyproject.toml"

#: Declared per platform in pyproject; the nixpkgs expression lists the Linux set.
NON_NIXPKGS_NAMES = {"pyperclip", "pynput", "keyboard", "evdev", "python-uinput"}


def _pyproject_dependencies() -> set[str]:
    import tomllib

    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = set()
    for dep in data["project"]["dependencies"]:
        name = re.split(r"[<>=!; \[\]]", dep, maxsplit=1)[0].strip().lower()
        if name:
            names.add(name.replace("_", "-"))
    return names


def _nix_dependencies() -> set[str]:
    text = PACKAGE_NIX.read_text(encoding="utf-8")
    block = text.split("dependencies = with python3Packages; [", 1)[1].split("];", 1)[0]
    names = set()
    for line in block.splitlines():
        line = line.split("#")[0].strip()
        if line and not line.startswith("]"):
            names.add(line.replace("_", "-").lower())
    return names


@pytest.mark.skipif(not PACKAGE_NIX.exists(), reason="expression removed")
def test_the_nixpkgs_expression_lists_every_runtime_dependency():
    declared = _pyproject_dependencies()
    packaged = _nix_dependencies()

    missing = sorted(declared - packaged - NON_NIXPKGS_NAMES)

    assert not missing, (
        f"packaging/nix/package.nix is missing {missing}. Add them to its "
        "`dependencies` list (or to NON_NIXPKGS_NAMES in this test if they are "
        "genuinely not needed on NixOS)."
    )
