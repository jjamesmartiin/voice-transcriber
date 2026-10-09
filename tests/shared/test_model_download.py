#!/usr/bin/env python3
"""
Unit tests for the model_download registry and installer:
- The ModelSpec registry (a second spec resolves; constants have one home)
- Per-model local completeness and install-target resolution
- SHA256SUMS manifest parsing and candidate fallback routing
- Format-agnostic, offline installation from a local split bundle
- Provenance writing

Hermeticity: tests/shared/conftest.py installs an autouse guard that makes any
real non-loopback socket connect raise immediately, so a leaked download can
never stream gigabytes from a CDN in a background thread.
"""
import dataclasses
import hashlib
import io
import json
import lzma
import os
import subprocess
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


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
def test_is_local_model_complete(tmp_path):
    assert model_download.is_local_model_complete(str(tmp_path)) is False

    for req in model_download._REQUIRED_LOCAL:
        (tmp_path / req).write_text("stub", encoding="utf-8")

    assert model_download.is_local_model_complete(str(tmp_path)) is True


def _synthetic_spec(name="synthetic", revision="testrev", files=None,
                    digests=None, target_subdir=None, **overrides):
    """A minimal non-Cohere spec: no model.safetensors, an arbitrary filename.

    Used to prove the installer and provenance writer are format-agnostic and
    that a second registry entry resolves through the same code path.
    """
    files = files if files is not None else {
        "config.json": b"{}",
        "weights.gguf": b"fake-gguf-weights-" + b"g" * 64,
    }
    if digests is None:
        digests = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    fields = dict(
        name=name,
        display_name=name.title(),
        description="synthetic model for tests",
        repo_id=f"example/{name}",
        revision=revision,
        asset_prefix=f"{name}-{revision}",
        bundle_tag=f"model-{name}-{revision[:12]}",
        release_base=f"https://example.invalid/releases/download/model-{name}-{revision[:12]}",
        package_files=tuple(files),
        required_local=tuple(files),
        digests=digests,
        license_file="",
        license_name="MIT",
        target_subdir=target_subdir or name,
        download_hint="tiny",
    )
    fields.update(overrides)
    return model_download.ModelSpec(**fields)


def test_registry_lists_cohere_and_resolves_a_second_spec(monkeypatch):
    assert model_download.get_spec("cohere") is model_download.COHERE

    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)

    assert model_download.get_spec("synthetic") is spec
    assert model_download.models_dir("synthetic").endswith(
        os.path.join("models", "synthetic"))


def test_models_dir_uses_the_target_subdir(monkeypatch, tmp_path):
    monkeypatch.delenv("VT_MODEL_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(model_download, "find_repo_root", lambda: None)
    spec = _synthetic_spec(target_subdir="diarization")
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)

    assert model_download.models_dir("synthetic") == os.path.join(
        str(tmp_path), "vt", "models", "diarization")


def test_get_spec_rejects_an_unknown_model():
    with pytest.raises(KeyError, match="unknown model 'nope'"):
        model_download.get_spec("nope")


def test_is_model_complete_is_per_spec(monkeypatch, tmp_path):
    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)

    # A Cohere-complete directory is not a synthetic-complete one, and vice
    # versa: completeness is driven by the spec's own required_local tuple.
    for req in model_download._REQUIRED_LOCAL:
        (tmp_path / req).write_text("stub", encoding="utf-8")

    assert model_download.is_model_complete("cohere", str(tmp_path)) is True
    assert model_download.is_model_complete("synthetic", str(tmp_path)) is False

    for req in spec.required_local:
        (tmp_path / req).write_text("stub", encoding="utf-8")
    assert model_download.is_model_complete("synthetic", str(tmp_path)) is True


def test_find_repo_root():
    """A real checkout is found from this file's own path."""
    root = model_download.find_repo_root()
    assert root is not None
    assert model_download._has_repo_marker(root)


def test_find_repo_root_accepts_a_worktree_marker(monkeypatch, tmp_path):
    """`git worktree add` leaves a `.git` *file*, not a directory.

    Regression: requiring a directory made ``find_repo_root()`` return None in a
    worktree, so the Cohere weights were installed outside the checkout and the
    worktree's own ``models/`` directory was ignored.
    """
    src = tmp_path / "checkout" / "src" / "voice_transcriber"
    src.mkdir(parents=True)
    (src / "model_download.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "checkout" / ".git").write_text(
        "gitdir: /elsewhere/.git/worktrees/checkout\n", encoding="utf-8"
    )
    monkeypatch.setattr(model_download, "__file__", str(src / "model_download.py"))

    assert model_download.find_repo_root() == str(tmp_path / "checkout")


def test_find_repo_root_is_none_without_a_marker(monkeypatch, tmp_path):
    """Read-only installs (Nix store, AppImage) have no marker: fall back."""
    src = tmp_path / "readonly_app" / "src" / "voice_transcriber"
    src.mkdir(parents=True)
    (src / "model_download.py").write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(model_download, "__file__", str(src / "model_download.py"))

    assert model_download.find_repo_root() is None


def test_write_provenance(tmp_path):
    model_download._write_provenance(
        model_download.COHERE,
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
    # A single-safetensors spec keeps the Cohere-era key set exactly.
    assert "digests" not in data


def test_write_provenance_records_a_digest_map_for_non_safetensors(tmp_path):
    """A GGUF/ONNX spec records its whole digest map, not a fake safetensors key."""
    spec = _synthetic_spec()
    model_download._write_provenance(spec, str(tmp_path), base_url="https://example.invalid")
    data = json.loads((tmp_path / "SOURCE.json").read_text(encoding="utf-8"))
    assert data["model"] == spec.repo_id
    assert data["digests"] == dict(spec.digests)
    assert "model_safetensors_sha256" not in data


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

    # An explicit base is tried first; the spec's canonical bundle is the fallback.
    monkeypatch.setitem(model_download.MODELS, "cohere",
                        dataclasses.replace(model_download.COHERE, release_base=bundle))

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
    when the revision does.
    """
    assert model_download.MODEL_BUNDLE_TAG == (
        f"model-cohere-{model_download.REVISION[:12]}")
    assert model_download.DEFAULT_RELEASE_BASE == (
        "https://github.com/jjamesmartiin/voice-transcriber"
        f"/releases/download/{model_download.MODEL_BUNDLE_TAG}")
    spec = model_download.get_spec("cohere")
    assert spec.bundle_tag == model_download.MODEL_BUNDLE_TAG
    assert spec.release_base == model_download.DEFAULT_RELEASE_BASE
    assert spec.asset_prefix == f"cohere-transcribe-{model_download.REVISION}"


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


def test_candidates_use_the_specs_own_bundle_as_the_fallback():
    """A non-Cohere spec falls back to *its* release base, not Cohere's."""
    spec = _synthetic_spec()
    cands = model_download._release_candidates("https://example.com/custom",
                                               spec.release_base)
    assert cands == ["https://example.com/custom", spec.release_base.rstrip("/")]


def test_model_constants_live_only_in_model_download():
    """Every spec's constants are declared exactly once and imported everywhere.

    Drift here is silent and nasty: the installer would fetch release assets for
    one revision while the loader asked the backend for another. Checked at the
    source level so the model-free shared tier never has to import
    torch/transformers to assert it.
    """
    own = (REPO_ROOT / "src" / "voice_transcriber" / "model_download.py").read_text(encoding="utf-8")
    consumers = (
        "src/voice_transcriber/transcribe_cohere.py",
        "scripts/prepare_model_release.py",
    )
    for spec in model_download.MODELS.values():
        assert spec.revision in own, (
            "model_download.py is the declared home of every revision")
        for digest in spec.digests.values():
            assert digest in own, (
                "model_download.py is the declared home of every digest")
        for rel in consumers:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert spec.revision not in text, (
                f"{rel} duplicates a revision — import the spec instead")
            for digest in spec.digests.values():
                assert digest not in text, (
                    f"{rel} duplicates a digest — import the spec instead")


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
    # It selects a registry entry by name, so a second artifact can publish.
    assert "get_spec(" in text


def test_packager_selects_a_registry_model():
    """The packager takes a model name and reads every constant from the spec."""
    script = REPO_ROOT / "scripts" / "prepare_model_release.py"
    text = script.read_text(encoding="utf-8")
    assert "from model_download import get_spec" in text
    assert "--model" in text
    assert "spec.asset_prefix" in text
    assert model_download.REVISION not in text


def _load_packager():
    """Import scripts/prepare_model_release.py without it being a package."""
    import importlib.util
    path = REPO_ROOT / "scripts" / "prepare_model_release.py"
    module_spec = importlib.util.spec_from_file_location("_vt_packager", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def test_cohere_notice_is_pinned_to_the_published_wording():
    """The NOTICE is a legal artifact and must never drift.

    It ships inside the release archive, so a change is a change to a
    published artifact. The refactor that introduced ``display_name``
    substituted the *console* labels into it and silently did two things: it
    dropped "2B" from the formal model name, and it replaced "the Apache
    License, Version 2.0" with the SPDX id "Apache-2.0". Neither is a runtime
    behaviour change, so no other test could catch it - hence pinning the
    exact bytes here.
    """
    spec = model_download.get_spec("cohere")
    expected = (
        "Cohere Transcribe 2B (cohere-transcribe-03-2026)\n"
        "Automatic Speech Recognition model by Cohere / Cohere Labs.\n"
        f"Revision: {spec.revision}\n"
        f"Source: https://huggingface.co/{spec.repo_id}\n"
        "\n"
        "This model is licensed under the Apache License, Version 2.0 "
        "(see the LICENSE file in this directory).\n"
        "Weights mirrored for distribution via the voice-transcriber GitHub release."
    )
    assert _load_packager().build_notice(spec) == expected


def test_notice_formal_names_are_not_the_console_labels():
    """A NOTICE cites the formal names, not the short console labels.

    If the two ever collapse back together this test says why that matters.
    """
    spec = model_download.get_spec("cohere")
    assert spec.notice_title == "Cohere Transcribe 2B"
    assert spec.license_title == "the Apache License, Version 2.0"
    # The console labels stay short; provenance keeps the SPDX id.
    assert spec.display_name == "Cohere Transcribe"
    assert spec.license_name == "Apache-2.0"
    assert spec.notice_title != spec.display_name
    assert spec.license_title != spec.license_name


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
    monkeypatch.setitem(model_download.MODELS, "cohere",
                        dataclasses.replace(model_download.COHERE, release_base=good))
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
def _make_bundle(tmp_path, prefix, files, n_parts=2):
    """Build a minimal split-xz bundle the installer can assemble offline.

    The payload tar holds ``files`` (name -> bytes) at its root, split into
    ``n_parts`` contiguous byte ranges and xz-compressed independently - exactly
    the layout ``scripts/prepare_model_release.py`` publishes.
    """
    payload = tmp_path / "payload"
    payload.mkdir(exist_ok=True)
    for name, data in files.items():
        p = payload / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tf:
        for name in files:
            tf.add(payload / name, arcname=name)
    raw = tar_buf.getvalue()

    size = (len(raw) + n_parts - 1) // n_parts
    bundle = tmp_path / f"bundle-{prefix}"
    bundle.mkdir(exist_ok=True)
    sums = []
    for i in range(n_parts):
        name = f"{prefix}.part{i + 1}.xz"
        blob = lzma.compress(raw[i * size:(i + 1) * size])
        (bundle / name).write_bytes(blob)
        sums.append(f"{hashlib.sha256(blob).hexdigest()}  {name}")
    (bundle / f"{prefix}.SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    return bundle


def _make_local_bundle(tmp_path, weights=None, revision=None, n_parts=2):
    """A Cohere-shaped bundle using the required_local file set."""
    spec = model_download.get_spec("cohere")
    prefix = spec.asset_prefix if revision is None else f"cohere-transcribe-{revision}"
    files = {name: f"stub-{name}".encode() for name in spec.required_local}
    weights = weights if weights is not None else b"fake-weights-" + b"x" * 128
    files["model.safetensors"] = weights
    return _make_bundle(tmp_path, prefix, files, n_parts=n_parts), weights


def _patch_cohere_digests(monkeypatch, weights):
    monkeypatch.setitem(
        model_download.MODELS, "cohere",
        dataclasses.replace(
            model_download.COHERE,
            digests={"model.safetensors": hashlib.sha256(weights).hexdigest()},
        ),
    )


def test_install_from_local_bundle_directory(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision=model_download.REVISION)
    _patch_cohere_digests(monkeypatch, weights)
    dest = tmp_path / "dest"

    result = model_download.install_from_local_bundle(str(bundle), dest=str(dest))

    assert result == str(dest)
    assert (dest / "model.safetensors").read_bytes() == weights
    for name in model_download._REQUIRED_LOCAL:
        assert (dest / name).exists(), name
    source = json.loads((dest / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["source_kind"] == "local"


def test_install_from_local_bundle_zip(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision=model_download.REVISION)
    _patch_cohere_digests(monkeypatch, weights)
    archive = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in bundle.iterdir():
            zf.write(item, arcname=item.name)
    dest = tmp_path / "dest"

    result = model_download.install_from_local_bundle(str(archive), dest=str(dest))

    assert result == str(dest)
    assert (dest / "model.safetensors").read_bytes() == weights


def test_install_from_local_bundle_rejects_tampered_part(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision=model_download.REVISION)
    _patch_cohere_digests(monkeypatch, weights)
    target = sorted(bundle.glob("*.part1.xz"))[0]
    blob = bytearray(target.read_bytes())
    blob[-1] ^= 0xFF
    target.write_bytes(bytes(blob))

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        model_download.install_from_local_bundle(
            str(bundle), dest=str(tmp_path / "dest"))


def test_install_from_a_registered_spec_resolves_and_honours_digests(monkeypatch, tmp_path):
    """A second (format-agnostic) spec installs through the same code path."""
    files = {"config.json": b"{}", "weights.gguf": b"fake-gguf-weights"}
    spec = _synthetic_spec(files=files)
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, files)
    dest = tmp_path / "dest"

    result = model_download.install_model_from_local_bundle(
        spec.name, str(bundle), dest=str(dest))

    assert result == str(dest)
    assert (dest / "weights.gguf").read_bytes() == files["weights.gguf"]
    source = json.loads((dest / "SOURCE.json").read_text(encoding="utf-8"))
    assert source["digests"] == dict(spec.digests)
    assert "model_safetensors_sha256" not in source


def test_install_rejects_a_bad_digest_map_entry(monkeypatch, tmp_path):
    """The digest map is honoured even when every part checksum is correct."""
    good = {"config.json": b"{}", "weights.gguf": b"fake-gguf-weights"}
    wrong = dict(good)
    wrong["weights.gguf"] = b"a-different-file"
    spec = _synthetic_spec(files=good, digests={
        "config.json": hashlib.sha256(good["config.json"]).hexdigest(),
        "weights.gguf": hashlib.sha256(wrong["weights.gguf"]).hexdigest(),
    })
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, good)

    with pytest.raises(RuntimeError, match=r"Extracted weights\.gguf sha256 .* does not match"):
        model_download.install_model_from_local_bundle(
            spec.name, str(bundle), dest=str(tmp_path / "dest"))


def test_ensure_model_installs_a_synthetic_spec_offline(monkeypatch, tmp_path):
    """A fake local bundle installs with NO network, through the name API."""
    files = {"config.json": b"{}", "weights.gguf": b"fake-gguf-weights"}
    spec = _synthetic_spec(files=files)
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, files)
    monkeypatch.setenv("VT_MODEL_SOURCE_DIR", str(bundle))
    monkeypatch.setenv("VT_AUTO_DOWNLOAD_MODEL", "1")

    def _network_forbidden(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("ensure_model hit the network despite a local bundle")

    monkeypatch.setattr(model_download.urllib.request, "urlopen", _network_forbidden)
    monkeypatch.setattr(model_download, "_asset_exists", _network_forbidden)

    dest = tmp_path / "dest"
    assert model_download.ensure_model(spec.name, dest=str(dest)) == str(dest)
    assert (dest / "weights.gguf").exists()
    assert model_download.is_model_complete(spec.name, str(dest)) is True


def test_ensure_local_cohere_installs_from_env_bundle_offline(monkeypatch, tmp_path):
    bundle, weights = _make_local_bundle(tmp_path, revision=model_download.REVISION)
    _patch_cohere_digests(monkeypatch, weights)
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


# ---------------------------------------------------------------------------
# CLI: --model selects the registry entry
# ---------------------------------------------------------------------------
def _run_cli(*args):
    return subprocess.run(
        [sys.executable, str(SRC_DIR / "voice_transcriber" / "model_download.py"), *args],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )


def test_cli_verify_only_uses_the_cohere_spec(tmp_path):
    for req in model_download._REQUIRED_LOCAL:
        (tmp_path / req).write_text("stub", encoding="utf-8")

    result = _run_cli("--model", "cohere", "--verify-only", "--dest", str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "Cohere model" in result.stdout


def test_cli_rejects_an_unknown_model():
    result = _run_cli("--model", "definitely-not-a-model", "--verify-only", "--dest", "/tmp")
    assert result.returncode == 2
    assert "unknown model" in result.stdout
