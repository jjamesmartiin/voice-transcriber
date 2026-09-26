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
  VT_MODEL_RELEASE_BASE   base URL of the release assets (default: GitHub
                          "latest" release of jjamesmartiin/voice-transcriber)
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

DEFAULT_RELEASE_BASE = (
    "https://github.com/jjamesmartiin/voice-transcriber/releases/latest/download"
)
FALLBACK_RELEASE_BASE = (
    "https://github.com/jjamesmartiin/voice-transcriber/releases/download/v1.1.0"
)

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
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        for cand in [
            os.path.join(sys._MEIPASS, "models", "cohere"),
            os.path.join(sys._MEIPASS, "models"),
            sys._MEIPASS,
        ]:
            if os.path.isdir(cand) and os.path.exists(os.path.join(cand, "config.json")):
                return cand
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


def _write_provenance(dest, base_url, resolved_url=None):
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
        except (urllib.error.URLError, OSError, socket.timeout) as e:
            last_err = e
            print(f"Manifest attempt {attempt}/3 for {sums_url} failed: {e}", flush=True)
            time.sleep(2 * attempt)
    if data is None:
        raise RuntimeError(f"Failed to download {sums_url}: {last_err}")
    manifest = {}
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            digest, name = line.split(None, 1)
        except ValueError:
            continue
        if re.fullmatch(rf"{re.escape(prefix)}\.part\d+\.xz", name):
            manifest[name] = digest
    return manifest, resolved_url


def ensure_local_cohere(dest=None, base_url=None, revision=REVISION):
    """
    Make sure a loadable local Cohere model exists at `dest`.

    Returns the directory path on success (already there, or freshly installed),
    or None if auto-download is disabled / failed (caller may fall back to the
    Hugging Face path or surface an error).
    """
    dest = dest or cohere_models_dir()
    if is_local_model_complete(dest):
        return dest

    if os.environ.get("VT_AUTO_DOWNLOAD_MODEL", "1").strip().lower() in (
            "0", "false", "no", "off"):
        print("VT_AUTO_DOWNLOAD_MODEL=0: skipping automatic model download.")
        return None

    prefix = f"cohere-transcribe-{revision}"
    primary_base = (base_url or _release_base()).rstrip("/")
    candidates = [primary_base]
    if FALLBACK_RELEASE_BASE not in candidates:
        candidates.append(FALLBACK_RELEASE_BASE)

    manifest = None
    resolved_url = None
    for cand in candidates:
        try:
            cand_manifest, cand_resolved = _fetch_part_manifest(cand, prefix)
            if cand_manifest:
                manifest = cand_manifest
                resolved_url = cand_resolved
                base_url = cand
                break
        except Exception:
            continue

    if not manifest:
        print(f"Could not fetch model manifest from release assets ({candidates}).")
        return None
    print(f"Downloading Cohere model parts from {base_url} (Apache-2.0 release asset)...")

    part_names = sorted(manifest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="vt-model-dl-", dir=os.path.dirname(dest))
    try:
        tar_path = os.path.join(tmp_root, "model.tar")
        # Decompress each verified part straight into one concatenated tar.
        with open(tar_path, "wb") as tar_out:
            for name in part_names:
                part_xz = os.path.join(tmp_root, name)
                _http_get(f"{base_url}/{name}", part_xz, name)
                digest = _sha256_file(part_xz)
                if digest != manifest[name]:
                    raise RuntimeError(
                        f"SHA-256 mismatch for {name}: expected {manifest[name]}, "
                        f"got {digest}. Aborting (no partial install).")
                print(f"Verified {name} (sha256 ok). Decompressing...", flush=True)
                with lzma.open(part_xz, "rb") as fin:
                    shutil.copyfileobj(fin, tar_out, 1 << 20)
                os.unlink(part_xz)

        # Extract into a staging dir, then atomically move into place.
        staging = os.path.join(tmp_root, "extracted")
        with tarfile.open(tar_path, "r") as tf:
            tf.extractall(staging, filter="data")
        weights = os.path.join(staging, "model.safetensors")
        got = _sha256_file(weights)
        if got != SAFETENSORS_SHA256:
            raise RuntimeError(
                f"Extracted model.safetensors sha256 {got} does not match the "
                f"expected {SAFETENSORS_SHA256}. Aborting.")
        if not is_local_model_complete(staging):
            raise RuntimeError("Extracted model directory is missing required files.")

        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.move(staging, dest)
        _write_provenance(dest, base_url, resolved_url)
        print(f"Model installed at {dest}. Loading fully offline from now on.\n"
              f"Origin recorded in {os.path.join(dest, 'SOURCE.json')}.")
        return dest
    except Exception as e:
        print(f"Model auto-download failed: {e}", flush=True)
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
