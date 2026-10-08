#!/usr/bin/env python3
"""Structural pins for the POSIX verb entry points (Linux / macOS / Nix).

One verb = one script under `scripts/`. The script detects the toolchain and
execs `platforms/<toolchain>/<verb>.sh`. The Nix toolchain uses the flake; the native
toolchains (linux/macos) bootstrap with the OS's package manager and dispatch to
the one shared runner, `tools/vt_dev.py`.

These file reads run on every platform, so the shape cannot drift on a machine
that never executes the shell.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = REPO_ROOT / "platforms" / "common" / "common.sh"
VT_DEV = REPO_ROOT / "tools" / "vt_dev.py"

VERBS = ("setup", "run", "test", "build", "clean")
TOOLCHAINS = ("nix", "linux", "macos")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSharedCore:
    def test_common_sh_defines_the_bootstrap_helpers(self):
        text = _read(COMMON_SH)
        for fn in ("vt_repo_root", "vt_find_python", "vt_venv_python",
                   "vt_detect_toolchain", "vt_dev_python", "vt_run_dev",
                   "vt_require_venv"):
            assert f"{fn}()" in text, fn

    def test_runner_defines_every_verb(self):
        text = _read(VT_DEV)
        for verb in VERBS:
            assert f'"{verb}":' in text, verb

    def test_common_sh_does_not_reimplement_the_verbs(self):
        # The shared shell must stay bootstrap-only: no venv/pip/model logic.
        text = _read(COMMON_SH)
        assert "pip install" not in text
        assert "python -m venv" not in text


class TestScriptDispatchers:
    def test_one_script_per_verb(self):
        for verb in VERBS:
            assert (REPO_ROOT / "scripts" / f"{verb}.sh").is_file(), verb

    def test_each_dispatches_by_toolchain(self):
        for verb in VERBS:
            text = _read(REPO_ROOT / "scripts" / f"{verb}.sh")
            assert "vt_detect_toolchain" in text, verb
            assert f"../platforms/$TOOLCHAIN/{verb}.sh" in text, verb


class TestPlatformVerbs:
    def test_every_toolchain_has_every_verb(self):
        for toolchain in TOOLCHAINS:
            for verb in VERBS:
                path = REPO_ROOT / "platforms" / toolchain / f"{verb}.sh"
                assert path.is_file(), (toolchain, verb)

    def test_native_verbs_dispatch_to_the_one_runner(self):
        for toolchain in ("linux", "macos"):
            for verb in VERBS:
                text = _read(REPO_ROOT / "platforms" / toolchain / f"{verb}.sh")
                assert "common/common.sh" in text, (toolchain, verb)
                assert f'vt_run_dev "$REPO" {verb}' in text, (toolchain, verb)

    def test_run_and_test_require_a_venv(self):
        # run/test never install - that is setup's job.
        for toolchain in ("linux", "macos"):
            for verb in ("run", "test"):
                text = _read(REPO_ROOT / "platforms" / toolchain / f"{verb}.sh")
                assert "vt_require_venv" in text, (toolchain, verb)

    def test_nix_verbs_use_nix(self):
        nix = REPO_ROOT / "platforms" / "nix"
        assert "nix run" in _read(nix / "run.sh")
        assert "nix develop" in _read(nix / "setup.sh")
        assert "nix develop" in _read(nix / "test.sh")
        assert "nix build" in _read(nix / "build.sh")
        assert "setup --no-deps" in _read(nix / "setup.sh")
