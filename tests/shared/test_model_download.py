#!/usr/bin/env python3
"""
Unit tests for model_download module:
- Local model detection
- SHA256SUMS manifest parsing
- HTTP 404 fast-failure and candidate fallback routing
- Provenance writing
"""
import hashlib
import io
import json
import lzma
import os
import sys
import tarfile
import urllib.error
import zipfile
from pathlib import Path

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
    broken = "https://example.com/broken"
    bundle = "https://example.com/bundle"

    def fake_fetch(cand, prefix):
        attempts.append(cand)
        if cand.startswith(broken):
            # First candidate simulates a missing manifest (HTTP 404)
            raise RuntimeError("404 Not Found")
        # The canonical bundle succeeds with the manifest.
        return ({"cohere-transcribe-rev.part1.xz": "digest1"}, f"{cand}/resolved")

    monkeypatch.setattr(model_download, "_fetch_part_manifest", fake_fetch)
    # Mock download execution so it doesn't try real network download
    monkeypatch.setattr(model_download, "_http_get", lambda url, dest, desc: 100)
    monkeypatch.setattr(model_download, "_sha256_file", lambda p: "digest1")
    # Presence of the parts is confirmed by HEAD; stub it so the test stays offline.
    monkeypatch.setattr(model_download, "_asset_exists", lambda url: True)

    # With empty local dir
    assert model_download.is_local_model_complete(str(tmp_path)) is False

    # An explicit base is tried first; the canonical bundle is the fallback.
    monkeypatch.setattr(model_download, "DEFAULT_RELEASE_BASE", bundle)

    # Run ensure_local_cohere up to fetch manifest phase
    # (it will attempt cand 1, fail, then attempt cand 2)
    try:
        model_download.ensure_local_cohere(dest=str(tmp_path), base_url=broken)
    except Exception:
        pass

    assert broken in attempts
    assert bundle in attempts


def test_model_bundle_tag_is_revision_derived():
    """Weights are keyed by *model revision*, not app version.

    That is what makes a new app release upload nothing: the tag only changes
    when REVISION does.
    """
    assert model_download.MODEL_BUNDLE_TAG == (
        f"model-cohere-{model_download.REVISION[:12]}")
    assert model_download.DEFAULT_RELEASE_BASE == (
        "https://github.com/jjamesmartiin/voice-transcriber"
        f"/releases/download/{model_download.MODEL_BUNDLE_TAG}")


def test_candidates_never_consult_the_app_latest_release():
    """`/releases/latest` is the AppImage's tag, not a weights pointer.

    Resolving weights through it is what allowed a half-published bundle to
    shadow a complete one.
    """
    cands = model_download._release_candidates(model_download.DEFAULT_RELEASE_BASE)
    assert not any("/releases/latest" in c for c in cands)
    assert cands == [model_download.DEFAULT_RELEASE_BASE]


def test_candidates_dedupe_an_explicit_override():
    """An explicit override wins, but the canonical bundle is still a fallback
    and no URL is repeated."""
    override = "https://example.com/custom"
    cands = model_download._release_candidates(override)
    assert cands[0] == override
    assert cands[1] == model_download.DEFAULT_RELEASE_BASE.rstrip("/")
    assert len(cands) == len(set(cands)) == 2


def test_model_revision_lives_only_in_model_download():
    """The model revision is declared exactly once and imported everywhere.

    Drift here is silent and nasty: the installer would fetch release assets for
    one revision while the loader asked the backend for another. Checked at the
    source level so the model-free shared tier never has to import
    torch/transformers to assert it.
    """
    rev = model_download.REVISION
    sha = model_download.SAFETENSORS_SHA256
    for rel in ("src/voice_transcriber/transcribe_cohere.py", "scripts/prepare_model_release.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert rev not in text, (
            f"{rel} duplicates REVISION — import it from model_download instead")
        assert sha not in text, (
            f"{rel} duplicates SAFETENSORS_SHA256 — import it instead")
    own = (REPO_ROOT / "src" / "voice_transcriber" / "model_download.py").read_text(encoding="utf-8")
    assert rev in own and sha in own, (
        "src/voice_transcriber/model_download.py is the declared home of the revision")


def test_publish_script_reads_constants_from_this_module():
    """The publisher must not hardcode the bundle tag.

    It resolves REPO_SLUG / MODEL_BUNDLE_TAG / REVISION by importing this
    module, so the two can never disagree about where the weights live.
    """
    script = REPO_ROOT / "scripts" / "publish_model_bundle.sh"
    assert script.exists(), "publisher script is missing"
    assert os.access(script, os.X_OK), "publisher script is not executable"
    text = script.read_text(encoding="utf-8")
    # Prose may name the scheme (`model-cohere-<rev12>`), but the *resolved*
    # values must come from the import, never be pasted in.
    assert model_download.MODEL_BUNDLE_TAG not in text, (
        "publisher hardcodes the resolved bundle tag instead of importing it")
    assert model_download.REVISION not in text, (
        "publisher hardcodes REVISION instead of importing it")
    assert "import model_download" in text


def test_manifest_listing_missing_parts_is_skipped(monkeypatch, tmp_path):
    """Regression: a manifest whose parts are absent must never be chosen.

    A manifest can name parts that are absent (an aborted or hand-managed
    publish), and a mid-upload bundle used to win the candidate race and
    hard-fail the install instead of letting a complete candidate serve.
    """
    broken = "https://example.com/releases/download/model-cohere-broken"
    good = "https://example.com/releases/download/model-cohere-good"

    def fake_fetch(cand, prefix):
        return ({"cohere-transcribe-rev.part1.xz": "d1",
                 "cohere-transcribe-rev.part2.xz": "d2"}, f"{cand}/SUMS")

    downloaded = []
    monkeypatch.setattr(model_download, "_fetch_part_manifest", fake_fetch)
    monkeypatch.setattr(model_download, "DEFAULT_RELEASE_BASE", good)
    # `broken` publishes part1 but not part2.
    monkeypatch.setattr(
        model_download, "_asset_exists",
        lambda url: not (url.startswith(broken) and url.endswith("part2.xz")))
    monkeypatch.setattr(model_download, "_http_get",
                        lambda url, dest, desc: downloaded.append(url) or 100)
    monkeypatch.setattr(model_download, "_sha256_file", lambda p: "d1")

    try:
        model_download.ensure_local_cohere(dest=str(tmp_path), base_url=broken)
    except Exception:
        pass  # the fake parts never decompress; the candidate choice is the point

    assert downloaded, "expected the complete candidate to be used"
    assert all(u.startswith(good) for u in downloaded), downloaded


def test_local_model_source_prefers_the_appimage_dir(monkeypatch, tmp_path):
    """A double-clicked AppImage must find model-bundle/ next to the .AppImage.

    Inside an AppImage ``sys.executable`` is the bundled interpreter, not the
    user-visible file, so the old two candidates missed the USB-stick layout.
    """
    app_dir = tmp_path / "app"
    run_dir = tmp_path / "run"
    app_dir.mkdir()
    run_dir.mkdir()
    appimage = app_dir / "vt-x86_64.AppImage"
    appimage.write_bytes(b"")
    bundle = app_dir / "model-bundle"
    bundle.mkdir()

    monkeypatch.chdir(run_dir)
    monkeypatch.delenv("VT_MODEL_SOURCE_DIR", raising=False)
    monkeypatch.delenv("VT_MODEL_BUNDLE", raising=False)
    monkeypatch.setenv("APPIMAGE", str(appimage))

    assert model_download._local_model_source() == (str(bundle), False)


# ---------------------------------------------------------------------------
# Offline / airgapped installation from a local split bundle
# ---------------------------------------------------------------------------
def _make_local_bundle(tmp_path, revision="testrev", n_parts=2):
    """Build a minimal split-xz bundle the installer can assemble offline.

    Returns (bundle_dir, weights_bytes). The payload tar holds every file
    ``is_local_model_complete`` requires at its root, split into ``n_parts``
    contiguous byte ranges and xz-compressed independently - exactly the layout
    ``scripts/prepare_model_release.py`` publishes.
    """
    prefix = f"cohere-transcribe-{revision}"
    payload = tmp_path / "payload"
    payload.mkdir()
    files = {name: f"stub-{name}".encode() for name in model_download._REQUIRED_LOCAL}
    weights = b"fake-weights-" + b"x" * 128
    files["model.safetensors"] = weights
    for name, data in files.items():
        (payload / name).write_bytes(data)

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tf:
        for name in files:
            tf.add(payload / name, arcname=name)
    raw = tar_buf.getvalue()

    size = (len(raw) + n_parts - 1) // n_parts
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    sums = []
    for i in range(n_parts):
        name = f"{prefix}.part{i + 1}.xz"
        blob = lzma.compress(raw[i * size:(i + 1) * size])
        (bundle / name).write_bytes(blob)
        sums.append(f"{hashlib.sha256(blob).hexdigest()}  {name}")
    (bundle / f"{prefix}.SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return bundle, weights


def test_install_from_local_bundle_directory(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision="testrev")
    monkeypatch.setattr(model_download, "SAFETENSORS_SHA256",
                        hashlib.sha256(weights).hexdigest())
    dest = tmp_path / "dest"

    result = model_download.install_from_local_bundle(
        str(bundle), dest=str(dest), revision="testrev")

    assert result == str(dest)
    assert (dest / "model.safetensors").read_bytes() == weights
    for name in model_download._REQUIRED_LOCAL:
        assert (dest / name).exists(), name
    source = json.loads((dest / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["source_kind"] == "local"


def test_install_from_local_bundle_zip(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision="testrev")
    monkeypatch.setattr(model_download, "SAFETENSORS_SHA256",
                        hashlib.sha256(weights).hexdigest())
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in bundle.iterdir():
            zf.write(item, arcname=item.name)
    dest = tmp_path / "dest"

    result = model_download.install_from_local_bundle(
        str(archive), dest=str(dest), revision="testrev")

    assert result == str(dest)
    assert (dest / "model.safetensors").read_bytes() == weights


def test_install_from_local_bundle_rejects_tampered_part(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision="testrev")
    monkeypatch.setattr(model_download, "SAFETENSORS_SHA256",
                        hashlib.sha256(weights).hexdigest())
    target = sorted(bundle.glob("*.part1.xz"))[0]
    blob = bytearray(target.read_bytes())
    blob[-1] ^= 0xFF
    target.write_bytes(bytes(blob))

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        model_download.install_from_local_bundle(
            str(bundle), dest=str(tmp_path / "dest"), revision="testrev")


def test_ensure_local_cohere_installs_from_env_bundle_offline(monkeypatch, tmp_path):
    rev = model_download.REVISION
    bundle, weights = _make_local_bundle(tmp_path, revision=rev)
    monkeypatch.setattr(model_download, "SAFETENSORS_SHA256",
                        hashlib.sha256(weights).hexdigest())
    monkeypatch.setenv("VT_MODEL_SOURCE_DIR", str(bundle))
    monkeypatch.setenv("VT_AUTO_DOWNLOAD_MODEL", "1")

    def _network_forbidden(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("ensure_local_cohere hit the network despite a local bundle")

    monkeypatch.setattr(model_download.urllib.request, "urlopen", _network_forbidden)
    monkeypatch.setattr(model_download, "_asset_exists", _network_forbidden)

    dest = tmp_path / "dest"
    assert model_download.ensure_local_cohere(dest=str(dest)) == str(dest)
    assert (dest / "model.safetensors").exists()


def test_ensure_local_cohere_explicit_missing_bundle_never_downloads(monkeypatch, tmp_path):
    """An explicit VT_MODEL_SOURCE_DIR that is wrong must fail closed.

    Silently falling back to the network would defeat the point of airgap, and
    could leak that a disconnected host has connectivity.
    """
    monkeypatch.setenv("VT_MODEL_SOURCE_DIR", str(tmp_path / "nope"))

    def _network_forbidden(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("downloaded despite an explicit local bundle")

    monkeypatch.setattr(model_download.urllib.request, "urlopen", _network_forbidden)

    assert model_download.ensure_local_cohere(dest=str(tmp_path / "dest")) is None


def test_extract_bundle_archive_rejects_zip_slip(tmp_path):
    """Archives with '../' traversing outside dest must be rejected."""
    import zipfile
    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")

    dest = tmp_path / "extract_dest"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="Zip slip path traversal detected"):
        model_download._extract_bundle_archive(str(bad_zip), str(dest))
    assert not (tmp_path / "evil.txt").exists()
