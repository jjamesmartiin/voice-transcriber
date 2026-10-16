"""Filesystem paths the app resolves without speaking HTTP.

``find_repo_root`` and ``get_data_dir`` started life inside
:mod:`voice_transcriber.model_download` because the installer needed them, but
they are pure path helpers. Living there meant any module that only wanted a
path - ``t2`` (which resolves the config file at import time), ``dictionary``,
``post_processor`` - had to import the downloader and, with it, ``urllib`` and
``http.client``. Splitting them into this leaf module keeps the offline-first
rule: importing a path helper pulls in ``os`` and nothing else.

``model_download`` re-exports these names, so callers that already ask that
module for a path keep working unchanged.
"""
import os


def _data_dir():
    """The per-user data root, without creating anything (see get_data_dir)."""
    if os.environ.get("XDG_DATA_HOME"):
        return os.path.join(os.environ["XDG_DATA_HOME"], "vt")
    if os.name == "posix":
        return os.path.join(os.path.expanduser("~"), ".local", "share", "vt")
    # Windows: Local, not the roaming profile. This directory holds the
    # multi-gigabyte weight cache, which must not sync between machines in a
    # domain. It mirrors ``t2.get_data_dir()``, which
    # tests/shared/test_crossplatform_runtime.py pins to Local; the installer and
    # the app must resolve the same place, or one caches weights where the other
    # never looks.
    user_home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(user_home, "AppData", "Local", "vt")


def get_data_dir():
    """Per-user data dir (mirrors t2.get_data_dir without importing the app)."""
    d = _data_dir()
    os.makedirs(d, exist_ok=True)
    return d


def _per_user_model_dir(subdir):
    """Per-user install path for a model subdir, without creating anything."""
    return os.path.join(_data_dir(), "models", subdir)


def _has_repo_marker(directory):
    """True when *directory* holds a checkout marker.

    The marker is a `.git` directory in a normal clone and a `.git` file holding
    ``gitdir: <path>`` in a linked worktree (``git worktree add``) or a
    submodule. An absent marker is normal for the Nix store, an AppImage and a
    pip-style install, which are read-only and use a per-user data dir instead.
    """
    return os.path.isdir(os.path.join(directory, ".git")) or os.path.isfile(
        os.path.join(directory, ".git")
    )


def find_repo_root():
    """Return the voice-transcriber checkout root when running from one.

    Walks up from this file looking for the repository marker (`.git`: a
    directory in a clone, a file in a linked worktree). When the app runs from
    the Nix store, an AppImage, or a pip-style install there is no marker, so
    this returns None and the installer falls back to the per-user data dir
    (those install locations are read-only).
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if _has_repo_marker(d):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None
