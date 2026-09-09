"""
First-run model acquisition from GitHub Releases — no Hugging Face required.

CohereLabs/cohere-transcribe-03-2026 is Apache-2.0 licensed, so the weights are
mirrored as split-xz GitHub Release assets (see scripts/prepare_model_release.py).
When the app needs the Cohere backend and no local copy exists, this module
downloads the parts, verifies their SHA-256, decompresses/concatenates them, and
extracts them into a writable per-user directory (~/.local/share/vt/models/cohere)
so the backend can load fully offline afterwards (local_files_only=True).

Pure stdlib: urllib for download, lzma for decompression, tarfile for extract.

Env knobs:
  VT_AUTO_DOWNLOAD_MODEL   "0"/"false" disables automatic download
  VT_MODEL_RELEASE_BASE    base URL of the release assets (default: GitHub
                           "latest" release of jjamesmartiin/voice-transcriber)
  XDG_DATA_HOME            (standard) relocates the install target for testing
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


def cohere_models_dir():
    """Writable install target shared by the app's local-model search path."""
    return os.path.join(get_data_dir(), "models", "cohere")


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
    """Download the SHA256SUMS file; return {filename: sha256} for *.partN.xz."""
    sums_url = f"{base_url}/{prefix}.SHA256SUMS"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".sha256")
    tmp.close()
    try:
        _http_get(sums_url, tmp.name, f"manifest ({prefix}.SHA256SUMS)")
        manifest = {}
        with open(tmp.name) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    digest, name = line.split(None, 1)
                except ValueError:
                    continue
                if re.fullmatch(rf"{re.escape(prefix)}\.part\d+\.xz", name):
                    manifest[name] = digest
        return manifest
    finally:
        os.unlink(tmp.name)


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
    base_url = (base_url or _release_base()).rstrip("/")
    print(f"Local model not found at {dest}.\n"
          f"Downloading Cohere model parts from {base_url} (Apache-2.0 release asset)...")

    try:
        manifest = _fetch_part_manifest(base_url, prefix)
    except Exception as e:
        print(f"Could not fetch model manifest ({e}). Falling back to Hugging Face "
              f"(requires token/access).")
        return None
    if not manifest:
        print(f"Manifest at {base_url} contained no {prefix}.partN.xz entries. "
              f"Falling back to Hugging Face.")
        return None

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
        print(f"Model installed at {dest}. Loading fully offline from now on.")
        return dest
    except Exception as e:
        print(f"Model auto-download failed: {e}", flush=True)
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
