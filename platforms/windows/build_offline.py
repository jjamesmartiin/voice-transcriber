#!/usr/bin/env python3
"""
Build script for the standalone Windows Voice Transcriber EXE.

By default the local Cohere model (models/cohere) is bundled for fully offline
operation. Pass --no-model to build a smaller (~1 GB) bundle that installs the
weights on first run instead - either from the release assets (online) or from a
local split bundle on disk (`model-bundle/` next to the exe, or
``VT_MODEL_SOURCE_DIR``), which is the airgapped-friendly build.
"""
import argparse
import hashlib
import os
import platform as _stdlib_platform
import sys
import shutil
import subprocess
from pathlib import Path
import threading
import time
import zipfile

def copy_with_progress(src, dst, desc="Copying"):
    """Copy file/directory with progress feedback for large files"""
    total_size = 0
    if src.is_dir():
        for item in src.rglob("*"):
            if item.is_file():
                total_size += item.stat().st_size
    else:
        total_size = src.stat().st_size

    size_mb = total_size / (1024 * 1024)
    print(f"{desc}: {src.name} ({size_mb:.1f} MB)...")

    copied = [0]
    failures = []

    def progress_copy():
        try:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)
                copied[0] = total_size
            else:
                shutil.copy2(src, dst)
                copied[0] = total_size
            print(f"{desc} complete: {size_mb:.1f} MB")
        except BaseException as exc:  # surfaced on the caller's thread below
            failures.append(exc)

    if size_mb > 100:
        thread = threading.Thread(target=progress_copy)
        thread.start()
        while thread.is_alive():
            time.sleep(2)
            pct = (copied[0] / total_size * 100) if total_size > 0 else 0
            print(f"  {desc}: {pct:.0f}% complete ({size_mb:.1f} MB total)")
            thread.join(timeout=0.5)
    else:
        progress_copy()

    # A copy running on a worker thread used to fail silently: the build went on
    # with a truncated ~GB model directory and shipped it. Never let a copy
    # error be swallowed by the thread that performed it.
    if failures:
        raise RuntimeError(f"{desc} failed: {failures[0]}") from failures[0]

# Configuration
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent if (SCRIPT_DIR.parent.parent / "src").exists() else SCRIPT_DIR
SRC_DIR = PROJECT_ROOT / "src"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
MODELS_DIR = BUILD_DIR / "models"

# The pin's single declaration home, imported rather than restated so the build
# cannot disagree with the publisher or the runtime about the asset name, the
# digest, or which files must travel together.
sys.path.insert(0, str(SRC_DIR))
from voice_transcriber import llama_server_bundle as pin  # noqa: E402

# Local model location (installed from the GitHub release assets on first run)
LOCAL_COHERE = PROJECT_ROOT / "models" / "cohere"

# --- Pinned llama-server file set resolution (build-time only) ----------------
#
# The bundled formatter binary must come from an explicit, pinned source. The
# *runtime* may legitimately fall back to PATH
# (``formatters/llama_server.py::_binary``); the build must not, because whatever
# the build machine's PATH -- and on Windows an empty or ``.`` PATH component
# means the CWD -- answers would then be shipped inside the release. See
# docs/audit-2026-10-11.md 3.5 and docs/TODO.md (uncontrolled search path,
# CWE-427).
#
# The artifact is a *file set*, not a file: ``llama-server.exe`` is a ~9 KB stub
# whose only local import is ``llama-server-impl.dll``, so copying the executable
# alone produces a bundle that resolves and then fails to start. The override
# and the fixed repository path therefore name either the unpacked directory
# holding the set or the upstream ZIP, which is extracted and verified first.
ENV_LLAMA_SERVER_ASSET = "VT_LLAMA_SERVER_ASSET"
ENV_LLAMA_SERVER_SHA256 = "VT_LLAMA_SERVER_SHA256"
PINNED_LLAMA_SERVER = PROJECT_ROOT / pin.ASSET_NAME


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _missing_members(root):
    """The required files absent from ``root``, in declaration order."""
    return [name for name in pin.REQUIRED_MEMBERS if not (root / name).is_file()]


def _member_root(root):
    """The directory at or immediately under ``root`` holding every required member.

    The upstream archive may or may not wrap its files in a top-level directory,
    so the complete set is located rather than assumed to be ``root`` itself.
    The search stops one level down deliberately: a deep walk of a caller-supplied
    tree is unbounded work, and the pinned archive never nests deeper than that.
    ``None`` means no directory at either level holds the whole set.
    """
    candidates = [root, *(p for p in sorted(root.iterdir()) if p.is_dir())]
    for candidate in candidates:
        if not _missing_members(candidate):
            return candidate
    return None


def _require_complete_set(root):
    """Return the complete-set directory, or hard-fail naming what is missing.

    The set is validated *before anything is copied*: the executable without its
    DLLs is the silent breakage this check exists to prevent.
    """
    found = _member_root(root)
    if found is not None:
        return found
    missing = ", ".join(_missing_members(root))
    raise SystemExit(
        f"ERROR: the llama-server set at {root} is incomplete; missing: "
        f"{missing}. llama-server.exe is a ~9 KB stub that will not start "
        f"without the rest of the pinned file set, so bundling a partial set "
        f"is refused.")


def _extract_archive(archive):
    """Extract the pinned ZIP to a scratch directory under ``build/``.

    Entries that would escape the destination are refused: the override can name
    an archive this build did not produce, and a path-traversing member is never
    something a legitimate llama.cpp asset contains.
    """
    dest = BUILD_DIR / "llama-server"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    resolved_dest = dest.resolve()
    with zipfile.ZipFile(archive) as archive_file:
        for name in archive_file.namelist():
            if not (dest / name).resolve().is_relative_to(resolved_dest):
                raise SystemExit(
                    f"ERROR: refusing to extract {name!r} from {archive}: it "
                    f"escapes the destination directory.")
        archive_file.extractall(dest)
    return dest


def verify_llama_server_sha256(path):
    """Verify an archive against ``VT_LLAMA_SERVER_SHA256`` or the pinned digest.

    The digest is of the *archive*, not of ``llama-server.exe``: a digest of the
    stub alone would bless exactly the bundle that fails to start. A caller-
    supplied ``VT_LLAMA_SERVER_SHA256`` still wins, so a deliberately different
    archive can be pinned; otherwise the digest declared by the pin module is
    required. A mismatch is a hard build failure, never a silent overwrite.
    """
    expected = (os.environ.get(ENV_LLAMA_SERVER_SHA256) or "").strip() or pin.ASSET_SHA256
    actual = _sha256(path)
    if actual.lower() != expected.lower():
        raise SystemExit(
            f"ERROR: SHA-256 mismatch for {path}: expected {expected}, got "
            f"{actual}. Refusing to bundle an unverified archive.")
    print(f"llama-server archive SHA-256 verified: {actual}")


def _resolve_source(source):
    """Resolve a directory-or-ZIP into the complete file-set directory."""
    if source.is_dir():
        if (os.environ.get(ENV_LLAMA_SERVER_SHA256) or "").strip():
            raise SystemExit(
                f"ERROR: {ENV_LLAMA_SERVER_SHA256} verifies an archive, but "
                f"{source} is a directory. Point {ENV_LLAMA_SERVER_ASSET} at the "
                f"upstream ZIP, or unset the digest to bundle an unpacked set.")
        return _require_complete_set(source)
    if source.is_file() and source.suffix.lower() == ".zip":
        verify_llama_server_sha256(source)
        return _require_complete_set(_extract_archive(source))
    raise SystemExit(
        f"ERROR: {source} is neither a directory nor a .zip. The pinned "
        f"llama-server artifact is a file set (llama-server.exe alone is a ~9 KB "
        f"stub); point the override at the unpacked directory or the upstream "
        f"archive, not at a single file.")


def resolve_llama_server():
    """Return the pinned llama-server file set to bundle, or ``None`` if absent.

    NEVER consults ``PATH``. An explicit ``VT_LLAMA_SERVER_ASSET`` that is set but
    unusable (missing, or a lone executable without its DLLs) is a hard error: a
    wrong instruction, not an absence. When neither the override nor the fixed
    pinned archive exists, warn loudly and bundle nothing.
    """
    override = (os.environ.get(ENV_LLAMA_SERVER_ASSET) or "").strip()
    if override:
        candidate = Path(override)
        if not candidate.exists():
            raise SystemExit(
                f"ERROR: {ENV_LLAMA_SERVER_ASSET}={override!r} is set but does not "
                f"exist. Point it at the unpacked llama-server directory or the "
                f"upstream ZIP, or unset it.")
        return _resolve_source(candidate)

    if PINNED_LLAMA_SERVER.exists():
        return _resolve_source(PINNED_LLAMA_SERVER)

    message = (
        f"WARNING: no pinned llama-server set to bundle. Checked "
        f"{ENV_LLAMA_SERVER_ASSET} (unset) and {PINNED_LLAMA_SERVER} (missing). "
        f"The Windows bundle will ship without llama-server; set "
        f"{ENV_LLAMA_SERVER_ASSET} to the unpacked set or the upstream ZIP.")
    print(message)
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::warning::{message}")
    return None


def bundle_llama_server(source_dir, dist_dir):
    """Copy the complete pinned set beside ``VoiceTranscriber.exe``.

    The whole set lands in one directory because ``llama-server.exe`` resolves
    its DLLs from its own directory: a member copied anywhere else is a member
    the server cannot load. The Microsoft VC++ runtime is reported, never copied
    -- it is not in the archive and is not ours to redistribute.
    """
    print(f"Bundling llama-server set: {source_dir} -> {dist_dir}")
    copy_with_progress(source_dir, dist_dir, "Copying llama-server")
    print("llama-server runtime prerequisite (NOT bundled): install the Microsoft "
          "Visual C++ Redistributable providing " +
          ", ".join(pin.RUNTIME_PREREQUISITES))


def ensure_cached_models():
    """Ensure the Cohere model is present locally before building"""
    print("=" * 60)
    print("Checking local Cohere model...")
    print("=" * 60)

    if (LOCAL_COHERE / "model.safetensors").exists():
        print(f"Cohere model: FOUND at {LOCAL_COHERE}")
    else:
        print(f"Cohere model: NOT FOUND at {LOCAL_COHERE}")
        print("  Run the app once (online) to install it from the GitHub release assets.")

    print()

def prepare_bundled_models():
    """Copy models to build directory for bundling"""
    print("=" * 60)
    print("Preparing bundled models...")
    print("=" * 60)

    # Clean up old models
    if MODELS_DIR.exists():
        print("Cleaning up old build models...")
        shutil.rmtree(MODELS_DIR)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # The runtime loads from models/cohere (installed from the GitHub release
    # assets on first run). Bundle that directory so the EXE runs fully offline.
    if (LOCAL_COHERE / "model.safetensors").exists():
        print(f"\nFound local Cohere model: {LOCAL_COHERE}")
        dest_cohere = MODELS_DIR / "cohere"
        copy_with_progress(LOCAL_COHERE, dest_cohere, "Copying Cohere model")
    else:
        print(f"\nWARNING: No local Cohere model found at {LOCAL_COHERE}")
        print("  Run the app once (online) to install it, then rebuild.")

    local_diarize = PROJECT_ROOT / "models" / "diarization"
    if local_diarize.exists():
        print(f"\nFound local Diarization model: {local_diarize}")
        dest_diarize = MODELS_DIR / "diarization"
        copy_with_progress(local_diarize, dest_diarize, "Copying Diarization model")

    local_formatter = PROJECT_ROOT / "models" / "formatter"
    if local_formatter.exists():
        print(f"\nFound local Formatter model: {local_formatter}")
        dest_formatter = MODELS_DIR / "formatter"
        copy_with_progress(local_formatter, dest_formatter, "Copying Formatter model")

    print(f"\nBundle prepared at: {MODELS_DIR}")

    # Print summary of what's being bundled
    total_size = sum(f.stat().st_size for f in MODELS_DIR.rglob("*") if f.is_file())
    print(f"Total bundle size: {total_size / (1024*1024):.1f} MB")
    print()

def build_exe(noconsole: bool = False):
    """Build the EXE with PyInstaller"""
    print("=" * 60)
    print("Building EXE with PyInstaller...")
    print("=" * 60)

    console_flag = "--noconsole" if noconsole else "--console"

    # PyInstaller options - copy src files to root of bundle (not in src/ subfolder)
    pyinstaller_args = [
        sys.executable, "-m", "PyInstaller",
        str(SRC_DIR / "main.py"),
        "--name=VoiceTranscriber",
        "--onedir",
        console_flag,
        "--clean",
        "-y",
        "--log-level=INFO",

        # Add models directory
        f"--add-data={MODELS_DIR}{os.pathsep}models",

        # Bundle the real stdlib ``platform`` module under a private directory:
        # the HAL package (src/platform) is collected under the name ``platform``
        # and shadows the stdlib module inside the archive. The shadow guard in
        # src/platform/__init__.py loads this copy so numpy/torch/pkg_resources
        # keep a working platform.system()/uname().
        f"--add-data={_stdlib_platform.__file__}{os.pathsep}stdlib_shim",

        # Add all src files to root (not in src/ subfolder)
        f"--add-data={SRC_DIR}{os.pathsep}.",

        # Add config files (common dictionary, presets, examples)
        f"--add-data={PROJECT_ROOT / 'config'}{os.pathsep}config",

        # Hidden imports
        "--hidden-import=hal",
        "--hidden-import=voice_transcriber",
        "--hidden-import=voice_transcriber.main",
        "--hidden-import=voice_transcriber.hal",
        "--hidden-import=voice_transcriber.audio_state",
        "--hidden-import=voice_transcriber.accent",
        "--hidden-import=voice_transcriber.check_devices",
        "--hidden-import=voice_transcriber.console_text",
        "--hidden-import=voice_transcriber.control",
        "--hidden-import=voice_transcriber.dictionary",
        "--hidden-import=voice_transcriber.doctor",
        "--hidden-import=voice_transcriber.hotkeys",
        "--hidden-import=voice_transcriber.logging_setup",
        "--hidden-import=voice_transcriber.micro_batcher",
        "--hidden-import=voice_transcriber.model_backend",
        "--hidden-import=voice_transcriber.model_download",
        "--hidden-import=voice_transcriber.notifications",
        "--hidden-import=voice_transcriber.post_processor",
        "--hidden-import=voice_transcriber.stats",
        "--hidden-import=voice_transcriber.t2",
        "--hidden-import=voice_transcriber.transcribe2",
        "--hidden-import=voice_transcriber.transcribe_cohere",
        "--hidden-import=voice_transcriber.transcribe_parakeet",
        "--hidden-import=voice_transcriber.backend_registry",
        "--hidden-import=voice_transcriber.diarization",
        "--hidden-import=voice_transcriber.paths",
        "--hidden-import=voice_transcriber.slm_client",
        "--hidden-import=voice_transcriber.releases",
        "--hidden-import=voice_transcriber.llama_server_bundle",
        "--hidden-import=voice_transcriber.diarize",
        "--hidden-import=voice_transcriber.diarizers.sherpa_onnx",
        "--hidden-import=voice_transcriber.formatter",
        "--hidden-import=voice_transcriber.formatters.llama_server",
        "--hidden-import=voice_transcriber.formatters.noop",
        "--hidden-import=voice_transcriber.meeting",
        "--hidden-import=voice_transcriber.platform.windows.autostart",
        "--hidden-import=voice_transcriber.platform.windows.single_instance",
        "--hidden-import=voice_transcriber.platform.windows.overlay",
        "--hidden-import=voice_transcriber.platform.windows.tray",
        "--hidden-import=voice_transcriber.meeting_pipeline",
        "--hidden-import=voice_transcriber.tui",
        "--hidden-import=voice_transcriber.tui_ratatui",
        "--hidden-import=voice_transcriber.platform.windows",
        "--hidden-import=voice_transcriber.platform.windows.hotkeys",
        "--hidden-import=voice_transcriber.platform.windows.clipboard",
        "--hidden-import=voice_transcriber.platform.windows.audio_cues",
        "--hidden-import=voice_transcriber.platform.windows.notifications",
        "--collect-submodules=voice_transcriber",
        "--hidden-import=platform.windows",
        "--hidden-import=platform.windows.hotkeys",
        "--hidden-import=platform.windows.clipboard",
        "--hidden-import=platform.windows.audio_cues",
        "--hidden-import=platform.windows.notifications",
        "--hidden-import=sounddevice",
        "--hidden-import=soundfile",
        "--hidden-import=numpy",
        "--hidden-import=torch",
        "--hidden-import=transformers",
        "--hidden-import=pynput",
        "--hidden-import=keyboard",
        "--hidden-import=pyperclip",
        "--hidden-import=psutil",
        "--hidden-import=tqdm",
        "--hidden-import=tokenizers",
        "--hidden-import=onnxruntime",
        "--hidden-import=sherpa_onnx",
        "--hidden-import=rich",

        # Collect all for these packages
        "--collect-all=transformers",
        "--collect-all=torch",
        "--collect-all=tokenizers",

        # Disable upx to avoid issues with large bundles
        "--upx-dir=NONE",
    ]

    icon_path = PROJECT_ROOT / "packaging" / "icon.ico"
    if icon_path.exists():
        pyinstaller_args.append(f"--icon={icon_path}")

    # Filter out NONE value
    pyinstaller_args = [arg for arg in pyinstaller_args if arg != "NONE"]

    print("Running:", " ".join(pyinstaller_args[:10]) + "...")
    print()

    result = subprocess.run(pyinstaller_args, cwd=PROJECT_ROOT)

    if result.returncode == 0:
        print()
        print("=" * 60)
        print("BUILD SUCCESSFUL!")
        print("=" * 60)
        dist_dir = DIST_DIR / "VoiceTranscriber"
        llama_dir = resolve_llama_server()
        if llama_dir and dist_dir.exists():
            bundle_llama_server(llama_dir, dist_dir)
        exe_path = dist_dir / "VoiceTranscriber.exe"
        if exe_path.exists():
            size_mb = exe_path.stat().st_size / (1024 * 1024)
            print(f"EXE created: {exe_path}")
            print(f"Size: {size_mb:.1f} MB ({size_mb/1024:.2f} GB)")
        print()
        print("To test offline:")
        print("1. Disconnect network / enable Airplane mode")
        print("2. Run the EXE")
        print("3. Test recording with Cohere")
    else:
        print()
        print("=" * 60)
        print("BUILD FAILED!")
        print("=" * 60)
        sys.exit(result.returncode)

def main():
    parser = argparse.ArgumentParser(
        description="Build the standalone Windows Voice Transcriber EXE.")
    parser.add_argument(
        "--no-model", action="store_true",
        help="Do not bundle the Cohere weights. The app installs them on first "
             "run, from the release assets or an offline model-bundle/ directory "
             "(VT_MODEL_SOURCE_DIR) - the airgapped-friendly build.")
    parser.add_argument(
        "--noconsole", "--windowed", action="store_true", dest="noconsole",
        help="Build windowed/headless without forcing an open console window.")
    args = parser.parse_args()

    print("Voice Transcriber - Offline Build Script")
    print("=" * 60)
    print()

    # Check prerequisites
    try:
        import PyInstaller
        print(f"PyInstaller version: {PyInstaller.__version__}")
    except ImportError:
        print("ERROR: PyInstaller not installed")
        print("Install with: pip install pyinstaller")
        sys.exit(1)

    if args.no_model:
        print("--no-model: bundling no weights (installed on first run).")
        if MODELS_DIR.exists():
            shutil.rmtree(MODELS_DIR)
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
    else:
        # Ensure models are cached
        ensure_cached_models()

        # Skip prompt - auto-proceed
        print("\nProceeding with build automatically...")

        # Prepare bundled models
        prepare_bundled_models()

    # Build the EXE
    build_exe(noconsole=args.noconsole)

if __name__ == "__main__":
    main()
