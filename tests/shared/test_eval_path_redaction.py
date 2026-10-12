#!/usr/bin/env python3
"""The eval scorers must redact machine paths in the generator, not by hand.

``3ee6f84`` sanitized ``eval/results.json`` and ``eval/diarization_results.json``
by editing the committed files. Re-running either scorer re-introduced
``/home/<user>/...`` because the generators still recorded absolute paths, and
``score_diarization._safe_relpath`` returned the **absolute** path when
``os.path.relpath`` raised -- exactly the Windows cross-drive case, so the
redaction silently failed there. These tests pin the generator-side fix. See
``docs/audit-2026-10-11.md`` 3.4.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULES = {
    "score": REPO_ROOT / "eval" / "score.py",
    "diarization": REPO_ROOT / "eval" / "score_diarization.py",
}


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(f"eval_{name}_redaction", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def module():
    # Function-scoped: ``load_app_config`` mutates module global state, and a
    # fresh module per test keeps these assertions order-independent.
    return {name: _load(name, path) for name, path in MODULES.items()}


@pytest.fixture
def forbid_relpath(monkeypatch):
    """Make every ``os.path.relpath`` call behave like the Windows cross-drive case."""
    def _raise(*_args, **_kwargs):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(os.path, "relpath", _raise)


@pytest.mark.parametrize("name", sorted(MODULES))
def test_safe_relpath_fallback_is_never_absolute(module, name, forbid_relpath):
    # Use an absolute path for the *running* platform. A Windows-style
    # ``D:\\...`` string is not considered absolute by ``os.path.isabs`` on
    # POSIX, so a platform-neutral input would let the old ``str(path)``
    # fallback slip through on Linux.
    absolute = ("D:\\somewhere\\machine\\file.txt" if os.name == "nt"
                else "/home/alice/machine/file.txt")
    out = module[name]._safe_relpath(absolute)
    assert not os.path.isabs(out)
    assert out == module[name]._REDACTED_PATH


@pytest.mark.parametrize("name", sorted(MODULES))
def test_safe_relpath_still_relativises(module, name):
    rel = module[name]._safe_relpath("/tmp/root/sub/file", "/tmp/root")
    assert rel == os.path.join("sub", "file")


def test_score_env_records_basenames_for_path_values(module, monkeypatch):
    score = module["score"]
    monkeypatch.setenv(
        "PYTHONPATH", os.pathsep.join(["/home/alice/repo/src", "/opt/shared/lib"]))
    monkeypatch.setenv("VT_MODEL_DIR", "/home/alice/repo/models/cohere")
    monkeypatch.setenv("VT_INT8_DYNAMIC", "1")

    env = score.recorded_env()

    assert env["PYTHONPATH"] == os.pathsep.join(["src", "lib"])
    assert env["VT_MODEL_DIR"] == "cohere"
    assert env["VT_INT8_DYNAMIC"] == "1"  # not path-like: recorded verbatim


def test_diarization_env_records_basenames_for_path_values(module, monkeypatch):
    dia = module["diarization"]
    monkeypatch.setenv("PYTHONPATH", "/home/alice/repo/src")
    monkeypatch.setenv("VT_DIARIZATION_MODELS", "/home/alice/.local/share/vt/models/diarization")
    monkeypatch.setenv("VT_MODEL_DIR", "/home/alice/repo/models/cohere")

    env = dia.recorded_env()

    assert env["PYTHONPATH"] == "src"
    assert env["VT_DIARIZATION_MODELS"] == "diarization"
    assert env["VT_MODEL_DIR"] == "cohere"


@pytest.mark.parametrize("name", sorted(MODULES))
def test_reproduce_command_does_not_leak_an_absolute_path(module, name, monkeypatch):
    mod = module[name]
    monkeypatch.setenv("PYTHONPATH", "/home/alice/repo/src")
    monkeypatch.setenv("VT_MODEL_DIR", "/home/alice/repo/models/cohere")
    monkeypatch.setattr(mod.sys, "argv", [f"eval/{name}.py", "--limit", "1"])

    cmd = mod._reproduce_command()

    assert "/home/alice" not in cmd
    assert "PYTHONPATH=src" in cmd


def test_score_config_source_and_error_are_redacted(module, monkeypatch):
    score = module["score"]
    config_path = os.path.join(score.REPO_ROOT, "config", "config.yaml")
    import importlib
    importlib.import_module("yaml")  # ensure the import below opens nothing

    def _missing(*_args, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", config_path)

    monkeypatch.setattr("builtins.open", _missing)

    score.load_app_config()

    source = score._CONFIG_SOURCE
    assert source["path"] == os.path.join("config", "config.yaml")
    assert not os.path.isabs(source["path"])
    assert score.REPO_ROOT not in source["error"]
    assert config_path not in source["error"]
