#!/usr/bin/env python3
"""The devshell's torch/onnxruntime decision has one owner (X2).

E3 (the int8 ONNX path for Cohere Transcribe) and D3 (diarization) both run on
``sherpa-onnx``, so any change to that dependency is a decision that lands in
both workstreams at once. The current compromise is deliberately narrow: a
second nixpkgs input supplies **only** ``sherpa-onnx`` >= 1.13, so ``torch`` and
the rest of the locked package set do not move with it.

Observed on the built devshell, 2026-10-09 (this is a correction of the session
note that said onnxruntime "did not move"):

* ``torch`` resolves to **2.10.0**, the version the locked nixpkgs provides. The
  second input would provide 2.12.0, so this records that it was not adopted.
* ``sherpa_onnx`` exposes ``OfflineCohereTranscribeModelConfig`` and
  ``OfflineRecognizer.from_cohere_transcribe`` - the E3 prerequisite.
* ``sherpa_onnx`` links its own ``libonnxruntime`` **1.26.0**, from the second
  input, not the locked nixpkgs' 1.24.4. The override therefore *does* move
  onnxruntime, transitively, for **both** workstreams. That is exactly the
  one-owner property this file records: there is a single ``sherpa-onnx``, hence
  a single onnxruntime, so E3 and D3 cannot independently choose one.

The static half runs everywhere (it only reads ``flake.nix`` / ``flake.lock``).
The runtime half only runs inside a Nix devshell - ``sys.executable`` under
``/nix/store`` - and skips elsewhere, so the Windows/macOS CI tiers stay
hermetic and do not need torch installed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAKE_NIX = REPO_ROOT / "flake.nix"
FLAKE_LOCK = REPO_ROOT / "flake.lock"

#: The torch version the locked nixpkgs resolves. A deliberate dependency move
#: updates this constant, which is the point: the move becomes a decision rather
#: than an accident.
PINNED_TORCH = "2.10.0"

#: The revision of the second input that supplies sherpa-onnx >= 1.13.
SHERPA_REV = "a9630bf480bf699d6792772fd0a5ee1b9084825b"

#: The onnxruntime the single sherpa-onnx is built against (a snapshot; see the
#: module docstring for why recording it is the X2 invariant).
SHERPA_ONNXRUNTIME = "1.26.0"


def _in_nix_devshell() -> bool:
    return sys.executable.startswith("/nix/store/")


def test_the_sherpa_input_is_pinned_to_one_rev():
    assert SHERPA_REV in FLAKE_NIX.read_text(encoding="utf-8"), (
        "flake.nix must pin nixpkgs-sherpa to the reviewed revision"
    )
    assert SHERPA_REV in FLAKE_LOCK.read_text(encoding="utf-8")


def test_the_second_input_only_supplies_sherpa_onnx():
    """A second input that could supply torch/onnxruntime would split the owner."""
    text = FLAKE_NIX.read_text(encoding="utf-8")
    # The override wraps the locked environment's python...
    assert "python.pkgs.sherpa-onnx.override" in text
    # ...and reaches into the second set only for sherpa-onnx and its bindings.
    assert "sherpaPkgs.sherpa-onnx.override" in text
    for forbidden in ("sherpaPkgs.torch", "sherpaPkgs.onnxruntime"):
        assert forbidden not in text, (
            f"{forbidden} would let the override drag a second workstream"
        )
    # `sherpaPkgs.python3` (the interpreter itself), but not `sherpaPkgs.python313Packages`.
    assert re.search(r"sherpaPkgs\.python3(?![\dA-Za-z_])", text) is None


@pytest.mark.skipif(
    not _in_nix_devshell(), reason="the torch pin is only observable in the devshell"
)
def test_devshell_torch_is_the_locked_version():
    import torch

    assert torch.__version__ == PINNED_TORCH, (
        "torch moved in the devshell; that is a decision for the whole "
        "torch/onnxruntime workstream (X2), not a side effect"
    )


@pytest.mark.skipif(
    not _in_nix_devshell(), reason="sherpa-onnx is only installed in the devshell"
)
def test_devshell_sherpa_onnx_exposes_the_cohere_api():
    sherpa_onnx = pytest.importorskip("sherpa_onnx")
    assert hasattr(sherpa_onnx, "OfflineCohereTranscribeModelConfig")
    assert hasattr(sherpa_onnx.OfflineRecognizer, "from_cohere_transcribe")


@pytest.mark.skipif(
    not (_in_nix_devshell() and sys.platform.startswith("linux")),
    reason="the linked onnxruntime is only visible on a Linux devshell",
)
def test_devshell_onnxruntime_has_one_source():
    """The single sherpa-onnx is built against the second input's onnxruntime."""
    pytest.importorskip("sherpa_onnx")
    maps = Path("/proc/self/maps").read_text(encoding="utf-8", errors="replace")
    versions = sorted(set(re.findall(r"-onnxruntime-([0-9.]+)/", maps)))
    assert versions == [SHERPA_ONNXRUNTIME], (
        f"the sherpa-onnx onnxruntime changed: {versions}. Both E3 and D3 read "
        "this one library; a move must be a deliberate, shared decision (X2)"
    )
