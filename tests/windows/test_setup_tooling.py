#!/usr/bin/env python3
"""Structural pins for the Windows verb entry points.

The PowerShell scripts cannot execute on the Linux CI runners, and the Windows
CI job installs pytest directly (`.github/workflows/ci.yml`) instead of going
through `setup.ps1`. Without these pins the launcher wiring could regress
silently: CI would stay green while a fresh clone's scripts broke.

Design (see AGENTS.md / docs/offline_install.md): the five verbs are separate
entry points, but the *body* of each lives in one shared, stdlib-only runner
(`tools/vt_dev.py`). The PowerShell scripts bootstrap only (find a Python,
enable long paths, force UTF-8) and dispatch to it. Real end-to-end verification
still requires a Windows host (see docs/TODO.md).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIN_DIR = REPO_ROOT / "platforms" / "windows"
COMMON_PS1 = REPO_ROOT / "platforms" / "common" / "common.ps1"
VT_DEV = REPO_ROOT / "tools" / "vt_dev.py"
SCRIPT_TEST_PS1 = REPO_ROOT / "scripts" / "test.ps1"

VERBS = ("setup", "run", "test", "build", "clean")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestSharedCore:
    """One place for the repeatable logic; every verb only bootstraps."""

    def test_common_ps1_defines_the_bootstrap_helpers(self):
        text = _read(COMMON_PS1)
        for fn in ("Write-Step", "Write-Success", "Write-Warn", "Write-Err",
                   "Get-RepoRoot", "Resolve-HostPython", "Get-LaunchPython"):
            assert f"function {fn}" in text, fn

    def test_verb_runner_exists_and_defines_every_verb(self):
        assert VT_DEV.is_file()
        text = _read(VT_DEV)
        for verb in VERBS:
            assert f'"{verb}":' in text, verb


class TestWindowsVerbScripts:
    def test_every_verb_has_a_ps1(self):
        for verb in VERBS:
            assert (WIN_DIR / f"{verb}.ps1").is_file(), verb

    def test_every_verb_sources_the_shared_core(self):
        for verb in VERBS:
            assert "common\\common.ps1" in _read(WIN_DIR / f"{verb}.ps1"), verb

    def test_every_verb_dispatches_to_the_runner(self):
        for verb in VERBS:
            text = _read(WIN_DIR / f"{verb}.ps1")
            assert "tools\\vt_dev.py" in text, verb
            assert f") {verb}" in text, verb

    def test_no_duplicated_logic_in_the_bootstrap(self):
        # venv / pip / model handling must live only in the Python runner.
        for verb in VERBS:
            text = _read(WIN_DIR / f"{verb}.ps1")
            assert "pip install" not in text, verb
            assert "-m venv" not in text, verb
            assert "Resolve-Path (Join-Path $ScriptDir" not in text, verb


class TestSetup:
    def test_setup_bootstraps_python_and_long_paths(self):
        text = _read(WIN_DIR / "setup.ps1")
        assert "winget install" in text
        assert "LongPathsEnabled" in text
        assert "Resolve-HostPython" in text

    def test_setup_forwards_the_skip_flags(self):
        text = _read(WIN_DIR / "setup.ps1")
        assert '"--no-dev"' in text
        assert '"--no-model"' in text


class TestRunVerb:
    def test_run_requires_an_environment(self):
        text = _read(WIN_DIR / "run.ps1")
        assert "Get-LaunchPython" in text
        assert "setup.bat" in text

    def test_run_ends_by_propagating_the_app_exit_code(self):
        # powershell.exe reports 0 for a script that terminates "normally", so
        # run.bat's `if errorlevel 1` would never see a crashed app.
        assert _read(WIN_DIR / "run.ps1").rstrip().endswith("exit $LASTEXITCODE")


class TestTestVerb:
    def test_test_forces_utf8_stdout(self):
        # model_download.py prints U+2713/U+2715; Windows' code page cannot encode
        # them, so a piped verify raised UnicodeEncodeError instead of reporting.
        assert '$env:PYTHONIOENCODING = "utf-8"' in _read(WIN_DIR / "test.ps1")


class TestScriptShims:
    def test_script_test_ps1_forwards_to_the_windows_verb(self):
        text = _read(SCRIPT_TEST_PS1)
        assert "platforms\\windows\\test.ps1" in text
        assert "@args" in text


class TestBatchPauseOnFailure:
    """A double-clicked .bat must not vanish before the error is readable."""

    def test_error_batches_pause_on_nonzero_exit(self):
        for name in ("run.bat", "test.bat", "build.bat", "clean.bat"):
            text = (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8").lower()
            assert "if errorlevel 1" in text, name
            assert "pause" in text, name

    def test_batch_error_banner_avoids_parentheses(self):
        # "(if enabled)" closed the `if errorlevel 1 (` block early, leaving a
        # stray `:` command; cmd aborted the batch and never reached `pause`.
        text = _read(REPO_ROOT / "scripts" / "run.bat")
        assert "Log [if enabled]:" in text
        assert "(if enabled)" not in text
