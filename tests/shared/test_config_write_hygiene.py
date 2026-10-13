#!/usr/bin/env python3
"""Write hygiene for the app config: atomic, uniquely named, owner-only.

The settings file is the one file a user cannot reconstruct from git, so a crash
mid-write, a second engine instance, or a truncated file must not be able to
silently destroy it.
"""

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

import t2


def _point_at(monkeypatch, config: Path) -> None:
    monkeypatch.setattr(t2, "get_config_file", lambda: config)
    t2.load_audio_config()


def test_config_temp_name_is_unique_per_write(tmp_path, monkeypatch):
    """Two engine instances (or two saves) must not share one temp file name.

    Regression: the temp was the fixed ``config.yaml.tmp``, so one writer could
    clobber the other's half-written file between its write and its rename.
    """
    config = tmp_path / "config.yaml"
    config.write_text("is_muted: true\n")
    _point_at(monkeypatch, config)

    real_replace = os.replace
    seen = []

    def spy(src, dst):
        seen.append(Path(src).name)
        return real_replace(src, dst)

    monkeypatch.setattr(t2.os, "replace", spy)

    t2.save_audio_config()
    t2.save_audio_config()

    assert len(seen) == 2
    assert seen[0] != "config.yaml.tmp", "the config temp name must not be fixed"
    assert seen[0] != seen[1], "two writers must not share one temp name"


def test_config_write_fsyncs_before_the_rename_and_is_owner_only(tmp_path, monkeypatch):
    """The bytes must reach disk before the rename, and the config is 0600.

    Regression: no ``fsync`` (a power loss could leave a zero-length or stale
    config after the rename was already durable), and the file was written under
    the umask, so it was world-readable.
    """
    config = tmp_path / "config.yaml"
    config.write_text("is_muted: true\n")
    _point_at(monkeypatch, config)

    synced = []
    monkeypatch.setattr(t2.os, "fsync", lambda fd: synced.append(fd))

    t2.save_audio_config()

    assert synced, "the config temp must be fsynced before os.replace"
    # File modes are a POSIX concept: CPython reports fixed 0o666/0o444 on Windows.
    if os.name != "nt":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_an_existing_empty_config_is_treated_as_damaged(tmp_path, monkeypatch):
    """An existing zero-byte config is damage, not "no settings".

    Regression: ``_read_config_file`` returned ``({}, None)`` for an empty file,
    so the truncation the temp+rename fix exists for was never backed up — the
    next save overwrote the user's settings with defaults and kept nothing.
    """
    config = tmp_path / "config.yaml"
    config.write_text("")  # a truncating write that got as far as creating the file
    _point_at(monkeypatch, config)

    t2.IS_MUTED = True
    t2.save_audio_config()

    backups = list(tmp_path.glob("config.yaml.corrupt-*"))
    assert len(backups) == 1, "an empty config must be preserved, not silently replaced"
    assert backups[0].read_text() == ""

    import yaml
    assert yaml.safe_load(config.read_text())["is_muted"] is True


@pytest.mark.parametrize("bad_content", ["- item1\n- item2\n", "just a scalar\n", "123\n"])
def test_non_mapping_config_is_backed_up_before_save(tmp_path, monkeypatch, bad_content):
    config = tmp_path / "config.yaml"
    config.write_text(bad_content)
    _point_at(monkeypatch, config)

    t2.IS_MUTED = True
    t2.save_audio_config()

    backups = list(tmp_path.glob("config.yaml.corrupt-*"))
    assert len(backups) == 1, f"bad config ({bad_content!r}) must be preserved, not silently replaced"
    assert backups[0].read_text() == bad_content



@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_the_data_dir_is_owner_only_when_created(tmp_path, monkeypatch):
    """The per-user data dir is created 0700, not world-traversable."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    data_dir = t2.get_data_dir()

    assert data_dir == tmp_path / "vt"
    assert stat.S_IMODE(data_dir.stat().st_mode) == 0o700
