#!/usr/bin/env python3
"""Structural pins for the pinned ``llama-server`` resolution in the build.

The Windows PyInstaller build bundles a formatter binary into the released zip.
It must take that binary only from an explicit, pinned source
(``VT_LLAMA_SERVER_ASSET`` or the fixed ``PROJECT_ROOT/<ASSET_NAME>``) and
**never** from ``PATH``: on Windows an empty or ``.`` PATH component means the
CWD, so the build machine's environment -- or the build directory itself --
would otherwise decide which binary ships inside the release (CWE-427,
uncontrolled search path). See ``docs/audit-2026-10-11.md`` 3.5.

The pinned artifact is a *file set*, not a file: ``llama-server.exe`` is a ~9 KB
stub whose only local import is ``llama-server-impl.dll``. A build that copies
the executable alone produces a bundle that resolves and then fails to start, so
these tests pin that an incomplete set is refused and the whole set is copied.

The build itself cannot run here (no Windows/PyInstaller), so these tests pin the
resolution *behaviour* and the absence of the PATH lookup structurally. The
stand-in sets are named from ``pin.REQUIRED_MEMBERS`` so they cannot drift from
the declaration.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_PATH = REPO_ROOT / "platforms" / "windows" / "build_offline.py"

sys.path.insert(0, str(REPO_ROOT / "src"))
from voice_transcriber import llama_server_bundle as pin  # noqa: E402


def _load_build():
    spec = importlib.util.spec_from_file_location("build_offline", BUILD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build():
    return _load_build()


def _make_set(root):
    """A stand-in for an unpacked pinned set: every required name, real files."""
    root.mkdir(parents=True, exist_ok=True)
    for name in pin.REQUIRED_MEMBERS:
        (root / name).write_bytes(b"stand-in")
    return root


def _make_zip(path, names=None):
    """A stand-in for the upstream asset, with the same member names by default."""
    with zipfile.ZipFile(path, "w") as archive:
        for name in (pin.REQUIRED_MEMBERS if names is None else names):
            archive.writestr(name, b"stand-in")
    return path


class TestNeverConsultsPath:
    """The regression the whole finding exists for: no PATH lookup, anywhere."""

    def test_source_never_names_a_path_lookup(self):
        # The structural pin. A source grep catches a reintroduced lookup in
        # ``build_exe`` itself, not just inside the resolver, which a behavioural
        # test on the resolver alone would miss.
        source = BUILD_PATH.read_text(encoding="utf-8")
        assert "shutil.which" not in source
        assert "import which" not in source

    def test_resolution_does_not_call_which(self, build, monkeypatch, tmp_path):
        # Belt and braces: even a dynamically constructed lookup breaks. Any
        # branch that consults PATH raises here.
        def _forbidden(*_args, **_kwargs):
            raise AssertionError("build must not consult PATH for llama-server")

        monkeypatch.setattr(build.shutil, "which", _forbidden)

        source = _make_set(tmp_path / "set")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(source))
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        assert build.resolve_llama_server() == source

        monkeypatch.delenv(build.ENV_LLAMA_SERVER_ASSET, raising=False)
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", source)
        assert build.resolve_llama_server() == source


class TestCompleteSet:
    """A complete set is accepted, and the whole set is what gets copied."""

    def test_complete_directory_set_is_accepted(self, build, monkeypatch, tmp_path):
        source = _make_set(tmp_path / "set")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(source))
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        assert build.resolve_llama_server() == source

    def test_complete_set_is_copied_whole(self, build, tmp_path, capsys):
        source = _make_set(tmp_path / "set")
        dist = tmp_path / "dist"
        dist.mkdir()

        build.bundle_llama_server(source, dist)

        for name in pin.REQUIRED_MEMBERS:
            assert (dist / name).is_file(), name
        out = capsys.readouterr().out
        for dll in pin.RUNTIME_PREREQUISITES:
            assert dll in out                    # reported as a prerequisite
            assert not (dist / dll).exists()     # never copied into the bundle


class TestIncompleteSetIsRefused:
    """The stub-without-its-DLLs case must fail loudly *before* anything is copied."""

    def test_missing_members_are_a_hard_failure_that_names_them(
        self, build, monkeypatch, tmp_path
    ):
        source = _make_set(tmp_path / "set")
        missing = ["llama-server-impl.dll", "ggml-base.dll"]
        for name in missing:
            (source / name).unlink()
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(source))
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)

        with pytest.raises(SystemExit) as excinfo:
            build.resolve_llama_server()

        message = str(excinfo.value)
        assert "incomplete" in message
        for name in missing:
            assert name in message, name

    def test_a_lone_executable_is_not_a_set(self, build, monkeypatch, tmp_path):
        lone = tmp_path / "llama-server.exe"
        lone.write_bytes(b"the ~9 KB stub, without its DLLs")
        # Executable, so the pre-fix code (which only checked the execute bit)
        # would have accepted it: this is the stub-without-DLLs bundle.
        os.chmod(lone, 0o755)
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(lone))
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)

        with pytest.raises(SystemExit) as excinfo:
            build.resolve_llama_server()

        assert "stub" in str(excinfo.value)


class TestOverrideAndAbsence:
    """An explicit override is an instruction; a broken one is a hard error."""

    def test_missing_override_is_a_hard_failure(self, build, monkeypatch, tmp_path):
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(tmp_path / "nope"))
        with pytest.raises(SystemExit):
            build.resolve_llama_server()

    def test_present_override_wins_over_the_fixed_path(self, build, monkeypatch, tmp_path):
        source = _make_set(tmp_path / "override")
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", tmp_path / "missing.zip")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(source))
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        assert build.resolve_llama_server() == source

    def test_fixed_path_is_used_without_an_override(self, build, monkeypatch, tmp_path):
        source = _make_set(tmp_path / "fixed")
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_ASSET, raising=False)
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", source)
        assert build.resolve_llama_server() == source

    def test_no_pinned_source_warns_loudly_and_bundles_nothing(
        self, build, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_ASSET, raising=False)
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", tmp_path / "absent.zip")

        assert build.resolve_llama_server() is None

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert build.ENV_LLAMA_SERVER_ASSET in out  # names the env override to set
        assert "absent.zip" in out                # names the fixed path it checked

    def test_github_actions_annotation_is_emitted(self, build, monkeypatch, tmp_path, capsys):
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_ASSET, raising=False)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", tmp_path / "absent.zip")

        build.resolve_llama_server()

        assert "::warning::" in capsys.readouterr().out


class TestArchiveDigest:
    """A hash is verified when supplied; a ZIP defaults to the pinned digest."""

    def test_a_zip_without_a_supplied_digest_must_match_the_pinned_asset(
        self, build, monkeypatch, tmp_path
    ):
        archive = _make_zip(tmp_path / "custom.zip")
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(archive))
        monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")

        with pytest.raises(SystemExit) as excinfo:
            build.resolve_llama_server()

        assert pin.ASSET_SHA256 in str(excinfo.value)

    def test_a_supplied_digest_verifies_and_extracts_the_archive(
        self, build, monkeypatch, tmp_path
    ):
        archive = _make_zip(tmp_path / "custom.zip")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(archive))
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_SHA256, digest)
        monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")

        resolved = build.resolve_llama_server()

        for name in pin.REQUIRED_MEMBERS:
            assert (resolved / name).is_file(), name

    def test_a_supplied_digest_that_does_not_match_is_a_hard_failure(
        self, build, monkeypatch, tmp_path
    ):
        archive = _make_zip(tmp_path / "custom.zip")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(archive))
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_SHA256, "0" * 64)
        monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")

        with pytest.raises(SystemExit):
            build.resolve_llama_server()

    def test_a_supplied_digest_cannot_verify_a_directory(self, build, monkeypatch, tmp_path):
        source = _make_set(tmp_path / "set")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(source))
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_SHA256, "0" * 64)

        with pytest.raises(SystemExit):
            build.resolve_llama_server()

    def test_a_traversing_archive_member_is_refused(self, build, monkeypatch, tmp_path):
        archive = tmp_path / "evil.zip"
        _make_zip(archive)
        with zipfile.ZipFile(archive, "a") as zf:
            zf.writestr("../escaped.dll", b"x")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_ASSET, str(archive))
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_SHA256, digest)
        monkeypatch.setattr(build, "BUILD_DIR", tmp_path / "build")

        with pytest.raises(SystemExit) as excinfo:
            build.resolve_llama_server()

        assert "escapes" in str(excinfo.value)
