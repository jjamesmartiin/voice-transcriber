#!/usr/bin/env python3
"""
Unit tests for scripts/write_release_requirements.py.

The file is what a mirror or an offline install reads to know which bundles an
app release needs, so the two ways it can rot are pinned here: it must name every
registered bundle (a missing one is an invisible gap), and it must read the
pins out of the registries rather than restate them (a restated revision is what
silently points users at the wrong weights).
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from voice_transcriber import llama_server_bundle, model_download

SCRIPT = REPO_ROOT / "scripts" / "write_release_requirements.py"


def test_it_names_every_bundle_and_the_pins_that_identify_them(tmp_path):
    out = tmp_path / "REQUIREMENTS.txt"
    subprocess.run(
        [sys.executable, str(SCRIPT), "--tag", "v9.9.9", "--out", str(out)],
        check=True, capture_output=True, text=True,
    )
    body = out.read_text(encoding="utf-8")

    assert "v9.9.9" in body
    for spec in model_download.MODELS.values():
        assert spec.bundle_tag in body
        assert spec.asset_prefix + ".SHA256SUMS" in body
        assert spec.revision in body
        for name, digest in spec.digests.items():
            assert f"{name}  {digest}" in body
    assert llama_server_bundle.BUNDLE_TAG in body
    assert llama_server_bundle.ASSET_NAME in body
    assert llama_server_bundle.ASSET_SHA256 in body


def test_it_reads_the_pins_instead_of_restating_them():
    """A pasted revision here would be a second home for a fact with one home.

    Checked at source level, like the sibling pins for the publisher and the
    packager, so the model-free shared tier never has to import torch.
    """
    text = SCRIPT.read_text(encoding="utf-8")
    assert "from voice_transcriber" in text
    assert model_download.REVISION not in text
    assert llama_server_bundle.ASSET_SHA256 not in text


def test_it_is_offline_and_needs_no_engine(tmp_path):
    """CI generates this on a runner with no model cache and no app instance."""
    out = tmp_path / "REQUIREMENTS.txt"
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--out", str(out)],
        check=True, capture_output=True, text=True,
    )
    assert "unreleased working tree" in out.read_text(encoding="utf-8")
    assert "REQUIREMENTS.txt" in result.stdout
