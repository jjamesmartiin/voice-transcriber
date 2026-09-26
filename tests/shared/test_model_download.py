#!/usr/bin/env python3
"""
Unit tests for model_download module:
- Local model detection
- SHA256SUMS manifest parsing
- HTTP 404 fast-failure and candidate fallback routing
- Provenance writing
"""
import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import model_download


def test_is_local_model_complete(tmp_path):
    assert model_download.is_local_model_complete(str(tmp_path)) is False

    for req in model_download._REQUIRED_LOCAL:
        (tmp_path / req).write_text("stub", encoding="utf-8")

    assert model_download.is_local_model_complete(str(tmp_path)) is True


def test_find_repo_root():
    root = model_download.find_repo_root()
    assert root is not None
    assert os.path.isdir(os.path.join(root, ".git"))


def test_write_provenance(tmp_path):
    model_download._write_provenance(
        str(tmp_path),
        base_url="https://github.com/jjamesmartiin/voice-transcriber/releases/download/v1.1.0",
        resolved_url="https://github.com/jjamesmartiin/voice-transcriber/releases/download/v1.1.0/cohere.tar",
    )
    source_file = tmp_path / "SOURCE.json"
    assert source_file.exists()
    data = json.loads(source_file.read_text(encoding="utf-8"))
    assert data["model"] == model_download.REPO_ID
    assert data["release_tag"] == "v1.1.0"
    assert data["model_safetensors_sha256"] == model_download.SAFETENSORS_SHA256


def test_fetch_part_manifest_parses_valid_checksums(monkeypatch):
    sample_manifest = (
        "987bd3e141c7bfdb5a78f5db11397ee7737308357e6cc0a3f36a4979b158137a  cohere-transcribe-rev1.part1.xz\n"
        "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef  cohere-transcribe-rev1.part2.xz\n"
        "otherdigest1234567890abcdef1234567890abcdef1234567890abcdef12345  unrelated.txt\n"
    )

    class FakeResponse:
        def __init__(self, data, url):
            self._data = data.encode("utf-8")
            self._url = url

        def read(self):
            return self._data

        def geturl(self):
            return self._url

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=120):
        return FakeResponse(sample_manifest, req.full_url)

    monkeypatch.setattr(model_download.urllib.request, "urlopen", fake_urlopen)

    manifest, resolved_url = model_download._fetch_part_manifest(
        "https://example.com/releases/download/v1.1.0",
        prefix="cohere-transcribe-rev1",
    )
    assert len(manifest) == 2
    assert "cohere-transcribe-rev1.part1.xz" in manifest
    assert "cohere-transcribe-rev1.part2.xz" in manifest
    assert "unrelated.txt" not in manifest
    assert manifest["cohere-transcribe-rev1.part1.xz"] == "987bd3e141c7bfdb5a78f5db11397ee7737308357e6cc0a3f36a4979b158137a"


def test_fetch_part_manifest_fast_fails_on_404(monkeypatch):
    call_count = 0

    def fake_urlopen_404(req, timeout=120):
        nonlocal call_count
        call_count += 1
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=404,
            msg="Not Found",
            hdrs={},
            fp=io.BytesIO(b""),
        )

    monkeypatch.setattr(model_download.urllib.request, "urlopen", fake_urlopen_404)

    with pytest.raises(RuntimeError) as exc_info:
        model_download._fetch_part_manifest("https://example.com/download", prefix="cohere-test")

    # Verify it broke immediately on 404 rather than retrying 3 times
    assert call_count == 1
    assert "404" in str(exc_info.value)


def test_ensure_local_cohere_fallback_candidate_chain(monkeypatch, tmp_path):
    attempts = []

    def fake_fetch(cand, prefix):
        attempts.append(cand)
        if "latest" in cand:
            # First candidate simulates missing manifest (HTTP 404)
            raise RuntimeError("404 Not Found")
        # Second candidate (v1.1.0) succeeds with manifest
        return ({"cohere-transcribe-rev.part1.xz": "digest1"}, f"{cand}/resolved")

    monkeypatch.setattr(model_download, "_fetch_part_manifest", fake_fetch)
    # Mock download execution so it doesn't try real network download
    monkeypatch.setattr(model_download, "_http_get", lambda url, dest, desc: 100)
    monkeypatch.setattr(model_download, "_sha256_file", lambda p: "digest1")

    # With empty local dir
    assert model_download.is_local_model_complete(str(tmp_path)) is False

    # Force candidates to check primary and fallback
    monkeypatch.setattr(model_download, "DEFAULT_RELEASE_BASE", "https://example.com/latest")
    monkeypatch.setattr(model_download, "FALLBACK_RELEASE_BASE", "https://example.com/v1.1.0")

    # Run ensure_local_cohere up to fetch manifest phase
    # (it will attempt cand 1, fail, then attempt cand 2)
    try:
        model_download.ensure_local_cohere(dest=str(tmp_path), base_url="https://example.com/latest")
    except Exception:
        pass

    assert "https://example.com/latest" in attempts
    assert "https://example.com/v1.1.0" in attempts
