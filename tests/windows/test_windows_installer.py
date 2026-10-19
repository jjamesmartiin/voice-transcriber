#!/usr/bin/env python3
"""Structural pins for the single-file Windows installer (Inno Setup).

`installer.iss` cannot be compiled on the Linux CI runners or on a contributor's
Linux box, and this repo has no Windows host to verify it on by hand, so the
things that would silently ship a broken installer are pinned here instead: a
name that no longer matches what PyInstaller produces, a version that was never
passed, an AppId that changes (two installs instead of an upgrade), and an
uninstaller that would take ~4 GB of downloaded weights with it.

First real verification is the `windows-latest` release job itself.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WIN_DIR = REPO_ROOT / "platforms" / "windows"
ISS = WIN_DIR / "installer.iss"
BUILD_OFFLINE = WIN_DIR / "build_offline.py"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestInstallerScript:
    def test_it_packages_the_bundle_pyinstaller_actually_builds(self):
        """The exe name and dist dir are duplicated, so pin them together."""
        iss = _read(ISS)
        builder = _read(BUILD_OFFLINE)

        built_name = re.search(r"--name=(\w+)", builder).group(1)
        assert f'#define AppExeName "{built_name}.exe"' in iss, (
            "installer.iss names an exe PyInstaller does not build")
        assert f'DIST_DIR / "{built_name}"' in builder, (
            "the dist layout the installer sources from moved")
        assert f'#define DistDir "..\\..\\dist\\{built_name}"' in iss

    def test_it_refuses_to_build_without_a_version(self):
        """A missing /DAppVersion would otherwise ship an installer called 0.0."""
        iss = _read(ISS)
        assert "#ifndef AppVersion" in iss
        assert "#error" in iss
        assert "AppVersion={#AppVersion}" in iss

    def test_upgrades_are_upgrades_not_second_copies(self):
        """AppId is the identity Windows keys upgrades on; a new one per release
        leaves the old version installed side by side."""
        iss = _read(ISS)
        app_id = re.search(r"^AppId=(.+)$", iss, re.M).group(1).strip()
        assert app_id.startswith("{{") and app_id.endswith("}")
        assert "Never change it" in iss

    def test_it_installs_per_user_without_admin(self):
        iss = _read(ISS)
        assert "PrivilegesRequired=lowest" in iss
        assert "DefaultDirName={localappdata}" in iss

    def test_autostart_task_and_registry_run_key(self):
        """Autostart on boot should be an optional task writing to HKCU Run."""
        iss = _read(ISS)
        assert 'Name: "autostart"' in iss
        assert r'Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"' in iss
        assert 'ValueName: "{#AppName}"' in iss
        assert 'Tasks: autostart' in iss
        assert 'Flags: uninsdeletevalue' in iss

    def test_uninstall_leaves_user_data_alone(self):
        """Weights (~4 GB), config and logs live outside {app}; a hand-placed
        models\\ directory lives inside it. [UninstallDelete] would add deletes
        that Inno does not do by default."""
        iss = _read(ISS)
        # A section *line*, not the mention in the comment explaining why it is absent.
        assert not re.search(r"^\[UninstallDelete\]", iss, re.M)
        assert "Deliberately no [UninstallDelete]" in iss


class TestReleaseWiring:
    def test_the_release_job_compiles_and_uploads_it(self):
        workflow = _read(RELEASE_WORKFLOW)
        assert "installer.iss" in workflow
        assert "ISCC.exe" in workflow
        assert "Inno Setup 6.3+" in workflow  # fails loudly if ISCC is missing
        assert "./dist/installer/*-setup.exe" in workflow
        assert "fail_on_unmatched_files: true" in workflow

    def test_the_asset_name_and_installer_output_agree(self):
        """A rename on one side only would publish nothing, or publish under a
        name no doc mentions."""
        out_name = re.search(r"^OutputBaseFilename=(.+)$", _read(ISS), re.M).group(1)
        assert out_name == "VoiceTranscriber-windows-x86_64-setup"

    def test_the_version_comes_from_the_app_not_the_tag(self):
        """The filename is unversioned (stable /releases/latest/download link),
        so the *contents* have to carry the version."""
        workflow = _read(RELEASE_WORKFLOW)
        assert "import voice_transcriber" in workflow
        assert "/DAppVersion=" in workflow
