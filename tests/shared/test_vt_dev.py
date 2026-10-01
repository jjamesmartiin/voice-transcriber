#!/usr/bin/env python3
"""Unit tests for tools/vt_dev.py - the one shared verb runner.

Pure-logic tests only: no venv is created, nothing is installed, and the app is
never launched. They pin the contract that every platform script dispatches to.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VT_DEV = REPO_ROOT / "tools" / "vt_dev.py"


def _load():
    spec = importlib.util.spec_from_file_location("vt_dev_under_test", VT_DEV)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


vt_dev = _load()


def test_verbs_match_the_contract():
    assert set(vt_dev.VERBS) == {"setup", "run", "test", "build", "clean"}


def test_env_forces_utf8(monkeypatch):
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    assert vt_dev._env()["PYTHONIOENCODING"] == "utf-8"


def test_env_keeps_an_explicit_encoding(monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
    assert vt_dev._env()["PYTHONIOENCODING"] == "latin-1"


def test_split_run_args_forwards_a_control_verb():
    model_dir, venv, no_model, app = vt_dev._split_run_args(["status", "--json"])
    assert model_dir is None
    assert venv is None
    assert no_model is False
    assert app == ["status", "--json"]


def test_split_run_args_parses_the_launcher_flags():
    model_dir, venv, no_model, app = vt_dev._split_run_args(
        ["--model-dir", "D:/m", "--no-model", "start"])
    assert model_dir == "D:/m"
    assert no_model is True
    assert app == ["start"]


def test_split_run_args_venv_override_is_a_path():
    _, venv, _, _ = vt_dev._split_run_args(["--venv", "/tmp/v", "status"])
    assert venv == Path("/tmp/v")


def test_platform_tier_is_one_of_the_known_tiers():
    tier, name = vt_dev.platform_tier()
    assert tier in ("tests/windows", "tests/linux", "tests/macos", "tests/wsl")
    assert name


def test_nix_toolchain_ignores_the_repo_venv(monkeypatch, tmp_path):
    import os
    exe = tmp_path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(vt_dev, "DEFAULT_VENV", tmp_path)
    monkeypatch.delenv("VT_TOOLCHAIN", raising=False)
    assert vt_dev.venv_python() == exe
    monkeypatch.setenv("VT_TOOLCHAIN", "nix")
    assert vt_dev.venv_python() is None


def test_model_dest_uses_the_repo_when_writable():
    dest = vt_dev.model_dest()
    assert dest is not None
    assert dest.name == "cohere"
    assert dest.parent.name == "models"


def test_main_help_is_zero():
    assert vt_dev.main(["--help"]) == 0


def test_main_unknown_verb_is_usage_error():
    assert vt_dev.main(["definitely-not-a-verb"]) == 2


def test_run_help_exits_cleanly():
    with pytest.raises(SystemExit) as exc:
        vt_dev._split_run_args(["--help"])
    assert exc.value.code == 0
