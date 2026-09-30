#!/usr/bin/env python3
"""Structural pins for the native-Windows setup / test launchers.

The PowerShell scripts cannot execute on the Linux CI runners, and the Windows
CI job installs pytest directly (`.github/workflows/ci.yml`) instead of going
through `setup.ps1`. Without these pins the launcher wiring could regress
silently: CI would stay green while a fresh clone's `run.bat`/`test.ps1` broke.

Real end-to-end verification still requires a Windows host (see TODO.md).
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIN_DIR = REPO_ROOT / "platforms" / "windows"

DEV_REQUIREMENTS = WIN_DIR / "requirements-dev.txt"
SETUP_PS1 = WIN_DIR / "setup.ps1"
RUN_PS1 = WIN_DIR / "run.ps1"
TEST_PS1 = REPO_ROOT / "test.ps1"
RUN_BAT = REPO_ROOT / "run.bat"
WIN_RUN_BAT = WIN_DIR / "run.bat"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestDevRequirements:
    def test_exists_and_pins_the_test_tooling(self):
        assert DEV_REQUIREMENTS.is_file()
        text = _read(DEV_REQUIREMENTS).lower()
        # Exact pins, matching the repository's fully-pinned requirements.txt.
        assert "pytest==" in text
        assert "pyinstaller==" in text


class TestSetupInstallsDevTooling:
    def test_setup_ps1_installs_dev_requirements(self):
        text = _read(SETUP_PS1)
        assert "requirements-dev.txt" in text
        # It must actually pip-install the file, not merely mention it.
        assert "-r $DevReqFile" in text

    def test_setup_ps1_keeps_installing_runtime_requirements(self):
        assert "-r $ReqFile" in _read(SETUP_PS1)

    def test_setup_ps1_can_auto_install_python_via_winget(self):
        text = _read(SETUP_PS1)
        assert "Resolve-HostPython" in text
        assert "winget install" in text


class TestLaunchersSelfHeal:
    def test_run_ps1_installs_dev_tooling_for_test_and_build(self):
        text = _read(RUN_PS1)
        assert "requirements-dev.txt" in text
        assert "Ensure-DevTooling" in text
        # Both the test and build paths must gate on the helper.
        assert 'Ensure-DevTooling -Module "pytest"' in text
        assert 'Ensure-DevTooling -Module "PyInstaller"' in text

    def test_test_ps1_is_a_shim_over_the_launcher(self):
        text = _read(TEST_PS1)
        assert "run.ps1" in text
        assert "test @args" in text


class TestLauncherFlags:
    """The escape hatches that make `run.bat` usable without installing."""

    def test_run_ps1_offers_the_skip_and_relocate_flags(self):
        text = _read(RUN_PS1)
        for flag in ("--no-install", "--no-dev", "--no-model", "--model-dir", "--venv"):
            assert flag in text, flag

    def test_install_is_gated_by_a_single_switch(self):
        text = _read(RUN_PS1)
        # --no-model and --model-dir must drive the env vars the model
        # installer already reads, not reinvent the lookup.
        assert "VT_AUTO_DOWNLOAD_MODEL" in text
        assert "VT_MODEL_DIR" in text
        # verify/fetch/clean must be side-effect-free: one switch gates venv
        # creation and the pip install block, and it excludes those modes.
        assert "if (-not (Test-Path $venvPython) -and $InstallAllowed)" in text
        assert "if ($InstallAllowed) {" in text
        assert '$Mode -eq "run" -or $Mode -eq "test" -or $Mode -eq "build"' in text

    def test_run_ps1_has_verify_and_fetch_modes(self):
        text = _read(RUN_PS1)
        assert '$Mode -eq "verify"' in text
        # verify must defer model completeness to the model module.
        assert "--verify-only" in text
        assert '$Mode -eq "fetch"' in text
        assert "model_download.py" in text

    def test_run_ps1_has_a_clean_mode_that_keeps_weights_by_default(self):
        text = _read(RUN_PS1)
        assert '$Mode -eq "clean"' in text
        # Weights are only removed when explicitly asked for.
        assert '$ModeArgs -contains "--models"' in text
        assert "Remove-Item" in text

    def test_setup_ps1_accepts_no_dev(self):
        text = _read(SETUP_PS1)
        assert "--no-dev" in text
        assert "if ($NoDev) {" in text

    def test_no_redundant_aliases(self):
        # One spelling per option (AGENTS.md: no duplicate aliases). If a
        # shorthand is reintroduced it must be documented here and in the
        # Windows guide, so pin its absence instead.
        run_text = _read(RUN_PS1)
        setup_text = _read(SETUP_PS1)
        for alias in ("--skip-deps", "--skip-model"):
            assert alias not in run_text, alias
        assert "--skip-dev" not in setup_text
        # The mode is `verify`; `check` must not linger as a hidden alias.
        assert '"check"' not in run_text


class TestBatchPauseOnFailure:
    """A double-clicked run.bat must not vanish before the error is readable."""

    def test_run_bat_pauses_on_nonzero_exit(self):
        for path in (RUN_BAT, WIN_RUN_BAT):
            text = _read(path).lower()
            assert "if errorlevel 1" in text, path
            assert "pause" in text, path


class TestWindowsOnlyLauncherFixes:
    """Pins for defects only a real Windows host can show.

    All four were measured by driving a Windows 11 guest over RDP with Windows
    PowerShell 5.1 - the interpreter run.bat actually invokes. The Linux CI runner
    cannot execute these scripts at all, so pin the shape of the fix instead
    (see TODO.md).
    """

    def test_run_ps1_ends_by_propagating_the_app_exit_code(self):
        # powershell.exe reports 0 for a script that terminates "normally", so
        # run.bat's `if errorlevel 1` never saw a crashed app: measured on Windows,
        # `sys.exit(3)` inside main.py reached run.bat as 0.
        assert _read(RUN_PS1).rstrip().endswith("exit $LASTEXITCODE")

    def test_batch_error_banner_avoids_parentheses(self):
        # "(if enabled)" closed the `if errorlevel 1 (` block early, leaving a stray
        # `:` command: cmd printed ": was unexpected at this time.", aborted the
        # batch and never reached `pause` - on every invocation, whatever the code.
        for path in (RUN_BAT, WIN_RUN_BAT):
            text = _read(path)
            assert "Log [if enabled]:" in text, path
            assert "(if enabled)" not in text, path

    def test_verify_deps_check_runs_from_a_file(self):
        # PS 5.1 drops embedded double quotes when a multi-line here-string is
        # handed to a native command, so `python -c $checkScript` arrived as
        # invalid source (SyntaxError) and verify reported missing dependencies on
        # every Windows machine, however complete the install was.
        text = _read(RUN_PS1)
        assert "& $pythonExe -c $checkScript" not in text
        assert "& $pythonExe $checkFile" in text

    def test_report_modes_force_utf8_stdout(self):
        # model_download.py prints U+2713/U+2715; with stdout redirected (or piped)
        # Windows' cp1252/locale encoding raised UnicodeEncodeError instead of
        # reporting the model verdict.
        text = _read(RUN_PS1)
        assert ('if ($Mode -eq "verify" -or $Mode -eq "fetch") '
                '{ $env:PYTHONIOENCODING = "utf-8" }') in text
