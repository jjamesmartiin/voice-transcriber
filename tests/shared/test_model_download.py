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
import http.client
import io
import json
import lzma
import os
import re
import subprocess
import sys
import tarfile
import threading
import types
import urllib.error
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import model_download
import paths
from voice_transcriber import releases


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


def test_cohere_spec_package_files_have_complete_digests():
    """Every file in package_files must have a pinned SHA-256 digest."""
    spec = model_download.get_spec("cohere")
    assert set(spec.package_files) <= set(spec.digests)


def test_release_base_refuses_http(monkeypatch):
    """An insecure http:// release base URL must be rejected."""
    monkeypatch.setenv("VT_MODEL_RELEASE_BASE", "http://insecure.example.com/releases")
    with pytest.raises(ValueError, match="Model release base must use https://"):
        model_download._release_base(model_download.COHERE)


def test_download_uses_proxy_free_opener(monkeypatch):
    """The weight downloader must not consult environment proxies."""
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9999")
    opener = model_download._OPENER
    assert not any(
        isinstance(h, urllib.request.ProxyHandler)
        for h in opener.handle_open.get("https", [])
    ), "Opener must not route https requests through ProxyHandler"



def test_formatter_and_diarization_specs_are_registered():
    """The two redistributable non-ASR models resolve and carry their own data.

    A fresh machine needs a download path for both; before C6 the registry held
    only Cohere, so enabling the formatter or meeting mode did nothing unless the
    files had been placed by hand.
    """
    formatter = model_download.get_spec("formatter")
    assert formatter.repo_id == "superwhisper/s1-mini-GGUF"
    assert formatter.target_subdir == "formatter"
    assert formatter.required_local == ("s1-mini-q4_k_m.gguf",)
    assert formatter.digests["s1-mini-q4_k_m.gguf"] == (
        "3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634")
    assert formatter.model_ids == ("s1-mini",)
    # The licence's naming term must survive as the display name.
    assert formatter.display_name == '"S1-mini" by "Superwhisper"'

    diarization = model_download.get_spec("diarization")
    assert diarization.target_subdir == "diarization"
    assert diarization.model_ids == ("diarization",)
    assert set(diarization.required_local) == set(diarization.digests)
    # Nested install layout the backend reads (diarizers/sherpa_onnx.py).
    assert any(f.endswith("pyannote-segmentation-3-0/model.onnx")
               for f in diarization.required_local)
    assert any(f.endswith(".onnx") and "3dspeaker" in f
               for f in diarization.required_local)


def test_cohere_digests_contain_code_files():
    """Remote code executed under trust_remote_code must be pinned in digests.

    The ``.py`` files are the code; the JSON files are what *name* them, and they
    are read by ``AutoConfig``/``AutoProcessor`` while ``trust_remote_code=True``
    is in force, so a rewritten ``config.json`` could point ``auto_map`` at any
    module without changing a single pinned hash. Both halves are pinned.
    """
    cohere = model_download.get_spec("cohere")
    code_digests = {
        "configuration_cohere_asr.py": "2a33be079910efb6ea3c40d5d34cda69a8ffbbc76da93b218338fb71d30f9fef",
        "modeling_cohere_asr.py": "ca5a0b67a1cba76e86e54a3bfe350255a17868f27a4b2faa400c2daa520193e0",
        "processing_cohere_asr.py": "151f965b721c068897cbf199296aa3d4d652dffaa2387d6edef7f10a9b16fb74",
        "tokenization_cohere_asr.py": "415fff67e1226d3467c71d22144e56825fd42c8ee41f677bdabe3a86119141ec",
        # Code-selection files, verified byte-identical to the HF snapshot the
        # pinned revision names (499888924f5f1313b48ab0686c8f3a94178a4709).
        "config.json": "5de7e586cec6d8f51225c8d5fe17a56a3043dda9af8c42f9cb01dd545905eb18",
        "processor_config.json": "d5eff85971bab7f42480856f8fb397d8dd966858d67049def99aa8a665aa87d5",
        "preprocessor_config.json": "9f297d330646ecc8ebb9dc5784f48b7c35b118c913e306a1ccd0192f2c976332",
    }
    for fname, expected_hash in code_digests.items():
        assert fname in cohere.digests
        assert cohere.digests[fname] == expected_hash
    assert cohere.digests["model.safetensors"] == model_download.SAFETENSORS_SHA256


def test_registry_name_for_model_maps_config_ids_to_registry_keys():
    assert model_download.registry_name_for_model("s1-mini") == "formatter"
    assert model_download.registry_name_for_model("S1-MINI") == "formatter"
    assert model_download.registry_name_for_model("diarization") == "diarization"
    assert model_download.registry_name_for_model("gpt-9-turbo") is None
    assert model_download.registry_name_for_model("") is None
    assert model_download.registry_name_for_model(None) is None


def test_every_shipped_formatter_model_id_has_a_registry_entry():
    """`formatter.MODELS` and the registry must not drift apart.

    `formatter_model` names a *model id*; the registry is the single home for
    what that id actually downloads. Without this pin, a new model in the
    settings cycle could resolve to nothing at download time.
    """
    import formatter as formatter_module

    for model_id in formatter_module.MODELS:
        name = model_download.registry_name_for_model(model_id)
        assert name is not None, f"no registry entry claims model id {model_id!r}"
        spec = model_download.get_spec(name)
        assert any(str(f).endswith(".gguf") for f in spec.required_local), (
            f"{name} declares no GGUF for {model_id!r}")


def test_models_dir_prefers_an_existing_per_user_install(monkeypatch, tmp_path):
    """Registering an entry must not hide weights installed outside a checkout.

    Regression the diarization graphs exposed: a writable checkout with no
    `models/<subdir>` used to win over a complete per-user install, so adding the
    `diarization` spec made already-present graphs unfindable.
    """
    monkeypatch.delenv("VT_MODEL_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(model_download, "find_repo_root", lambda: str(tmp_path / "repo"))
    (tmp_path / "repo").mkdir()
    spec = _synthetic_spec(name="synthetic", target_subdir="synthetic")
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)

    installed = tmp_path / "vt" / "models" / "synthetic"
    installed.mkdir(parents=True)
    for req in spec.required_local:
        (installed / req).write_text("stub", encoding="utf-8")

    assert model_download.models_dir("synthetic") == str(installed)
    # ... and a completely absent install still targets the writable checkout.
    for req in spec.required_local:
        (installed / req).unlink()
    assert model_download.models_dir("synthetic") == os.path.join(
        str(tmp_path), "repo", "models", "synthetic")


def test_ensure_model_async_is_inert_without_a_status_handler(monkeypatch, tmp_path):
    """No UI attached -> no background download (keeps shared tests hermetic)."""
    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    monkeypatch.setattr(model_download, "_status_handler", None)
    called = []
    monkeypatch.setattr(model_download, "ensure_model",
                        lambda *a, **k: called.append(a) or str(tmp_path))

    assert model_download.ensure_model_async(spec.name, dest=str(tmp_path / "d")) is False
    assert called == []


def test_ensure_model_async_runs_the_install_on_a_daemon_thread(monkeypatch, tmp_path):
    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    monkeypatch.setattr(model_download, "_status_handler", lambda *a: None)
    done = threading.Event()
    calls = []

    def fake_ensure(name, dest=None, base_url=None, revision=None):
        calls.append((name, dest))
        done.set()
        return dest

    monkeypatch.setattr(model_download, "ensure_model", fake_ensure)
    dest = str(tmp_path / "d")
    assert model_download.ensure_model_async(spec.name, dest=dest, consented=True) is True
    assert done.wait(5), "the background install never ran"
    assert calls == [(spec.name, dest)]
    # A completed install must not start a second one.
    monkeypatch.setattr(model_download, "is_model_complete", lambda *a, **k: True)
    assert model_download.ensure_model_async(spec.name, dest=dest, consented=True) is False


def test_prompt_policy_overrides_the_tty_heuristic(monkeypatch):
    """A TUI owning the terminal must be able to forbid the stdin prompt."""
    monkeypatch.setattr(model_download, "_prompt_allowed", None)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    assert model_download._can_prompt() is True

    model_download.set_prompt_allowed(False)
    assert model_download._can_prompt() is False
    model_download.set_prompt_allowed(True)
    assert model_download._can_prompt() is True
    model_download.set_prompt_allowed(None)
    assert model_download._can_prompt() is True


def test_ensure_model_async_waits_for_consent_under_a_tui(monkeypatch, tmp_path):
    """With no safe way to ask, report the size and start nothing.

    This is the fresh-machine-under-a-TUI case: the first-use path must not
    prompt on stdin from a background thread (invisible, and it blocks on
    keystrokes the TUI consumes), and it must not download without consent.
    """
    spec = _synthetic_spec(download_hint="~462 MiB")
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    monkeypatch.setattr(model_download, "_prompt_allowed", False)
    monkeypatch.setattr(model_download, "_async_consent_notified", set())
    monkeypatch.delenv("VT_AUTO_DOWNLOAD_MODEL", raising=False)
    notices = []
    monkeypatch.setattr(
        model_download, "_status_handler",
        lambda stage, pct, text: notices.append((stage, pct, text)))
    started = []
    monkeypatch.setattr(model_download, "ensure_model",
                        lambda *a, **k: started.append(a))

    dest = str(tmp_path / "d")
    assert model_download.ensure_model_async(spec.name, dest=dest) is False
    # A second first-use utterance does not spam the status line.
    assert model_download.ensure_model_async(spec.name, dest=dest) is False
    assert started == [], "started a download without consent"
    assert len(notices) == 1, notices
    _stage, _pct, text = notices[0]
    assert "~462 MiB" in text
    assert f"{spec.name}-download" in text


def test_ensure_model_async_consent_starts_the_install(monkeypatch, tmp_path):
    """The explicit command is what gets past the TUI consent gate."""
    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    monkeypatch.setattr(model_download, "_prompt_allowed", False)
    monkeypatch.setattr(model_download, "_async_consent_notified", set())
    monkeypatch.setattr(model_download, "_status_handler", lambda *a: None)
    done = threading.Event()
    monkeypatch.setattr(model_download, "ensure_model",
                        lambda name, **k: done.set())

    assert model_download.ensure_model_async(
        spec.name, dest=str(tmp_path / "d"), consented=True) is True
    assert done.wait(5), "explicit consent did not start the install"


def test_an_in_flight_install_is_never_reported_as_missing(monkeypatch, tmp_path):
    """A first-use call during a consented download must ride along silently.

    Checking consent before the in-flight set made an utterance that arrived mid
    download re-report "not installed; run ...-download", which is both wrong and
    noise the user cannot act on while the install is already running.
    """
    spec = _synthetic_spec(download_hint="~462 MiB")
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    monkeypatch.setattr(model_download, "_prompt_allowed", False)
    monkeypatch.setattr(model_download, "_async_consent_notified", set())
    notices = []
    monkeypatch.setattr(model_download, "_status_handler",
                        lambda stage, pct, text: notices.append(text))
    release = threading.Event()
    monkeypatch.setattr(model_download, "ensure_model",
                        lambda name, **k: release.wait(5))

    dest = str(tmp_path / "d")
    assert model_download.ensure_model_async(
        spec.name, dest=dest, consented=True) is True
    assert model_download.ensure_model_async(spec.name, dest=dest) is True
    assert notices == [], notices
    release.set()


def test_auto_download_env_is_standing_consent(monkeypatch, tmp_path):
    """VT_AUTO_DOWNLOAD_MODEL=1 keeps its "do not ask" meaning."""
    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    monkeypatch.setattr(model_download, "_prompt_allowed", False)
    monkeypatch.setattr(model_download, "_async_consent_notified", set())
    monkeypatch.setattr(model_download, "_status_handler", lambda *a: None)
    monkeypatch.setenv("VT_AUTO_DOWNLOAD_MODEL", "1")
    done = threading.Event()
    monkeypatch.setattr(model_download, "ensure_model",
                        lambda name, **k: done.set())

    assert model_download.ensure_model_async(
        spec.name, dest=str(tmp_path / "d")) is True
    assert done.wait(5), "standing consent did not start the install"


def test_the_download_prompt_states_the_size_before_fetching(capsys, monkeypatch, tmp_path):
    """A clean machine must be told the size before anything downloads.

    The prompt is answered "no" and the local-bundle probe is stubbed out, so
    this proves the wording without touching the network or writing a file.
    """
    monkeypatch.setenv("VT_MODEL_DIR", str(tmp_path / "formatter"))
    monkeypatch.delenv("VT_MODEL_SOURCE_DIR", raising=False)
    monkeypatch.delenv("VT_MODEL_BUNDLE", raising=False)
    monkeypatch.delenv("VT_AUTO_DOWNLOAD_MODEL", raising=False)
    monkeypatch.setattr(model_download, "_local_model_source", lambda: (None, False))
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert model_download.ensure_model("formatter") is None
    out = capsys.readouterr().out
    assert "Download size: ~462 MiB." in out, out
    assert '"S1-mini" by "Superwhisper"' in out, out


def test_ensure_model_never_prompts_when_the_policy_forbids_it(capsys, monkeypatch, tmp_path):
    """The TUI case: state the size, but never call input() on stdin.

    Under a full-screen TUI stdin is still a tty, so the old isatty-only guard
    called input() from a background thread - an invisible prompt that blocked on
    keystrokes the TUI was consuming.
    """
    monkeypatch.setenv("VT_MODEL_DIR", str(tmp_path / "formatter"))
    monkeypatch.delenv("VT_MODEL_SOURCE_DIR", raising=False)
    monkeypatch.delenv("VT_MODEL_BUNDLE", raising=False)
    monkeypatch.delenv("VT_AUTO_DOWNLOAD_MODEL", raising=False)
    monkeypatch.setattr(model_download, "_local_model_source", lambda: (None, False))
    monkeypatch.setattr(model_download, "_prompt_allowed", False)
    monkeypatch.setattr(sys, "stdin", types.SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input",
                        lambda *a, **k: pytest.fail("prompted on stdin under a TUI"))
    # Stop before the network: the manifest fetch is the first network call.
    monkeypatch.setattr(
        model_download, "_fetch_part_manifest",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in this test")))

    assert model_download.ensure_model("formatter") is None
    out = capsys.readouterr().out
    assert "Download size: ~462 MiB." in out, out


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


def test_a_stale_revision_recorded_in_source_json_is_not_complete(tmp_path, monkeypatch):
    """The weights are keyed by revision, so a bump must re-install, not reuse.

    Regression: completeness was "the required filenames exist", and the revision
    written into SOURCE.json was never read back. After a revision bump every
    existing user kept running the previous model, silently, and the doctor
    reported their weights as verified.
    """
    import json

    spec = _synthetic_spec()
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    for req in spec.required_local:
        (tmp_path / req).write_text("stub", encoding="utf-8")

    # No SOURCE.json (a manual install) stays supported.
    assert model_download.is_model_complete(spec.name, str(tmp_path)) is True

    # The revision this spec wants, as written by _write_provenance.
    (tmp_path / "SOURCE.json").write_text(
        json.dumps({"model": spec.repo_id, "revision": spec.revision}), encoding="utf-8")
    assert model_download.is_model_complete(spec.name, str(tmp_path)) is True

    # An older install: same files, previous revision.
    (tmp_path / "SOURCE.json").write_text(
        json.dumps({"model": spec.repo_id, "revision": "an-older-revision"}), encoding="utf-8")
    assert model_download.is_model_complete(spec.name, str(tmp_path)) is False

    # Unreadable provenance must not force a re-download on its own.
    (tmp_path / "SOURCE.json").write_text("{not json", encoding="utf-8")
    assert model_download.is_model_complete(spec.name, str(tmp_path)) is True


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
    (src / "paths.py").write_text("# stub", encoding="utf-8")
    (tmp_path / "checkout" / ".git").write_text(
        "gitdir: /elsewhere/.git/worktrees/checkout\n", encoding="utf-8"
    )
    monkeypatch.setattr(paths, "__file__", str(src / "paths.py"))

    assert model_download.find_repo_root() == str(tmp_path / "checkout")


def test_find_repo_root_is_none_without_a_marker(monkeypatch, tmp_path):
    """Read-only installs (Nix store, AppImage) have no marker: fall back."""
    src = tmp_path / "readonly_app" / "src" / "voice_transcriber"
    src.mkdir(parents=True)
    (src / "paths.py").write_text("# stub", encoding="utf-8")
    monkeypatch.setattr(paths, "__file__", str(src / "paths.py"))

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
    # Cohere spec includes code file digests alongside safetensors.
    assert data["digests"] == dict(model_download.COHERE.digests)


def test_write_provenance_records_the_tag_a_real_download_came_from(tmp_path):
    """The assets redirect, so the tag has to come from the URL we asked for.

    A release asset 302s to objects.githubusercontent.com, whose URL carries no
    tag; deriving `release_tag` from the resolved URL alone left it null for
    every real download while `download_url_base` named the tag.
    """
    model_download._write_provenance(
        model_download.get_spec("diarization"),
        str(tmp_path),
        base_url="https://github.com/jjamesmartiin/voice-transcriber"
                 f"/releases/download/{releases.PREREQUISITES_TAG}",
        resolved_url="https://objects.githubusercontent.com/github-production"
                     "/release-assets/1234?X-Amz-Expires=300",
    )
    data = json.loads((tmp_path / "SOURCE.json").read_text(encoding="utf-8"))
    assert data["release_tag"] == releases.PREREQUISITES_TAG
    assert data["download_url_base"].endswith(f"/{releases.PREREQUISITES_TAG}")


def test_write_provenance_single_safetensors_omits_digests_map(tmp_path):
    """A spec containing only model.safetensors keeps the legacy key set without redundant digests map."""
    spec = dataclasses.replace(
        model_download.COHERE,
        digests={"model.safetensors": model_download.SAFETENSORS_SHA256},
    )
    model_download._write_provenance(
        spec,
        str(tmp_path),
        base_url="https://github.com/jjamesmartiin/voice-transcriber/releases/download/v1.1.0",
    )
    data = json.loads((tmp_path / "SOURCE.json").read_text(encoding="utf-8"))
    assert data["model_safetensors_sha256"] == model_download.SAFETENSORS_SHA256
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


def _fake_body_response(payload, attempts, *, mode):
    """A ``urlopen`` response that misbehaves on the first attempt only."""

    class FakeResponse:
        def __init__(self, data):
            self._data = io.BytesIO(data)
            self.headers = {"Content-Length": str(len(data))}

        def read(self, size=-1):
            if attempts["n"] == 1:
                if mode == "incomplete":
                    raise http.client.IncompleteRead(b"", len(payload))
                if self._data.tell() >= len(payload) // 2:
                    return b""  # short body: EOF halfway through
            return self._data.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    return FakeResponse(payload)


def test_http_get_retries_an_incomplete_read(monkeypatch, tmp_path):
    """A dropped connection mid-part must be retried, not abort the install.

    ``http.client.IncompleteRead`` is an ``HTTPException``, not an ``OSError``,
    so it used to bypass the retry tuple and lose a whole 2.8 GB download.
    """
    payload = b"x" * (2 << 20)
    attempts = {"n": 0}

    def fake_urlopen(req, timeout=120):
        attempts["n"] += 1
        return _fake_body_response(payload, attempts, mode="incomplete")

    monkeypatch.setattr(model_download.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_download.time, "sleep", lambda *a, **k: None)
    dest = tmp_path / "part.xz"

    assert model_download._http_get(
        "https://example.com/p", str(dest), "part") == len(payload)
    assert attempts["n"] == 2
    assert dest.read_bytes() == payload


def test_http_get_retries_a_short_body(monkeypatch, tmp_path):
    """A response that ends before its declared length is retried too."""
    payload = b"y" * (2 << 20)
    attempts = {"n": 0}

    def fake_urlopen(req, timeout=120):
        attempts["n"] += 1
        return _fake_body_response(payload, attempts, mode="short")

    monkeypatch.setattr(model_download.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(model_download.time, "sleep", lambda *a, **k: None)
    dest = tmp_path / "part.xz"

    assert model_download._http_get(
        "https://example.com/p", str(dest), "part") == len(payload)
    assert attempts["n"] == 2


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


def test_every_bundle_shares_one_prerequisites_release():
    """One release hosts every prerequisite, keyed by *content* not by tag.

    That is what keeps the releases list to a single prerequisites entry: a new
    app release uploads nothing, and a model revision adds assets to this tag
    instead of minting one. Safety comes from the asset names -- they embed the
    revision, so a bundle for another revision 404s on its own revision-named
    manifest rather than being served in its place.
    """
    assert releases.PREREQUISITES_TAG == "PREREQUISITES"
    for name, spec in model_download.MODELS.items():
        assert spec.bundle_tag == releases.PREREQUISITES_TAG, name
        assert spec.release_base == (
            "https://github.com/jjamesmartiin/voice-transcriber"
            f"/releases/download/{releases.PREREQUISITES_TAG}"), name
        # The revision survives in the asset names, which is the whole safety net.
        assert spec.revision in spec.asset_prefix, name

    # The deprecated aliases still point at the cohere entry.
    spec = model_download.get_spec("cohere")
    assert model_download.MODEL_BUNDLE_TAG == spec.bundle_tag
    assert model_download.DEFAULT_RELEASE_BASE == spec.release_base
    assert spec.asset_prefix == f"cohere-transcribe-{model_download.REVISION}"


def test_an_alternate_revision_does_not_mint_a_tag():
    """A legacy revision override reuses the shared tag, as the real specs do.

    It used to derive `model-<name>-<rev12>`; with one prerequisites release the
    override must not go looking for a tag that will never exist.
    """
    other = "f" * 40
    resolved = model_download._resolved_spec("cohere", other)
    assert resolved.bundle_tag == releases.PREREQUISITES_TAG
    assert resolved.release_base == (
        "https://github.com/jjamesmartiin/voice-transcriber"
        f"/releases/download/{releases.PREREQUISITES_TAG}")
    assert resolved.asset_prefix == f"cohere-transcribe-{other}"
    assert resolved.asset_prefix != model_download.get_spec("cohere").asset_prefix


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
    # values must come from the import, never be pasted in. Matched on word
    # boundaries: the tag is a plain word now, so a substring test would also
    # fire on the publisher reading `releases.PREREQUISITES_TITLE`.
    def _pasted(value):
        return re.search(rf"\b{re.escape(value)}\b", text)

    assert not _pasted(model_download.MODEL_BUNDLE_TAG), (
        "publisher hardcodes the resolved bundle tag instead of importing it")
    assert not _pasted(model_download.REVISION), (
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
    # It can locate an install the app itself made, so a machine that never
    # touched the HF cache can still package.
    assert "find_installed_model_dir" in text


def test_packager_tars_nested_files_and_ships_a_verbatim_notice(tmp_path):
    """A spec may nest package files and ship its own NOTICE verbatim.

    Diarization's segmentation graph lives in a subdirectory, and S1-mini's
    upstream NOTICE carries an operative naming clause that a generated NOTICE
    would paraphrase, so both capabilities are load-bearing.
    """
    notice_text = 'S1-mini-GGUF\nCopyright 2026 Superwhisper\n'
    notice_path = tmp_path / "NOTICE.upstream"
    notice_path.write_bytes(notice_text.encode("utf-8"))
    files = {"seg/model.onnx": b"ONNX-weights", "embed.onnx": b"EMB"}
    spec = _synthetic_spec(files=files, notice_file=str(notice_path))
    model_dir = tmp_path / "model"
    for name, data in files.items():
        path = model_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    packager = _load_packager()
    tar_path = tmp_path / "model.tar"
    packager.build_tar(spec, str(model_dir), str(tmp_path / "staging"), str(tar_path))

    with tarfile.open(tar_path, "r") as tf:
        names = tf.getnames()
        assert "seg/model.onnx" in names, names
        assert "embed.onnx" in names, names
        assert tf.extractfile("NOTICE").read().decode().replace("\r\n", "\n") == notice_text


def test_packager_falls_back_to_the_registry_install_dir(monkeypatch, tmp_path):
    """A machine that installed through the app can package without --model-dir."""
    spec = _synthetic_spec()
    for req in spec.package_files:
        (tmp_path / req).write_bytes(b"x")
    monkeypatch.setattr(model_download, "models_dir", lambda name: str(tmp_path))
    packager = _load_packager()
    assert packager.find_installed_model_dir(spec) == str(tmp_path)
    # A directory missing a declared file is not an install.
    (tmp_path / spec.package_files[0]).unlink()
    assert packager.find_installed_model_dir(spec) is None


def test_install_from_a_bundle_with_a_nested_package_file(monkeypatch, tmp_path):
    """The format-agnostic installer handles a nested layout (diarization)."""
    files = {"seg/model.onnx": b"ONNX-weights", "embed.onnx": b"EMB"}
    spec = _synthetic_spec(files=files)
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, files)
    dest = tmp_path / "dest"

    result = model_download.install_model_from_local_bundle(
        spec.name, str(bundle), dest=str(dest))

    assert result == str(dest)
    assert (dest / "seg" / "model.onnx").read_bytes() == files["seg/model.onnx"]
    assert model_download.is_model_complete(spec.name, str(dest))


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


def test_s1_mini_notice_is_pinned_to_the_published_wording():
    """The S1-mini NOTICE is a legal artifact and must never drift.

    Its ADDITIONAL TERM requires the exact name '"S1-mini" by "Superwhisper"',
    so the *upstream* NOTICE ships byte-for-byte through ``spec.notice_file``
    instead of a generated one, which would paraphrase the operative clause.
    Pinned here the same way the Cohere NOTICE is.
    """
    spec = model_download.get_spec("formatter")
    assert spec.notice_file == "config/licenses/S1-mini-NOTICE.txt"
    notice = (REPO_ROOT / spec.notice_file).read_text(encoding="utf-8")
    assert notice == (
        "S1-mini-GGUF\n"
        "Copyright 2026 Superwhisper\n"
        "\n"
        "GGUF conversions of S1-mini, which is itself a derivative of Qwen3-0.6B,\n"
        "Copyright 2024 Alibaba Cloud, licensed under the Apache License, Version 2.0.\n"
        "\n"
        "Any use, distribution, or integration of this model, whether unmodified or\n"
        "as part of a derivative work or product, must continue to identify it by its\n"
        'original name, "S1-mini" by "Superwhisper", using that exact capitalization.\n'
        "This restates the ADDITIONAL TERM of the LICENSE file, which is the\n"
        "operative text.\n"
    )


def test_s1_mini_licence_carries_provenance_and_the_full_upstream_text():
    """The committed licence is the upstream text plus verifiable provenance."""
    spec = model_download.get_spec("formatter")
    lic = (REPO_ROOT / spec.license_file).read_text(encoding="utf-8")
    assert spec.repo_id in lic, "provenance must name the source repo"
    assert spec.revision in lic, "provenance must pin the revision"
    assert spec.digests["s1-mini-q4_k_m.gguf"] in lic, "provenance must pin the digest"
    # The full upstream text is retained: Apache-2.0 *and* the additional term.
    assert "Apache License" in lic
    assert "Version 2.0" in lic
    assert "ADDITIONAL TERM" in lic
    assert '"S1-mini" by "Superwhisper"' in lic


def test_diarization_licences_and_provenance_are_committed():
    """Both graphs' licences ship, with the digests that verify them."""
    spec = model_download.get_spec("diarization")
    lic = (REPO_ROOT / spec.license_file).read_text(encoding="utf-8")
    assert "MIT License" in lic                 # pyannote segmentation
    assert "Copyright (c) 2022 CNRS" in lic
    assert "Apache License" in lic              # 3D-Speaker embedding
    assert "recongition" in lic, "the upstream (misspelled) asset URL is provenance"
    assert spec.repo_id in lic
    for digest in spec.digests.values():
        assert digest in lic, f"provenance must pin {digest}"

    notice = (REPO_ROOT / spec.notice_file).read_text(encoding="utf-8")
    assert "pyannote" in notice
    assert "3D-Speaker" in notice
    assert "MIT" in notice and "Apache" in notice


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


def test_install_rejects_archive_member_not_in_allowlist(monkeypatch, tmp_path):
    """An archive member not listed in spec.package_files must be refused."""
    files = {"config.json": b"{}", "weights.gguf": b"fake-gguf-weights", "evil.py": b"print('pwned')"}
    spec = _synthetic_spec(files={"config.json": b"{}", "weights.gguf": b"fake-gguf-weights"})
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, files)

    with pytest.raises(RuntimeError, match=r"Refusing archive member 'evil\.py': not in synthetic package_files allowlist"):
        model_download.install_model_from_local_bundle(
            spec.name, str(bundle), dest=str(tmp_path / "dest"))


def test_install_allows_license_notice_and_subdirectories(monkeypatch, tmp_path):
    """The packager adds LICENSE and NOTICE, and diarization uses a subdirectory."""
    files = {
        "sub/model.onnx": b"fake-onnx",
        "LICENSE": b"MIT License",
        "NOTICE": b"Third party notices",
    }
    spec = _synthetic_spec(files={"sub/model.onnx": b"fake-onnx"})
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, files)

    installed = model_download.install_model_from_local_bundle(
        spec.name, str(bundle), dest=str(tmp_path / "dest"))
    assert installed is not None
    assert (tmp_path / "dest" / "sub" / "model.onnx").read_bytes() == b"fake-onnx"
    assert (tmp_path / "dest" / "LICENSE").read_bytes() == b"MIT License"
    assert (tmp_path / "dest" / "NOTICE").read_bytes() == b"Third party notices"


def _complete_dest_fixture(monkeypatch, tmp_path):
    """A synthetic spec, a valid bundle and a pre-existing dest directory."""
    files = {"config.json": b"{}", "weights.gguf": b"fake-gguf-weights"}
    spec = _synthetic_spec(files=files)
    monkeypatch.setitem(model_download.MODELS, spec.name, spec)
    bundle = _make_bundle(tmp_path, spec.asset_prefix, files)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "keep-me.txt").write_text("a good install", encoding="utf-8")
    return spec, bundle, dest


def test_a_second_installer_never_deletes_a_completed_install(monkeypatch, tmp_path):
    """Two concurrent installs must not delete each other's weights.

    With the destination already complete, the installer keeps it instead of
    ``rmtree``-ing it just to move an identical copy into place.
    """
    spec, bundle, dest = _complete_dest_fixture(monkeypatch, tmp_path)
    # Pretend a concurrent installer finished while we assembled: every
    # completeness check now says yes, so this one must not touch ``dest``.
    monkeypatch.setattr(model_download, "is_model_complete", lambda *a, **k: True)

    result = model_download.install_model_from_local_bundle(
        spec.name, str(bundle), dest=str(dest))

    assert result == str(dest)
    assert (dest / "keep-me.txt").read_text(encoding="utf-8") == "a good install"
    assert not (tmp_path / "dest.lock").exists()  # lock released


def test_a_held_install_lock_defers_to_the_other_installer(monkeypatch, tmp_path):
    """A lock held by another process means its install is the one to use."""
    spec, bundle, dest = _complete_dest_fixture(monkeypatch, tmp_path)
    lock_dir = tmp_path / "dest.lock"
    lock_dir.mkdir()
    monkeypatch.setattr(model_download, "is_model_complete", lambda *a, **k: True)
    monkeypatch.setattr(model_download.time, "sleep", lambda *a, **k: None)

    result = model_download.install_model_from_local_bundle(
        spec.name, str(bundle), dest=str(dest))

    assert result == str(dest)
    assert (dest / "keep-me.txt").read_text(encoding="utf-8") == "a good install"
    # The other process's lock is not ours to remove.
    assert lock_dir.exists()


def test_a_stale_install_lock_does_not_block_a_later_install(monkeypatch, tmp_path, capsys):
    """A lock left behind by a killed installer must not be permanent.

    Regression guard: the wait used to end in a hard failure, so a ``.lock``
    directory dropped by a crash blocked every later install until somebody
    deleted it by hand. It now proceeds, because the re-check before the swap is
    what actually protects a finished install — a lock nobody owns is not
    evidence of anything.
    """
    spec, bundle, dest = _complete_dest_fixture(monkeypatch, tmp_path)
    lock_dir = tmp_path / "dest.lock"
    lock_dir.mkdir()
    # The wait polls a destination that never becomes complete, so it runs out.
    monkeypatch.setattr(model_download.time, "sleep", lambda *a, **k: None)

    result = model_download.install_model_from_local_bundle(
        spec.name, str(bundle), dest=str(dest))

    assert result == str(dest)
    assert "looks stuck; continuing" in capsys.readouterr().out
    assert lock_dir.exists()  # still not ours to remove


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


def test_extract_bundle_archive_rejects_tar_slip(monkeypatch, tmp_path):
    """Tar archives with '../' traversing outside dest must be rejected."""
    bad_tar = tmp_path / "bad.tar"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        ti = tarfile.TarInfo(name="../../evil.txt")
        content = b"pwned"
        ti.size = len(content)
        tf.addfile(ti, io.BytesIO(content))
    bad_tar.write_bytes(buf.getvalue())

    dest = tmp_path / "extract_dest"
    dest.mkdir()
    with pytest.raises((RuntimeError, tarfile.FilterError)):
        model_download._extract_bundle_archive(str(bad_tar), str(dest))
    assert not (tmp_path / "evil.txt").exists()

    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    with pytest.raises(RuntimeError, match="Tar path traversal detected"):
        model_download._extract_bundle_archive(str(bad_tar), str(dest))
    assert not (tmp_path / "evil.txt").exists()


def test_extract_bundle_archive_rejects_tar_links(monkeypatch, tmp_path):
    """A symlink member defeats the name check, so links are refused outright.

    Regression: the fallback guard only checked ``member.name``, so
    ``escape -> .`` followed by ``escape/evil.txt`` (or a link to an absolute path
    such as ``/home/user/.bashrc``) passed every check and ``extractall`` wrote
    through the link, outside ``dest`` (CVE-2007-4559). Only the no-data_filter
    path is affected, and that is exactly the path this exercises.
    """
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    target = tmp_path / "victim.txt"
    target.write_text("original")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        link = tarfile.TarInfo(name="escape")
        link.type = tarfile.SYMTYPE
        link.linkname = str(target.parent)
        tf.addfile(link)
        evil = tarfile.TarInfo(name="escape/victim.txt")
        content = b"pwned"
        evil.size = len(content)
        tf.addfile(evil, io.BytesIO(content))
    bad_tar = tmp_path / "link.tar"
    bad_tar.write_bytes(buf.getvalue())

    dest = tmp_path / "extract_dest"
    dest.mkdir()
    with pytest.raises(RuntimeError, match="link or special file"):
        model_download._extract_bundle_archive(str(bad_tar), str(dest))
    assert target.read_text() == "original", "the symlink was written through"


def test_assemble_parts_rejects_tar_slip_without_data_filter(monkeypatch, tmp_path):
    """TarSlip guard in _assemble_parts rejects traversal when data_filter is unavailable."""
    bad_tar_buf = io.BytesIO()
    with tarfile.open(fileobj=bad_tar_buf, mode="w") as tf:
        ti = tarfile.TarInfo(name="../../evil.txt")
        content = b"pwned"
        ti.size = len(content)
        tf.addfile(ti, io.BytesIO(content))
    raw_tar = bad_tar_buf.getvalue()
    xz_blob = lzma.compress(raw_tar)

    part = tmp_path / "evil.part1.xz"
    part.write_bytes(xz_blob)
    digest = hashlib.sha256(xz_blob).hexdigest()

    manifest = {"evil.part1.xz": digest}
    part_paths = {"evil.part1.xz": str(part)}
    dest = tmp_path / "dest"
    spec = model_download.COHERE

    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    with pytest.raises(RuntimeError, match="Tar path traversal detected"):
        model_download._assemble_parts(spec, part_paths, manifest, str(dest), origin="test")
    assert not (tmp_path / "evil.txt").exists()


def test_assemble_parts_rejects_tar_slip_with_data_filter(tmp_path):
    """TarSlip guard in _assemble_parts rejects traversal when data_filter is available."""
    if not hasattr(tarfile, "data_filter"):
        pytest.skip("tarfile.data_filter not available on this Python runtime")
    bad_tar_buf = io.BytesIO()
    with tarfile.open(fileobj=bad_tar_buf, mode="w") as tf:
        ti = tarfile.TarInfo(name="../../evil.txt")
        content = b"pwned"
        ti.size = len(content)
        tf.addfile(ti, io.BytesIO(content))
    raw_tar = bad_tar_buf.getvalue()
    xz_blob = lzma.compress(raw_tar)

    part = tmp_path / "evil.part1.xz"
    part.write_bytes(xz_blob)
    digest = hashlib.sha256(xz_blob).hexdigest()

    manifest = {"evil.part1.xz": digest}
    part_paths = {"evil.part1.xz": str(part)}
    dest = tmp_path / "dest"
    spec = model_download.COHERE

    with pytest.raises((RuntimeError, tarfile.FilterError)):
        model_download._assemble_parts(spec, part_paths, manifest, str(dest), origin="test")
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
