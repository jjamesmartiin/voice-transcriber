"""
First-run model acquisition from GitHub Releases — no Hugging Face required.

A single :class:`ModelSpec` registry (see :data:`MODELS`) declares every
redistributable model: its upstream repo/revision, the release-asset prefix, the
SHA-256 of each redistributed file, its license and its install directory.
CohereLabs/cohere-transcribe-03-2026 is the first entry; a GGUF formatter and
ONNX diarization graphs are declared here the same way. When the app needs a
model and no local copy exists, this module downloads the parts, verifies their
SHA-256, decompresses/concatenates them, and installs them so the backend can
load fully offline afterwards (local_files_only=True). Verification is driven by
the spec's ``digests`` map and never assumes a particular filename.

Install location (see :func:`models_dir`): when the app runs from a git checkout
of this repository, the weights unpack into ``<repo>/models/<target_subdir>`` so
it is obvious they belong to / came from this project; read-only installs (Nix
store, AppImage) fall back to ``~/.local/share/vt/models/<target_subdir>``. A
SOURCE.json provenance file is written next to the weights recording the origin
repo, release tag, sha256, license, and install date.

Pure stdlib: urllib for download, lzma for decompression, tarfile for extract.

Env knobs:
  VT_MODEL_DIR            explicit model install directory (overrides both)
  VT_AUTO_DOWNLOAD_MODEL  "0"/"false" disables automatic download
  VT_MODEL_RELEASE_BASE   base URL of the release assets (default: the
                          revision-keyed model bundle tag in
                          jjamesmartiin/voice-transcriber)
  XDG_DATA_HOME           (standard) relocates the per-user fallback for testing
"""
import dataclasses
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


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    """Everything the installer and the publisher need to know about one model.

    This registry is the single declaration home for every model constant: a
    repo id, revision, digest, bundle tag, license or target directory is
    written down here and nowhere else. ``tests/shared/test_model_download.py``
    pins that no other module duplicates those values.

    Verification is deliberately format-agnostic: ``digests`` maps *each*
    redistributed file to its SHA-256, so a ``model.safetensors`` HF snapshot, a
    single ``.gguf`` formatter and a set of ``.onnx`` diarization graphs all flow
    through the same installer without a special case. Adding a model is adding
    an entry to :data:`MODELS`; the packager and publisher read the entry.
    """

    name: str                        # registry key, e.g. "cohere"
    display_name: str                # human label used in console messages
    description: str                 # one provenance line, used in NOTICE
    repo_id: str                     # source Hugging Face repo id
    revision: str                    # pinned upstream revision
    asset_prefix: str                # release prefix: "<prefix>.partN.xz"
    bundle_tag: str                  # GitHub release tag hosting the assets
    release_base: str                # base URL the installer downloads from
    package_files: tuple = ()        # files the packager stages into the tar
    required_local: tuple = ()       # minimal set meaning "loadable locally"
    digests: dict = dataclasses.field(default_factory=dict)  # file -> sha256
    #: Config-layer model ids that select this entry. ``formatter_model: s1-mini``
    #: and ``diarization_model: diarization`` are user-facing values; this maps
    #: them back to the registry key so the mapping lives in exactly one place.
    model_ids: tuple = ()
    license_file: str = ""           # repo-relative license to ship
    #: Repo-relative NOTICE to ship *verbatim*. Empty means "generate one from
    #: the spec" (the Cohere behaviour). A model whose upstream NOTICE carries
    #: an operative clause - S1-mini's naming term - must ship that exact text.
    notice_file: str = ""
    license_name: str = ""           # short license label (NOTICE, provenance)
    target_subdir: str = ""          # <models root>/<target_subdir>
    download_hint: str = ""          # size hint shown in the download prompt
    # Formal names for the legal NOTICE artifact only. These are deliberately
    # separate from ``display_name``/``license_name``: console messages want a
    # short label ("Cohere Transcribe"), whereas a NOTICE is a legal artifact
    # and must keep the exact wording it has always shipped with. Empty means
    # "fall back to the short label".
    notice_title: str = ""           # formal model name on the NOTICE
    license_title: str = ""          # formal license name on the NOTICE


# Single source of truth for the publishing target: scripts/publish_model_bundle.sh
# reads these back out of the registry so it cannot drift from the client.
REPO_SLUG = "jjamesmartiin/voice-transcriber"
_REPO_URL = f"https://github.com/{REPO_SLUG}"

# Weights are addressed by *model revision*, never by app release. The revision
# is compiled into the binary and already names the asset files, so every app
# version resolves to the same bundle: publishing weights is a once-per-revision
# job, not a once-per-version one. Bumping the revision renames the tag, which
# is what forces a fresh publish when (and only when) the weights actually
# change.
_COHERE_REVISION = "499888924f5f1313b48ab0686c8f3a94178a4709"
_COHERE_SHA256 = "987bd3e141c7bfdb5a78f5db11397ee7737308357e6cc0a3f36a4979b158137a"
_COHERE_TAG = f"model-cohere-{_COHERE_REVISION[:12]}"

COHERE = ModelSpec(
    name="cohere",
    display_name="Cohere Transcribe",
    description="Automatic Speech Recognition model by Cohere / Cohere Labs.",
    repo_id="CohereLabs/cohere-transcribe-03-2026",
    revision=_COHERE_REVISION,
    asset_prefix=f"cohere-transcribe-{_COHERE_REVISION}",
    bundle_tag=_COHERE_TAG,
    release_base=f"{_REPO_URL}/releases/download/{_COHERE_TAG}",
    package_files=(
        "config.json",
        "generation_config.json",
        "model.safetensors",
        "modeling_cohere_asr.py",
        "configuration_cohere_asr.py",
        "processing_cohere_asr.py",
        "tokenization_cohere_asr.py",
        "processor_config.json",
        "preprocessor_config.json",
        "tokenizer_config.json",
        "tokenizer.json",
        "tokenizer.model",
        "special_tokens_map.json",
    ),
    required_local=(
        "config.json", "model.safetensors", "tokenizer.json",
        "modeling_cohere_asr.py", "processor_config.json",
    ),
    digests={"model.safetensors": _COHERE_SHA256},
    license_file="config/licenses/Cohere-Apache-2.0.txt",
    license_name="Apache-2.0",
    # Pinned so the published NOTICE stays byte-identical to every release so
    # far: "2B" is part of the formal name, and a NOTICE cites the license by
    # its full title rather than the SPDX id used in console/provenance text.
    notice_title="Cohere Transcribe 2B",
    license_title="the Apache License, Version 2.0",
    target_subdir="cohere",
    download_hint="~2.8 GB (~3.9 GB uncompressed on disk)",
)

#: The registry. Every redistributable artifact is one entry here; the packager
#: and publisher read the entry, and the installers resolve through it. Adding a
#: model is adding an entry, never a branch in the installer.

# --- Formatter: S1-mini (GGUF, llama.cpp) ----------------------------------
# The quant lives in a *separate* repo from the base model: superwhisper/s1-mini
# ships no GGUF at all. Pin by commit sha, because the repo has no tags (a
# ``v1`` ref 404s). The digest is the extracted file, not the download wrapper.
_FORMATTER_REVISION = "34add00a48a2e5d24e5a4ee5405a99620a3a240c"
_FORMATTER_SHA256 = "3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634"
_FORMATTER_TAG = f"model-formatter-{_FORMATTER_REVISION[:12]}"

FORMATTER = ModelSpec(
    name="formatter",
    # The licence's additional term requires exactly this attribution wherever
    # the model is named, so it is the display name as well as the NOTICE title.
    display_name='"S1-mini" by "Superwhisper"',
    description="On-device transcript formatter by Superwhisper.",
    repo_id="superwhisper/s1-mini-GGUF",
    revision=_FORMATTER_REVISION,
    asset_prefix=f"s1-mini-{_FORMATTER_REVISION}",
    bundle_tag=_FORMATTER_TAG,
    release_base=f"{_REPO_URL}/releases/download/{_FORMATTER_TAG}",
    package_files=("s1-mini-q4_k_m.gguf",),
    required_local=("s1-mini-q4_k_m.gguf",),
    digests={"s1-mini-q4_k_m.gguf": _FORMATTER_SHA256},
    model_ids=("s1-mini",),
    license_file="config/licenses/S1-mini-Apache-2.0.txt",
    notice_file="config/licenses/S1-mini-NOTICE.txt",
    license_name="Apache-2.0 (with an additional naming term)",
    notice_title="S1-mini",
    license_title=("the Apache License, Version 2.0, plus the additional "
                   "naming term"),
    target_subdir="formatter",
    download_hint="~462 MiB",
)

# --- Diarization: pyannote segmentation + 3D-Speaker embedding --------------
# Both graphs come from k2-fsa/sherpa-onnx GitHub releases; the embedding asset
# bucket really is misspelled ``recongition`` upstream, so the URLs are copied
# literally. The revision is the segmentation tarball's sha256: it is the
# primary artifact and pins the bundle tag to the content, the same way the
# Cohere and formatter tags pin to a commit sha.
_DIARIZATION_TARBALL_SHA256 = (
    "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
)
#: Extracted pyannote segmentation graph (what we redistribute).
_DIARIZATION_SEGMENTATION_SHA256 = (
    "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079"
)
#: 3D-Speaker eres2net embedding graph.
_DIARIZATION_EMBEDDING_SHA256 = (
    "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
)
_DIARIZATION_REVISION = _DIARIZATION_TARBALL_SHA256
_DIARIZATION_TAG = f"model-diarization-{_DIARIZATION_REVISION[:12]}"
#: Installed layout the backend reads (see diarizers/sherpa_onnx.py).
_DIARIZATION_SEGMENTATION_PATH = (
    "sherpa-onnx-pyannote-segmentation-3-0/model.onnx"
)
_DIARIZATION_EMBEDDING_PATH = (
    "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
)

DIARIZATION = ModelSpec(
    name="diarization",
    display_name="Diarization (sherpa-onnx)",
    description="Speaker diarization graphs: pyannote segmentation 3.0 "
                "(MIT) and a 3D-Speaker eres2net embedding (Apache-2.0).",
    repo_id="k2-fsa/sherpa-onnx",
    revision=_DIARIZATION_REVISION,
    asset_prefix=f"diarization-{_DIARIZATION_REVISION}",
    bundle_tag=_DIARIZATION_TAG,
    release_base=f"{_REPO_URL}/releases/download/{_DIARIZATION_TAG}",
    package_files=(
        _DIARIZATION_EMBEDDING_PATH,
        _DIARIZATION_SEGMENTATION_PATH,
    ),
    required_local=(
        _DIARIZATION_EMBEDDING_PATH,
        _DIARIZATION_SEGMENTATION_PATH,
    ),
    digests={
        _DIARIZATION_EMBEDDING_PATH: _DIARIZATION_EMBEDDING_SHA256,
        _DIARIZATION_SEGMENTATION_PATH: _DIARIZATION_SEGMENTATION_SHA256,
    },
    model_ids=("diarization",),
    license_file="config/licenses/Diarization-LICENSES.txt",
    notice_file="config/licenses/Diarization-NOTICE.txt",
    license_name="MIT (segmentation) and Apache-2.0 (embedding)",
    notice_title="sherpa-onnx diarization models",
    license_title="MIT (segmentation) and Apache-2.0 (embedding)",
    target_subdir="diarization",
    download_hint="~40 MB",
)

MODELS: dict = {
    COHERE.name: COHERE,
    FORMATTER.name: FORMATTER,
    DIARIZATION.name: DIARIZATION,
}

# --- Backward-compatible constant aliases (deprecated) ---------------------
# Kept so existing callers, docs and the publisher's constant import keep
# working unchanged. New code should read a ModelSpec out of MODELS instead.
REPO_ID = COHERE.repo_id
REVISION = COHERE.revision
SAFETENSORS_SHA256 = COHERE.digests["model.safetensors"]
MODEL_BUNDLE_TAG = COHERE.bundle_tag
DEFAULT_RELEASE_BASE = COHERE.release_base
_REQUIRED_LOCAL = list(COHERE.required_local)


def get_spec(name):
    """Return the :class:`ModelSpec` registered under ``name``.

    Raises ``KeyError`` (never a silent ``None``) for an unknown model so a typo
    fails at the call site instead of installing nothing.
    """
    try:
        return MODELS[name]
    except KeyError:
        known = ", ".join(sorted(MODELS))
        raise KeyError(f"unknown model {name!r} (known: {known})") from None


def registry_name_for_model(model_id):
    """Registry key whose ``model_ids`` contains ``model_id``, or ``None``.

    A config value (``formatter_model: s1-mini``, ``diarization_model:
    diarization``) is a *model id*, not a registry key. This is the one place
    that mapping lives, so the settings layer and the loaders cannot drift.
    """
    key = str(model_id or "").strip().lower()
    if not key:
        return None
    for name, spec in MODELS.items():
        if key in tuple(getattr(spec, "model_ids", ()) or ()):
            return name
    return None


def _resolved_spec(name, revision=None):
    """Return the spec for ``name``, allowing a legacy revision override.

    The override recomputes the revision-derived constants and exists only for
    the deprecated ``*_cohere`` aliases and the tests written against them; a
    real registry entry always pins its own revision.
    """
    spec = get_spec(name)
    if not revision or revision == spec.revision:
        return spec
    template = spec.asset_prefix.replace(spec.revision, "{revision}")
    bundle_tag = f"model-{spec.name}-{revision[:12]}"
    return dataclasses.replace(
        spec,
        revision=revision,
        asset_prefix=template.format(revision=revision),
        bundle_tag=bundle_tag,
        release_base=f"{_REPO_URL}/releases/download/{bundle_tag}",
    )

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


def _data_dir():
    """The per-user data root, without creating anything (see get_data_dir)."""
    if os.environ.get("XDG_DATA_HOME"):
        return os.path.join(os.environ["XDG_DATA_HOME"], "vt")
    if os.name == "posix":
        return os.path.join(os.path.expanduser("~"), ".local", "share", "vt")
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "vt")


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


def models_dir(name):
    """Preferred writable install target for the model registered as ``name``.

    Order: $VT_MODEL_DIR override -> <repo checkout>/models/<target_subdir> (so
    the weights live visibly inside the project they came from) -> per-user data
    dir (~/.local/share/vt/models/<target_subdir>) for read-only installs
    (Nix/AppImage).
    """
    return _models_dir_for(get_spec(name))


def _models_dir_for(spec):
    """Implementation of :func:`models_dir` for a resolved spec."""
    override = os.environ.get("VT_MODEL_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    subdir = spec.target_subdir
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        for cand in [
            os.path.join(exe_dir, "models", subdir),
            os.path.join(exe_dir, "model", subdir),
            os.path.join(exe_dir, "models"),
            exe_dir,
        ]:
            if os.path.isdir(cand) and is_model_complete(spec.name, cand):
                return cand
        if hasattr(sys, "_MEIPASS"):
            for cand in [
                os.path.join(sys._MEIPASS, "models", subdir),
                os.path.join(sys._MEIPASS, "models"),
                sys._MEIPASS,
            ]:
                if os.path.isdir(cand) and is_model_complete(spec.name, cand):
                    return cand
        exe_models = os.path.join(exe_dir, "models", subdir)
        if os.path.isdir(exe_models) or os.access(exe_dir, os.W_OK):
            return exe_models
    root = find_repo_root()
    repo_dir = os.path.join(root, "models", subdir) if root else None
    per_user = _per_user_model_dir(subdir)

    # An existing, complete install always wins, wherever it lives. This keeps a
    # model that was installed into the per-user directory (when the checkout
    # was read-only, or before its registry entry existed) findable once the
    # checkout path becomes writable - registering a model must never make an
    # already-installed one unfindable.
    if repo_dir and is_model_complete(spec.name, repo_dir):
        return repo_dir
    if is_model_complete(spec.name, per_user):
        return per_user

    # Nothing installed yet: a writable checkout is the conventional target, so
    # the weights live visibly in the project they came from; read-only installs
    # (Nix store, AppImage) fall back to the per-user data dir.
    if repo_dir and (
        (os.path.isdir(repo_dir) and os.access(repo_dir, os.W_OK))
        or (not os.path.isdir(repo_dir) and os.access(root, os.W_OK))
    ):
        return repo_dir
    return per_user


def cohere_models_dir():
    """Deprecated alias for ``models_dir("cohere")``."""
    return models_dir("cohere")


def _write_provenance(spec, dest, base_url, resolved_url=None, source_kind="remote"):
    """Write SOURCE.json next to the weights so their origin is unambiguous."""
    import datetime as _dt
    release_tag = None
    if resolved_url:
        m = re.search(r"/releases/download/([^/]+)/", resolved_url)
        if m:
            release_tag = m.group(1)
    info = {
        "model": spec.repo_id,
        "revision": spec.revision,
    }
    # Keep the Cohere-era key for a safetensors snapshot; a format-agnostic spec
    # (GGUF / ONNX) records its whole digest map instead.
    if "model.safetensors" in spec.digests:
        info["model_safetensors_sha256"] = spec.digests["model.safetensors"]
    if set(spec.digests) != {"model.safetensors"}:
        info["digests"] = dict(spec.digests)
    info.update({
        "download_url_base": base_url,
        "release_tag": release_tag,
        "source_kind": source_kind,
        "license": spec.license_name + " (see LICENSE and NOTICE in this directory)",
        "installed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "note": "Weights mirrored from the " + spec.repo_id + " HF snapshot via the "
                "voice-transcriber GitHub release, so the app runs without a "
                "Hugging Face account.",
    })
    try:
        with open(os.path.join(dest, "SOURCE.json"), "w") as f:
            import json as _json
            _json.dump(info, f, indent=2)
            f.write("\n")
    except Exception as e:
        print(f"(could not write SOURCE.json provenance: {e})")


def _release_base(spec):
    return os.environ.get("VT_MODEL_RELEASE_BASE", spec.release_base).rstrip("/")


def _release_candidates(primary, canonical=None):
    """Base URLs to try, most-preferred first, without duplicates.

    `primary` is whatever the caller asked for (an explicit base_url, the
    VT_MODEL_RELEASE_BASE override, or the revision-derived bundle tag) and
    `canonical` is the spec's own bundle URL. The canonical bundle is appended as
    a fallback so an explicit or relocated base degrades to the real one instead
    of failing.
    """
    fallback = DEFAULT_RELEASE_BASE if canonical is None else canonical
    out = []
    for cand in (primary, fallback):
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


def is_model_complete(name, directory=None):
    """True when a loadable local copy of ``name`` already exists (offline-ready)."""
    spec = get_spec(name)
    directory = directory or models_dir(name)
    return all(os.path.exists(os.path.join(directory, f))
               for f in spec.required_local)


def is_local_model_complete(directory=None):
    """Deprecated alias for ``is_model_complete("cohere", directory)``."""
    return is_model_complete("cohere", directory)


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


def install_model_from_local_bundle(name, source, dest=None, revision=None):
    """Install the model registered as ``name`` from a local split bundle, offline.

    ``source`` is either a directory holding ``<asset_prefix>.partN.xz`` plus
    ``SHA256SUMS``, or a ``.zip`` / ``.tar[.*]`` archive containing them. The
    parts are verified, decompressed, concatenated and extracted exactly as the
    network installer does, so an airgapped host needs no extra tooling.
    """
    spec = _resolved_spec(name, revision)
    dest = dest or _models_dir_for(spec)
    prefix = spec.asset_prefix
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
        for part_name in manifest:
            part = os.path.join(bundle_dir, part_name)
            if not os.path.isfile(part):
                raise FileNotFoundError(f"bundle {source!r} is missing {part_name}")
            part_paths[part_name] = part

        print(f"Installing {spec.display_name} model from local bundle {source} "
              f"({len(part_paths)} part(s))...", flush=True)
        return _assemble_parts(
            spec, part_paths, manifest, dest,
            origin=os.path.abspath(source), source_kind="local",
        )
    finally:
        if tmp_extract:
            shutil.rmtree(tmp_extract, ignore_errors=True)


def install_from_local_bundle(source, dest=None, revision=None):
    """Deprecated alias for ``install_model_from_local_bundle("cohere", ...)``."""
    return install_model_from_local_bundle("cohere", source, dest=dest,
                                           revision=revision)


def _assemble_parts(spec, part_paths, manifest, dest, origin,
                    resolved_url=None, source_kind="remote"):
    """Verify, decompress, concatenate and extract verified parts into ``dest``.

    ``part_paths`` maps each manifest filename to an existing local path; the
    caller owns those files and they are never deleted here. ``origin`` is
    recorded in SOURCE.json (a release URL online, a filesystem path offline).
    Verification is driven by ``spec.digests`` and never assumes a particular
    filename, so a GGUF or ONNX artifact installs through this same path.
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
        # Verify every redistributed file against the spec's digest map. Nothing
        # assumes the artifact is called model.safetensors: a GGUF or ONNX spec
        # carries its own filenames and hashes.
        for fname, expected in spec.digests.items():
            path = os.path.join(staging, fname)
            if not os.path.exists(path):
                raise RuntimeError(
                    f"Extracted archive is missing {fname} (listed in the "
                    f"{spec.name} digest map). Aborting.")
            got = _sha256_file(path)
            if got != expected:
                raise RuntimeError(
                    f"Extracted {fname} sha256 {got} does not match the "
                    f"expected {expected}. Aborting.")
        if not is_model_complete(spec.name, staging):
            raise RuntimeError("Extracted model directory is missing required files.")

        if os.path.isdir(dest):
            shutil.rmtree(dest)
        shutil.move(staging, dest)
        _write_provenance(spec, dest, origin, resolved_url, source_kind=source_kind)
        print(f"Model installed at {dest}. Loading fully offline from now on.\n"
              f"Origin recorded in {os.path.join(dest, 'SOURCE.json')}.")
        return dest
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _print_manual_install_instructions(spec, dest):
    """Print clear instructions for manual model installation."""
    print("\n" + "=" * 68)
    print(f"  TO MANUALLY INSTALL THE {spec.display_name.upper()} MODEL:")
    print("  1. Download the split model parts and SHA256SUMS from GitHub:")
    print(f"     {_REPO_URL}/releases/tag/{spec.bundle_tag}")
    print("  2. Decompress and extract the archive directly into:")
    print(f"     {dest}")
    print(f"     (Required: {', '.join(spec.required_local)})")
    print("  Or set the VT_MODEL_DIR environment variable to your model folder.")
    print("=" * 68 + "\n", flush=True)


def ensure_model(name, dest=None, base_url=None, revision=None):
    """
    Make sure a loadable local copy of ``name`` exists at `dest`.

    Offline first: if a split bundle is present on disk (``VT_MODEL_SOURCE_DIR``
    or a ``model-bundle/`` directory next to the app), it is installed with no
    network. Otherwise the parts are fetched from the release assets.

    Returns the directory path on success (already there, or freshly installed),
    or None if auto-download is disabled / failed (caller may fall back to the
    Hugging Face path or surface an error).
    """
    spec = _resolved_spec(name, revision)
    dest = dest or _models_dir_for(spec)
    if is_model_complete(spec.name, dest):
        return dest

    # Airgapped path: install from a bundle carried on disk before ever
    # touching the network.
    local_source, explicit = _local_model_source()
    if local_source:
        try:
            installed = install_model_from_local_bundle(
                spec.name, local_source, dest=dest, revision=spec.revision)
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
        _print_manual_install_instructions(spec, dest)
        return None

    if auto_env not in ("1", "true", "yes", "always"):
        if sys.stdin and hasattr(sys.stdin, "isatty") and sys.stdin.isatty():
            try:
                print("\n" + "-" * 68)
                print(f"  {spec.display_name} model weights are not installed.")
                print(f"  Target location: {dest}")
                print(f"  Download size: {spec.download_hint}.")
                print("-" * 68)
                ans = input("  Would you like to download them automatically from GitHub now? [Y/n]: ").strip().lower()
                if ans and ans not in ("y", "yes"):
                    print("  Automatic download cancelled.")
                    _print_manual_install_instructions(spec, dest)
                    return None
            except (EOFError, KeyboardInterrupt):
                print("\n  Download cancelled.")
                _print_manual_install_instructions(spec, dest)
                return None

    prefix = spec.asset_prefix
    primary_base = (base_url or _release_base(spec)).rstrip("/")
    candidates = _release_candidates(primary_base, spec.release_base)

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
        _print_manual_install_instructions(spec, dest)
        return None
    print(f"Downloading {spec.display_name} model parts from {base_url} "
          f"({spec.license_name} release asset)...")

    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    tmp_root = tempfile.mkdtemp(prefix="vt-model-dl-", dir=os.path.dirname(os.path.abspath(dest)))
    try:
        part_paths = {}
        for part_name in sorted(manifest):
            part_xz = os.path.join(tmp_root, part_name)
            _http_get(f"{base_url}/{part_name}", part_xz, part_name)
            part_paths[part_name] = part_xz
        return _assemble_parts(spec, part_paths, manifest, dest,
                               origin=base_url, resolved_url=resolved_url,
                               source_kind="remote")
    except Exception as e:
        print(f"Model auto-download failed: {e}", flush=True)
        _print_manual_install_instructions(spec, dest)
        return None
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def ensure_local_cohere(dest=None, base_url=None, revision=REVISION):
    """Deprecated alias for ``ensure_model("cohere", ...)``."""
    return ensure_model("cohere", dest=dest, base_url=base_url, revision=revision)


if __name__ == "__main__":
    import argparse
    # The ✓/✕ markers below must not raise on a stream that cannot encode them
    # (legacy codepage, LANG=C, PYTHONIOENCODING=ascii). See console_text.
    import console_text
    console_text.harden_standard_streams()
    parser = argparse.ArgumentParser(
        prog="python -m src.model_download",
        description="Download and verify model assets from a GitHub release.",
    )
    parser.add_argument("--model", default="cohere",
                        help="Model to install, by registry name (default: cohere). "
                             "See `help --json`; the registry lives in this module.")
    parser.add_argument("--dest", default=None,
                        help="Target installation directory (default: the registry's target for --model)")
    parser.add_argument("--verify-only", action="store_true", help="Only verify existing local model files")
    parser.add_argument("--from", dest="source", default=None,
                        help="Install from a local split bundle (directory or .zip/.tar) instead of downloading")
    args = parser.parse_args()

    try:
        spec = get_spec(args.model)
    except KeyError as e:
        print(f"✕ {e}")
        sys.exit(2)

    target = args.dest or models_dir(args.model)
    if args.verify_only:
        if is_model_complete(args.model, target):
            print(f"✓ {spec.name.capitalize()} model at {target} is complete and valid.")
            sys.exit(0)
        else:
            print(f"✕ {spec.name.capitalize()} model at {target} is missing or incomplete.")
            sys.exit(1)

    if args.source:
        result = install_model_from_local_bundle(args.model, args.source, dest=target)
    else:
        result = ensure_model(args.model, dest=target)
    if result:
        print(f"✓ {spec.name.capitalize()} model ready at {result}")
        sys.exit(0)
    else:
        print("✕ Model download or verification failed.")
        sys.exit(1)

