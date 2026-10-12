#!/usr/bin/env python3
"""Structural pins for the pinned ``llama-server.exe`` resolution in the build.

The Windows PyInstaller build bundles a formatter binary into the released zip.
It must take that binary only from an explicit, pinned source
(``VT_LLAMA_SERVER_EXE`` or the fixed ``PROJECT_ROOT/llama-server.exe``) and
**never** from ``PATH``: on Windows an empty or ``.`` PATH component means the
CWD, so the build machine's environment -- or the build directory itself --
would otherwise decide which binary ships inside the release (CWE-427,
uncontrolled search path). See ``docs/audit-2026-10-11.md`` 3.5.

The build itself cannot run here (no Windows/PyInstaller), so these tests pin the
resolution *behaviour* and the absence of the PATH lookup structurally.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILD_PATH = REPO_ROOT / "platforms" / "windows" / "build_offline.py"


def _load_build():
    spec = importlib.util.spec_from_file_location("build_offline", BUILD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build():
    return _load_build()


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
            raise AssertionError("build must not consult PATH for llama-server.exe")

        monkeypatch.setattr(build.shutil, "which", _forbidden)
        monkeypatch.setattr(build.os, "access", lambda *_a, **_k: True)

        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_EXE, str(pinned))
        assert build.resolve_llama_server() == pinned

        monkeypatch.delenv(build.ENV_LLAMA_SERVER_EXE, raising=False)
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", pinned)
        assert build.resolve_llama_server() == pinned


class TestOverride:
    """An explicit override is an instruction; a broken one is a hard error."""

    def test_missing_override_is_a_hard_failure(self, build, monkeypatch, tmp_path):
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_EXE, str(tmp_path / "nope.exe"))
        with pytest.raises(SystemExit):
            build.resolve_llama_server()

    def test_non_executable_override_is_a_hard_failure(self, build, monkeypatch, tmp_path):
        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_EXE, str(pinned))
        # Windows ignores the execute bit, so simulate the POSIX answer rather
        # than depend on platform semantics.
        monkeypatch.setattr(build.os, "access", lambda *_a, **_k: False)
        with pytest.raises(SystemExit):
            build.resolve_llama_server()

    def test_present_override_wins(self, build, monkeypatch, tmp_path):
        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        monkeypatch.setattr(build.os, "access", lambda *_a, **_k: True)
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_EXE, str(pinned))
        assert build.resolve_llama_server() == pinned


class TestFixedPathAndAbsence:
    def test_fixed_repository_path_is_used_without_an_override(self, build, monkeypatch, tmp_path):
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_EXE, raising=False)
        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", pinned)
        assert build.resolve_llama_server() == pinned

    def test_no_pinned_source_warns_loudly_and_bundles_nothing(
        self, build, monkeypatch, tmp_path, capsys
    ):
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_EXE, raising=False)
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", tmp_path / "absent.exe")

        assert build.resolve_llama_server() is None

        out = capsys.readouterr().out
        assert "WARNING" in out
        assert build.ENV_LLAMA_SERVER_EXE in out  # names the env override to set
        assert "absent.exe" in out                # names the fixed path it checked

    def test_github_actions_annotation_is_emitted(self, build, monkeypatch, tmp_path, capsys):
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_EXE, raising=False)
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setattr(build, "PINNED_LLAMA_SERVER", tmp_path / "absent.exe")

        build.resolve_llama_server()

        assert "::warning::" in capsys.readouterr().out


class TestDigest:
    """A digest is honoured when supplied; none is invented when it is not."""

    def test_supplied_digest_match_passes(self, build, monkeypatch, tmp_path, capsys):
        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        digest = hashlib.sha256(b"pinned").hexdigest()
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_SHA256, digest)

        build.verify_llama_server_sha256(pinned)

        assert digest in capsys.readouterr().out

    def test_supplied_digest_mismatch_is_a_hard_failure(self, build, monkeypatch, tmp_path):
        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        monkeypatch.setenv(build.ENV_LLAMA_SERVER_SHA256, "0" * 64)
        with pytest.raises(SystemExit):
            build.verify_llama_server_sha256(pinned)

    def test_no_digest_means_no_verification(self, build, monkeypatch, tmp_path):
        pinned = tmp_path / "llama-server.exe"
        pinned.write_bytes(b"pinned")
        monkeypatch.delenv(build.ENV_LLAMA_SERVER_SHA256, raising=False)
        build.verify_llama_server_sha256(pinned)  # must not raise
