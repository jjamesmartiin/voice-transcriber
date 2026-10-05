"""
First-run model acquisition from GitHub Releases — no Hugging Face required.

CohereLabs/cohere-transcribe-03-2026 is Apache-2.0 licensed, so the weights are
mirrored as split-xz GitHub Release assets (see scripts/prepare_model_release.py).
When the app needs the Cohere backend and no local copy exists, this module
downloads the parts, verifies their SHA-256, decompresses/concatenates them, and
installs them so the backend can load fully offline afterwards
(local_files_only=True).

Install location (see cohere_models_dir): when the app runs from a git
checkout of this repository, the weights unpack into <repo>/models/cohere so it
is obvious they belong to / came from this project; read-only installs (Nix
store, AppImage) fall back to ~/.local/share/vt/models/cohere. A SOURCE.json
provenance file is written next to the weights recording the origin repo,
release tag, sha256, license, and install date.

Pure stdlib: urllib for download, lzma for decompression, tarfile for extract.

Env knobs:
  VT_MODEL_DIR            explicit model install directory (overrides both)
  VT_AUTO_DOWNLOAD_MODEL  "0"/"false" disables automatic download
  VT_MODEL_RELEASE_BASE   base URL of the release assets (default: the
                          revision-keyed model bundle tag in
                          jjamesmartiin/voice-transcriber)
  XDG_DATA_HOME           (standard) relocates the per-user fallback for testing
"""
import hashlib
import lzma
import os
import re
import shutil
import socket
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

REPO_ID = "CohereLabs/cohere-transcribe-03-2026"
REVISION = "499888924f5f1313b48ab0686c8f3a94178a4709"
SAFETENSORS_SHA256 = "987bd3e141c7bfdb5a78f5db11397ee7737308357e6cc0a3f36a4979b158137a"

# Single source of truth for the publishing target: scripts/publish_model_bundle.sh
# reads these back out of this module so it cannot drift from the client.
REPO_SLUG = "jjamesmartiin/voice-transcriber"
_REPO_URL = f"https://github.com/{REPO_SLUG}"

# Weights are addressed by *model revision*, never by app release. REVISION is
# compiled into the binary and already names the asset files, so every app
# version resolves to the same bundle: publishing weights is a once-per-revision
# job, not a once-per-version one. Bumping REVISION renames the tag, which is
# what forces a fresh publish when (and only when) the weights actually change.
MODEL_BUNDLE_TAG = f"model-cohere-{REVISION[:12]}"
DEFAULT_RELEASE_BASE = f"{_REPO_URL}/releases/download/{MODEL_BUNDLE_TAG}"

# Minimal set whose presence means "a local copy exists and can be loaded".
_REQUIRED_LOCAL = [
    "config.json", "model.safetensors", "tokenizer.json",
    "modeling_cohere_asr.py", "processor_config.json",
]

_status_handler = None  # set by the app: fn(stage: str, pct: int, text: str)


def set_status_handler(fn):
    """Register a UI callback for download progress (optional)."""
    global _status_handler
    _status_handler = fn


def _notify(stage, pct, text):
    if _status_handler:
        try:
            _status_handler(stage, int(pct), text)
        except Exception:
            pass
    print(text, flush=True)


def get_data_dir():
    """Per-user data dir (mirrors t2.get_data_dir without importing the app)."""
    if os.environ.get("XDG_DATA_HOME"):
        d = os.path.join(os.environ["XDG_DATA_HOME"], "vt")
    elif os.name == "posix":
        d = os.path.join(os.path.expanduser("~"), ".local", "share", "vt")
    else:
        d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "vt")
    os.makedirs(d, exist_ok=True)
    return d


def find_repo_root():
    """Return the voice-transcriber checkout root when running from one.

    Walks up from this file looking for the repository marker (`.git`). When the
    app runs from the Nix store, an AppImage, or a pip-style install there is no
    marker, so this returns None and the installer falls back to the per-user
    data dir (those install locations are read-only).
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def cohere_models_dir():
    """Preferred writable install target for the Cohere model.

    Order: $VT_MODEL_DIR override -> <repo checkout>/models/cohere (so the
    weights live visibly inside the project it came from) -> per-user data dir
    (~/.local/share/vt/models/cohere) for read-only installs (Nix/AppImage).
    """
    override = os.environ.get("VT_MODEL_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for cand in [
            os.path.join(exe_dir, "models", "cohere"),
            os.path.join(exe_dir, "model", "cohere"),
            os.path.join(exe_dir, "models"),
            exe_dir,
        ]:
            if os.path.isdir(cand) and is_local_model_complete(cand):
                return cand
        if hasattr(sys, "_MEIPASS"):
            for cand in [
                os.path.join(sys._MEIPASS, "models", "cohere"),
                os.path.join(sys._MEIPASS, "models"),
                sys._MEIPASS,
            ]:
                if os.path.isdir(cand) and is_local_model_complete(cand):
                    return cand
        exe_models = os.path.join(exe_dir, "models", "cohere")
        if os.path.isdir(exe_models) or os.access(exe_dir, os.W_OK):
            return exe_models
    root = find_repo_root()
    if root:
        repo_dir = os.path.join(root, "models", "cohere")
        # writable now? (no side effects: don't create anything on lookup)
        if os.path.isdir(repo_dir):
            if os.access(repo_dir, os.W_OK):
                return repo_dir
        elif os.access(root, os.W_OK):
            return repo_dir
    return os.path.join(get_data_dir(), "models", "cohere")


def _write_provenance(dest, base_url, resolved_url=None, source_kind="remote"):
    """Write SOURCE.json next to the weights so their origin is unambiguous."""
    import datetime as _dt
    release_tag = None
    if resolved_url:
        m = re.search(r"/releases/download/([^/]+)/", resolved_url)
        if m:
            release_tag = m.group(1)
    info = {
        "model": REPO_ID,
        "revision": REVISION,
        "model_safetensors_sha256": SAFETENSORS_SHA256,
        "download_url_base": base_url,
        "release_tag": release_tag,
        "source_kind": source_kind,
        "license": "Apache-2.0 (see LICENSE and NOTICE in this directory)",
        "installed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "note": "Weights mirrored from the " + REPO_ID + " HF snapshot via the "
                "voice-transcriber GitHub release, so the app runs without a "
                "Hugging Face account.",
    }
    try:
        with open(os.path.join(dest, "SOURCE.json"), "w") as f:
            import json as _json
            _json.dump(info, f, indent=2)
            f.write("\n")
    except Exception as e:
        print(f"(could not write SOURCE.json provenance: {e})")


def _release_base():
    return os.environ.get("VT_MODEL_RELEASE_BASE", DEFAULT_RELEASE_BASE).rstrip("/")


def _release_candidates(primary):
    """Base URLs to try, most-preferred first, without duplicates.

    `primary` is whatever the caller asked for (an explicit base_url, the
    VT_MODEL_RELEASE_BASE override, or the revision-derived bundle tag). The
    canonical bundle is appended as a fallback so an explicit or relocated base
    degrades to the real one instead of failing.
    """
    out = []
    for cand in (primary, DEFAULT_RELEASE_BASE):
        c = (cand or "").rstrip("/")
        if c and c not in out:
            out.append(c)
    return out


def _asset_exists(url):
    """HEAD `url` to confirm a release asset is really there.

    The manifest is published before the parts it lists, so presence of
    SHA256SUMS says nothing about presence of the weights. Uses HEAD so the
    check costs two tiny requests rather than two gigabytes.
    """
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "vt-model-installer/1.0"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return 200 <= getattr(resp, "status", 200) < 300
        except urllib.error.HTTPError as e:
            # Some CDNs refuse HEAD outright; only a definite 404/410 means "absent".
            if e.code in (403, 405, 501):
                return True
            if e.code in (404, 410):
                return False
            last = e
        except (urllib.error.URLError, OSError, socket.timeout) as e:
            last = e
        if attempt == 1:
            time.sleep(1)
    print(f"Could not HEAD {url} ({last}); treating the asset as absent.", flush=True)
    return False


def is_local_model_complete(directory=None):
    """True when a loadable local copy already exists (offline-ready)."""
    directory = directory or cohere_models_dir()
    return all(os.path.exists(os.path.join(directory, f))
               for f in _REQUIRED_LOCAL)


def _http_get(url, dest, descr):
    """Stream `url` into `dest` with retries, returning bytes downloaded."""
    socket.setdefaulttimeout(60)
    last_err = None
    last_pct = -1
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vt-model-installer/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                done = 0
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            pct = int(100.0 * done / total)
                            # notify at 5% steps (both the UI handler and stdout)
                            if pct != last_pct and (pct % 5 == 0 or pct >= 100):
                                last_pct = pct
                                _notify("Downloading model", pct,
                                        f"Downloading {descr}: {done/1e9:.2f}/{total/1e9:.2f} GB")
            return done
        except (urllib.error.URLError, OSError, socket.timeout) as e:
            last_err = e
            print(f"Download attempt {attempt}/3 for {url} failed: {e}", flush=True)
            time.sleep(2 * attempt)
    raise RuntimeError(f"Failed to download {url}: {last_err}")


def _sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _parse_part_manifest(text, prefix):
    """Parse a SHA256SUMS body into {filename: sha256} for ``<prefix>.partN.xz``."""
    manifest = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            digest, name = line.split(None, 1)
        except ValueError:
            continue
        name = name.strip()
        if re.fullmatch(rf"{re.escape(prefix)}\.part\d+\.xz", name):
            manifest[name] = digest
    return manifest


def _fetch_part_manifest(base_url, prefix):
    """Download the SHA256SUMS file.

    Returns (manifest, resolved_url) where manifest is {filename: sha256} for
    *.partN.xz entries and resolved_url is the final URL after redirects (used
    to record the concrete release tag in SOURCE.json).
    """
    sums_url = f"{base_url}/{prefix}.SHA256SUMS"
    data = None
    resolved_url = None
    last_err = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(sums_url, headers={"User-Agent": "vt-model-installer/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                resolved_url = resp.geturl()
                data = resp.read()
            break
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (404, 410):
                # Release asset missing (e.g. latest release does not mirror model weights):
                # fast-fail immediately so fallback candidates can be tried with zero delay.
                break
            print(f"Manifest attempt {attempt}/3 for {sums_url} failed: {e}", flush=True)
            time.sleep(2 * attempt)
        except (urllib.error.URLError, OSError, socket.timeout) as e:
            last_err = e
            print(f"Manifest attempt {attempt}/3 for {sums_url} failed: {e}", flush=True)
            time.sleep(2 * attempt)
    if data is None:
        raise RuntimeError(f"Failed to download {sums_url}: {last_err}")
    return _parse_part_manifest(data.decode("utf-8", "replace"), prefix), resolved_url


def _local_model_source():
    """Locate an on-disk split bundle to install from, without the network.

    Returns ``(path, explicit)``. ``VT_MODEL_SOURCE_DIR`` / ``VT_MODEL_BUNDLE``
    is an explicit operator override and is used as-is even if it does not
    exist, so a typo surfaces instead of silently downloading. A
    ``model-bundle`` directory next to the executable, next to the running
    AppImage (``$APPIMAGE`` / ``$APPDIR``) or the working directory is
    auto-detected, which is the USB-stick layout for airgapped installs.
    """
    override = (os.environ.get("VT_MODEL_SOURCE_DIR")
                or os.environ.get("VT_MODEL_BUNDLE") or "").strip()
    if override:
        return override, True
    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "model-bundle"),
    ]
    # A double-clicked AppImage runs from a read-only squashfs mount and its
    # ``sys.executable`` is the bundled interpreter, so look next to the
    # ``.AppImage`` file itself (the AppImage runtime exports both vars).
    for env in ("APPIMAGE", "APPDIR"):
        base = (os.environ.get(env) or "").strip()
        if not base:
            continue
        if env == "APPIMAGE":
            base = os.path.dirname(os.path.abspath(base))
        candidates.append(os.path.join(base, "model-bundle"))
    candidates.append(os.path.join(os.getcwd(), "model-bundle"))
    for cand in candidates:
        if os.path.isdir(cand):
            return cand, False
    return None, False


def _read_local_manifest(bundle_dir, prefix):
    """Find and parse the SHA256SUMS inside a local bundle directory."""
    names = [f"{prefix}.SHA256SUMS", "SHA256SUMS", "SHA256SUMS.txt"]
    names += sorted(n for n in os.listdir(bundle_dir) if n.endswith(".SHA256SUMS"))
    for name in names:
        path = os.path.join(bundle_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            manifest = _parse_part_manifest(f.read(), prefix)
        if manifest:
            return manifest
    return {}


def _extract_bundle_archive(path, dest):
    """Extract a .zip / .tar[.*] bundle into ``dest`` with path-traversal guards."""
    dest_path = os.path.abspath(dest)
    if path.lower().endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                target = os.path.abspath(os.path.join(dest, member))
                if not (target == dest_path or target.startswith(dest_path + os.sep)):
                    raise RuntimeError(f"Zip slip path traversal detected: {member!r}")
            zf.extractall(dest)
        return
    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest, filter="data")
            else:
                for member in tf.getmembers():
                    target = os.path.abspath(os.path.join(dest, member.name))
                    if not (target == dest_path or target.startswith(dest_path + os.sep)):
                        raise RuntimeError(f"Tar path traversal detected: {member.name!r}")
                tf.extractall(dest)
        return
    raise RuntimeError(
        f"unsupported model bundle archive {path!r} (use a directory, .zip or .tar)")


def install_from_local_bundle(source, dest=None, revision=REVISION):
    """Install the Cohere model from a local split bundle, with no network.

    ``source`` is either a directory holding ``cohere-transcribe-<rev>.partN.xz``
    plus ``SHA256SUMS``, or a ``.zip`` / ``.tar[.*]`` archive containing them.
    The parts are verified, decompressed, concatenated and extracted exactly as
    the network installer does, so an airgapped host needs no extra tooling.
    """
    dest = dest or cohere_models_dir()
    prefix = f"cohere-transcribe-{revision}"
    tmp_extract = None
    try:
        if os.path.isdir(source):
            bundle_dir = source
        elif os.path.isfile(source):
            tmp_extract = tempfile.mkdtemp(prefix="vt-model-bundle-")
            _extract_bundle_archive(source, tmp_extract)
            bundle_dir = tmp_extract
        else:
            raise FileNotFoundError(f"model bundle not found: {source}")

        manifest = _read_local_manifest(bundle_dir, prefix)
        if not manifest:
            raise RuntimeError(
                f"no {prefix}.partN.xz entries in any SHA256SUMS under {bundle_dir}")

        part_paths = {}
        for name in manifest:
            part = os.path.join(bundle_dir, name)
            if not os.path.isfile(part):
                raise FileNotFoundError(f"bundle {source!r} is missing {name}")
            part_paths[name] = part

        print(f"Installing Cohere model from local bundle {source} "
              f"({len(part_paths)} part(s))...", flush=True)
        return _assemble_parts(
            part_paths, manifest, dest,
            origin=os.path.abspath(source), source_kind="local",
        )
    finally:
        if tmp_extract:
            shutil.rmtree(tmp_extract, ignore_errors=True)


def _assemble_parts(part_paths, manifest, dest, origin,
                    resolved_url=None, source_kind="remote"):
    """Verify, decompress, concatenate and extract verified parts into ``dest``.

    ``part_paths`` maps each manifest filename to an existing local path; the
    caller owns those files and they are never deleted here. ``origin`` is
    recorded in SOURCE.json (a release URL online, a filesystem path offline).
    """
    part_names = sorted(manifest)
    dest_parent = os.path.dirname(os.path.abspath(dest))
    os.makedirs(dest_parent, exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="vt-model-install-", dir=dest_parent)
    try:
        tar_path = os.path.join(tmp_root, "model.tar")
        # Decompress each verified part straight into one concatenated tar.
        with open(tar_path, "wb") as tar_out:
            for name in part_names:
                part_path = part_paths[name]
                digest = _sha256_file(part_path)
                if digest != manifest[name]:
                    raise RuntimeError(
                        f"SHA-256 mismatch for {name}: expected {manifest[name]}, "
                        f"got {digest}. Aborting (no partial install).")
                print(f"Verified {name} (sha256 ok). Decompressing...", flush=True)
                with lzma.open(part_path, "rb") as fin:
                    shutil.copyfileobj(fin, tar_out, 1 << 20)

        # Extract into a staging dir, then atomically move into place.
        staging = os.path.join(tmp_root, "extracted")
        with tarfile.open(tar_path, "r") as tf:
            if hasattr(tarfile, "data_filter"):
                tf.extractall(staging, filter="data")
            else:
                tf.extractall(staging)
        weights = os.path.join(staging, "model.safetensors")
        got = _sha256_file(weights)
        if got != SAFETENSORS_SHA256:
            raise RuntimeError(
                f"Extracted model.safetensors sha256 {got} does not match the "
                f"expected {SAFETENSORS_SHA256}. Aborting.")
        if not is_local_model_complete(staging):
            raise RuntimeError("Extracted model directory is missing required files.")

        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.move(staging, dest)
        _write_provenance(dest, origin, resolved_url, source_kind=source_kind)
        print(f"Model installed at {dest}. Loading fully offline from now on.\n"
              f"Origin recorded in {os.path.join(dest, 'SOURCE.json')}.")
        return dest
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _print_manual_install_instructions(dest):
    """Print clear instructions for manual model installation."""
    print("\n" + "=" * 68)
    print("  TO MANUALLY INSTALL THE COHERE TRANSCRIBE MODEL:")
    print("  1. Download the split model parts and SHA256SUMS from GitHub:")
    print(f"     https://github.com/jjamesmartiin/voice-transcriber/releases/tag/{MODEL_BUNDLE_TAG}")
    print("  2. Decompress and extract the archive directly into:")
    print(f"     {dest}")
    print("     (Required: model.safetensors, config.json, tokenizer.json, etc.)")
    print("  Or set the VT_MODEL_DIR environment variable to your model folder.")
    print("=" * 68 + "\n", flush=True)


def ensure_local_cohere(dest=None, base_url=None, revision=REVISION):
    """
    Make sure a loadable local Cohere model exists at `dest`.

    Offline first: if a split bundle is present on disk (``VT_MODEL_SOURCE_DIR``
    or a ``model-bundle/`` directory next to the app), it is installed with no
    network. Otherwise the parts are fetched from the release assets.

    Returns the directory path on success (already there, or freshly installed),
    or None if auto-download is disabled / failed (caller may fall back to the
    Hugging Face path or surface an error).
    """
    dest = dest or cohere_models_dir()
    if is_local_model_complete(dest):
        return dest

    # Airgapped path: install from a bundle carried on disk before ever
    # touching the network.
    local_source, explicit = _local_model_source()
    if local_source:
        try:
            installed = install_from_local_bundle(local_source, dest=dest,
                                                  revision=revision)
        except Exception as e:
            print(f"Local model bundle install failed: {e}", flush=True)
            installed = None
        if installed:
            return installed
        if explicit:
            # The operator pointed us at a specific bundle; do not silently fall
            # back to the network - that is the whole point of airgap.
            return None

    auto_env = os.environ.get("VT_AUTO_DOWNLOAD_MODEL", "").strip().lower()
    if auto_env in ("0", "false", "no", "off"):
        print("VT_AUTO_DOWNLOAD_MODEL=0: skipping automatic model download.")
        _print_manual_install_instructions(dest)
        return None

    if auto_env not in ("1", "true", "yes", "always"):
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            try:
                print("\n" + "-" * 68)
                print("  Cohere Transcribe model weights are not installed.")
                print(f"  Target location: {dest}")
                print("  Download size: ~2.8 GB (~3.9 GB uncompressed on disk).")
                print("-" * 68)
                ans = input("  Would you like to download them automatically from GitHub now? [Y/n]: ").strip().lower()
                if ans and ans not in ("y", "yes"):
                    print("  Automatic download cancelled.")
                    _print_manual_install_instructions(dest)
                    return None
            except (EOFError, KeyboardInterrupt):
                print("\n  Download cancelled.")
                _print_manual_install_instructions(dest)
                return None

    prefix = f"cohere-transcribe-{revision}"
    primary_base = (base_url or _release_base()).rstrip("/")
    candidates = _release_candidates(primary_base)

    manifest = None
    resolved_url = None
    failures = []
    for cand in candidates:
        try:
            cand_manifest, cand_resolved = _fetch_part_manifest(cand, prefix)
        except Exception as e:
            failures.append(f"{cand}: {e}")
            continue
        if not cand_manifest:
            failures.append(
                f"{cand}: no {prefix}.partN.xz entries in its SHA256SUMS")
            continue
        # A manifest is uploaded before the parts it lists, so a bundle that is
        # mid-upload or was abandoned must never shadow a complete one. Confirm
        # every part is actually fetchable before committing to this candidate.
        missing = [n for n in sorted(cand_manifest)
                   if not _asset_exists(f"{cand}/{n}")]
        if missing:
            failures.append(
                f"{cand}: SHA256SUMS lists assets that are not present: "
                f"{', '.join(missing)}")
            continue
        manifest = cand_manifest
        resolved_url = cand_resolved
        base_url = cand
        break

    if not manifest:
        print(f"Could not fetch model manifest from release assets ({candidates}).")
        for f in failures:
            print(f"  - {f}")
        _print_manual_install_instructions(dest)
        return None
    print(f"Downloading Cohere model parts from {base_url} (Apache-2.0 release asset)...")

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="vt-model-dl-", dir=os.path.dirname(os.path.abspath(dest)))
    try:
        part_paths = {}
        for name in sorted(manifest):
            part_xz = os.path.join(tmp_root, name)
            _http_get(f"{base_url}/{name}", part_xz, name)
            part_paths[name] = part_xz
        return _assemble_parts(part_paths, manifest, dest,
                               origin=base_url, resolved_url=resolved_url,
                               source_kind="remote")
    except Exception as e:
        print(f"Model auto-download failed: {e}", flush=True)
        _print_manual_install_instructions(dest)
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    import argparse
    # The ✓/✕ markers below must not raise on a stream that cannot encode them
    # (legacy codepage, LANG=C, PYTHONIOENCODING=ascii). See console_text.
    import console_text
    console_text.harden_standard_streams()
    parser = argparse.ArgumentParser(
        prog="python -m src.model_download",
        description="Download and verify Cohere model assets from GitHub release.",
    )
    parser.add_argument("--dest", default=None, help="Target installation directory (default: models/cohere)")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing local model files")
    parser.add_argument("--from", dest="source", default=None,
                        help="Install from a local split bundle (directory or .zip/.tar) instead of downloading")
    args = parser.parse_args()

    target = args.dest or cohere_models_dir()
    if args.verify_only:
        if is_local_model_complete(target):
            print(f"✓ Cohere model at {target} is complete and valid.")
            sys.exit(0)
        else:
            print(f"✕ Cohere model at {target} is missing or incomplete.")
            sys.exit(1)

    if args.source:
        result = install_from_local_bundle(args.source, dest=target)
    else:
        result = ensure_local_cohere(dest=target)
    if result:
        print(f"✓ Cohere model ready at {result}")
        sys.exit(0)
    else:
        print("✕ Model download or verification failed.")
        sys.exit(1)

