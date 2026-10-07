#!/usr/bin/env python3
"""Voice Transcriber - the one implementation of the developer/user verbs.

Every platform script (`platforms/<os>/<verb>.sh`, `platforms/windows/<verb>.ps1`)
bootstraps the runtime with that OS's native tooling and then dispatches here:

    exec python3 tools/vt_dev.py setup --no-dev
    exec python3 tools/vt_dev.py run -- status

Keeping the verb bodies in one stdlib-only module means a fix lands on every
platform at once. The Nix platform does not use this file - it calls the flake
(nix run / nix develop / nix build), which is its native tooling.

This module is deliberately dependency-free: it must run on a bare host Python
before the project virtual environment exists.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
TOOLS_DIR = REPO_ROOT / "tools"
DEFAULT_VENV = REPO_ROOT / ".venv"
BUILD_OFFLINE = REPO_ROOT / "platforms" / "windows" / "build_offline.py"

IS_WINDOWS = os.name == "nt"
IS_DARWIN = sys.platform == "darwin"

_VALID_TIERS = ("all", "shared", "platform", "e2e", "model", "perf", "verify",
                "latency", "benchmark", "windows", "linux", "macos", "wsl")

_COLOR = sys.stdout.isatty()


def _paint(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def step(msg: str) -> None:
    print(_paint("36;1", f"\n[{msg}]"))


def ok(msg: str) -> None:
    print(_paint("32", "[OK] ") + msg)


def warn(msg: str) -> None:
    print(_paint("33", "[WARN] ") + msg)


def err(msg: str) -> None:
    print(_paint("31", "[ERROR] ") + msg, file=sys.stderr)


def _env(extra: dict | None = None) -> dict:
    """Environment for child Python processes.

    Forces UTF-8 so ``model_download``'s check marks (U+2713/U+2715) do not
    crash on a legacy Windows code page - which would otherwise make the model
    verify look like a failure.
    """
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if extra:
        env.update(extra)
    return env


def sh(cmd: list[str], **kw) -> int:
    """Run a command, echoing it, and return the exit code."""
    kw.setdefault("env", _env())
    print(_paint("90", "$ " + " ".join(str(c) for c in cmd)))
    try:
        return subprocess.call([str(c) for c in cmd], **kw)
    except FileNotFoundError:
        err(f"not found: {cmd[0]}")
        return 127


# --------------------------------------------------------------------------- #
# Interpreter / environment discovery
# --------------------------------------------------------------------------- #
def venv_python(venv_dir: Path | None = None) -> Path | None:
    # Under the Nix toolchain the flake provides the interpreter and every
    # dependency, so a stale repo .venv must be ignored.
    if venv_dir is None and os.environ.get("VT_TOOLCHAIN") == "nix":
        return None
    d = Path(venv_dir) if venv_dir else DEFAULT_VENV
    p = d / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return p if p.exists() else None


def launch_python(venv_override: Path | None = None) -> Path:
    """Interpreter for run/test/build: the repo venv if present, else host."""
    v = venv_python(venv_override)
    return v if v else Path(sys.executable)


def have_module(py: Path, name: str) -> bool:
    code = (
        "import importlib.util,sys;"
        f"sys.exit(0 if importlib.util.find_spec({name!r}) else 1)"
    )
    return subprocess.call([str(py), "-c", code],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           env=_env()) == 0


def requirements_files() -> tuple[Path, Path]:
    """(runtime, dev) requirements for this OS, matching the old scripts."""
    if IS_WINDOWS:
        win = REPO_ROOT / "platforms" / "windows"
        return win / "requirements.txt", win / "requirements-dev.txt"
    return REPO_ROOT / "requirements.txt", REPO_ROOT / "requirements-dev.txt"


# --------------------------------------------------------------------------- #
# setup
# --------------------------------------------------------------------------- #
def ensure_venv() -> Path:
    existing = venv_python()
    if existing:
        return existing
    step("SETUP creating virtual environment (.venv)")
    if sh([sys.executable, "-m", "venv", str(DEFAULT_VENV)]) != 0:
        raise SystemExit("failed to create the virtual environment")
    created = venv_python()
    if not created:
        raise SystemExit(f"venv created but {created} is missing")
    return created


def install_torch(py: Path) -> None:
    """Platform-specific PyTorch. Windows pins torch in requirements.txt."""
    if IS_WINDOWS:
        return
    if IS_DARWIN:
        step("SETUP installing PyTorch for macOS (MPS on Apple Silicon)")
        sh([py, "-m", "pip", "install", "--quiet", "torch"])
        return
    nvidia = shutil.which("nvidia-smi")
    if nvidia and subprocess.call([nvidia], stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) == 0:
        step("SETUP NVIDIA GPU detected; installing CUDA PyTorch")
        for cu in ("cu124", "cu121"):
            url = f"https://download.pytorch.org/whl/{cu}"
            if sh([py, "-m", "pip", "install", "--quiet", "torch",
                   "--index-url", url]) == 0:
                ok(f"PyTorch with CUDA installed ({cu})")
                return
        warn("CUDA install failed; falling back to the default wheel")
    sh([py, "-m", "pip", "install", "--quiet", "torch"])


def install_requirements(py: Path, no_dev: bool) -> None:
    req, dev_req = requirements_files()
    sh([py, "-m", "pip", "install", "--quiet", "--upgrade",
        "pip", "setuptools", "wheel"])
    if not no_dev and dev_req.is_file():
        step("SETUP installing project + development dependencies")
        if sh([py, "-m", "pip", "install", "--quiet", "-r", str(dev_req)]) != 0:
            raise SystemExit("dependency installation failed")
        return
    step("SETUP installing project dependencies")
    if not req.is_file():
        raise SystemExit(f"requirements file not found: {req}")
    if sh([py, "-m", "pip", "install", "--quiet", "-r", str(req)]) != 0:
        raise SystemExit("dependency installation failed")


def check_system_deps() -> None:
    """Advisory only: never installs system packages, just points at them."""
    if IS_DARWIN:
        if not shutil.which("brew"):
            warn("Homebrew not detected (recommended: https://brew.sh)")
        elif shutil.which("portaudio") is None and not any(
                Path(p).exists() for p in
                ("/opt/homebrew/include/portaudio.h",
                 "/usr/local/include/portaudio.h")):
            warn("PortAudio not installed. Run: brew install portaudio")
        else:
            ok("Homebrew and PortAudio look present")
        return
    if IS_WINDOWS or sys.platform != "linux":
        return

    wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or \
        os.environ.get("XDG_SESSION_TYPE") == "wayland"
    missing: list[str] = []
    if wayland:
        for tool, pkg in (("wl-copy", "wl-clipboard"), ("ydotool", "ydotool")):
            if not shutil.which(tool):
                missing.append(pkg)
    else:
        if not (shutil.which("xclip") or shutil.which("xsel")):
            missing.append("xclip")
        if not shutil.which("xdotool"):
            missing.append("xdotool")
    if not missing:
        ok("Recommended system CLI utilities are available")
        return
    warn(f"Missing recommended utilities: {' '.join(missing)}")
    print("    Install them with your package manager; for example:")
    distro = ""
    for line in _os_release().splitlines():
        if line.startswith("ID="):
            distro = line.split("=", 1)[1].strip().strip('"')
    hints = {
        "ubuntu": "sudo apt-get install -y libportaudio2 libasound2-plugins "
                  "xclip wl-clipboard xdotool ydotool",
        "debian": "sudo apt-get install -y libportaudio2 libasound2-plugins "
                  "xclip wl-clipboard xdotool ydotool",
        "fedora": "sudo dnf install -y portaudio alsa-lib xclip wl-clipboard "
                  "xdotool ydotool",
        "arch": "sudo pacman -S --needed portaudio alsa-lib xclip wl-clipboard "
                "xdotool ydotool",
        "opensuse": "sudo zypper install -y portaudio alsa xclip wl-clipboard "
                    "xdotool ydotool",
    }
    print("    " + hints.get(distro, "Install portaudio, alsa, xclip/wl-clipboard, "
                                     "xdotool/ydotool"))


def _os_release() -> str:
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def check_permissions() -> None:
    if IS_DARWIN:
        ok("macOS: grant your terminal Accessibility + Microphone access")
        return
    if IS_WINDOWS or sys.platform != "linux":
        return
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    try:
        import grp
        # Mirror the app's authoritative check (main.check_permissions): the
        # *effective* group list, not the login name in gr_mem.
        in_group = grp.getgrnam("input").gr_gid in os.getgroups()
    except Exception:
        in_group = False
    if in_group:
        ok(f"User '{user}' is in the 'input' group")
    else:
        warn(f"User '{user}' is NOT in the 'input' group (needed for hotkeys)")
        print(f"  Run: sudo usermod -a -G input {user}   (then log out/in)")
    uinput = Path("/dev/uinput")
    if uinput.exists() and not os.access(uinput, os.W_OK):
        warn("/dev/uinput is not writable; synthetic keys may need a udev rule")


def model_dest() -> Path | None:
    """Where to install weights: ``<repo>/models/cohere`` when the checkout is
    writable, else ``None`` so ``model_download`` picks the per-user data dir
    (the Nix store and a frozen AppImage are read-only).
    """
    if os.access(REPO_ROOT, os.W_OK):
        return REPO_ROOT / "models" / "cohere"
    return None


def ensure_model(no_model: bool) -> None:
    dest = model_dest()
    dl = SRC_DIR / "model_download.py"
    py = launch_python()
    where = str(dest) if dest else "the per-user data dir"
    step("SETUP model weights")
    verify = [str(py), str(dl), "--verify-only"]
    if dest is not None:
        verify += ["--dest", str(dest)]
    if subprocess.call(verify, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=_env()) == 0:
        ok(f"Model ready: {where}")
        return
    if no_model:
        warn(f"No model and --no-model was passed. Drop weights in "
             f"{REPO_ROOT / 'models'} to run offline.")
        return
    install = [py, str(dl)]
    if dest is not None:
        install += ["--dest", str(dest)]
    if sh(install) != 0:
        err("Model download failed. For an airgapped host, put a split bundle "
            f"in {REPO_ROOT / 'models'}.")


def cmd_setup(args: list[str]) -> int:
    no_dev, no_model, no_deps = False, False, False
    for a in args:
        if a == "--no-dev":
            no_dev = True
        elif a == "--no-model":
            no_model = True
        elif a == "--no-deps":
            # Used by the Nix platform: the flake already provides the deps.
            no_deps = True
        elif a in ("-h", "--help"):
            print("usage: vt_dev.py setup [--no-dev] [--no-model] [--no-deps]")
            return 0
        else:
            err(f"unknown option: {a}")
            return 2
    check_system_deps()
    if not no_deps:
        py = ensure_venv()
        install_torch(py)
        install_requirements(py, no_dev)
        ok("Dependencies installed")
    check_permissions()
    ensure_model(no_model)
    print("\nSetup complete. Launch with: run")
    return 0


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #
def _split_run_args(args: list[str]):
    """Parse vt-level flags; everything after the first app arg is forwarded.

    Matches the old run.ps1: --model-dir PATH, --venv PATH, --no-model, then the
    rest (a control verb or app flags) goes to main.py untouched.
    """
    model_dir = None
    venv_override = None
    no_model = False
    app_args: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--model-dir":
            if i + 1 >= len(args):
                raise SystemExit("--model-dir requires a path")
            model_dir = args[i + 1]
            i += 2
        elif a == "--venv":
            if i + 1 >= len(args):
                raise SystemExit("--venv requires a path")
            venv_override = Path(args[i + 1])
            i += 2
        elif a == "--no-model":
            no_model = True
            i += 1
        elif a in ("-h", "--help"):
            print("usage: vt_dev.py run [--model-dir PATH] [--venv PATH] "
                  "[--no-model] [verb ...]")
            raise SystemExit(0)
        else:
            app_args = args[i:]
            break
    return model_dir, venv_override, no_model, app_args


def cmd_run(args: list[str]) -> int:
    model_dir, venv_override, no_model, app_args = _split_run_args(args)
    if model_dir:
        os.environ["VT_MODEL_DIR"] = model_dir
    if no_model:
        os.environ["VT_AUTO_DOWNLOAD_MODEL"] = "0"
    py = launch_python(venv_override)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PIP_DISABLE_PIP_VERSION_WARNING", "1")
    env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if IS_WINDOWS:
        env.setdefault("VT_PLATFORM", "windows")
        env.setdefault("VT_MODEL_BACKEND", "cohere")
    cmd = [str(py), str(SRC_DIR / "main.py"), *app_args]
    if IS_WINDOWS:
        return subprocess.call(cmd, env=env)
    os.execvpe(str(py), cmd, env)
    return 0  # unreachable


# --------------------------------------------------------------------------- #
# test
# --------------------------------------------------------------------------- #
def platform_tier() -> tuple[str, str]:
    if IS_WINDOWS:
        return "tests/windows", "Windows"
    if IS_DARWIN:
        return "tests/macos", "macOS"
    in_wsl = bool(os.environ.get("WSL_DISTRO_NAME")) or \
        Path("/mnt/wslg").exists() or "microsoft" in _proc_version()
    if in_wsl:
        return "tests/wsl", "WSL"
    return "tests/linux", "Linux"


def _proc_version() -> str:
    try:
        return Path("/proc/version").read_text(errors="replace").lower()
    except OSError:
        return ""


def ensure_dev_tool(py: Path, module: str, label: str, no_dev: bool) -> bool:
    if have_module(py, module):
        return True
    if no_dev:
        err(f"{label} is not installed and --no-dev was passed.")
        return False
    _, dev_req = requirements_files()
    if not dev_req.is_file():
        err(f"{label} is missing and {dev_req} was not found.")
        return False
    step(f"TEST installing {label}")
    return sh([py, "-m", "pip", "install", "-r", str(dev_req)]) == 0


def cmd_test(args: list[str]) -> int:
    no_dev = "--no-dev" in args
    args = [a for a in args if a != "--no-dev"]
    py = launch_python()
    tier = args[0] if args and not args[0].startswith("-") else "all"
    rest = args[1:] if args and not args[0].startswith("-") else list(args)
    if tier not in _VALID_TIERS:
        # Not a tier name: treat every arg as a raw pytest argument.
        rest, tier = args, "all"

    plat_tier, plat_name = platform_tier()
    step(f"TEST environment check ({_py_version(py)})")
    if venv_python():
        print(f"Venv:    {DEFAULT_VENV}")
    else:
        warn(f"No .venv; using host Python at {py}")
    model_dl = SRC_DIR / "model_download.py"
    have_model = subprocess.call(
        [str(py), str(model_dl), "--verify-only"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=_env()) == 0
    if have_model:
        ok("Model verified")
    else:
        warn("Model missing - run setup (or drop it in models/).")

    if tier == "verify":
        return 0 if have_model else 1
    if tier in ("model", "e2e"):
        targets = ["tests/e2e"]
    elif tier in ("perf", "latency", "benchmark"):
        targets = ["tests/e2e/test_latency_and_accuracy.py"]
    elif tier == "shared":
        targets = ["tests/shared"]
    elif tier == "platform":
        targets = [plat_tier]
    elif tier == "workspace" or tier == "all":
        targets = ["tests/shared", plat_tier]
    elif tier in ("windows", "linux", "macos", "wsl"):
        targets = [f"tests/{tier}"]
    else:
        targets = ["tests/shared", plat_tier]

    if not ensure_dev_tool(py, "pytest", "pytest", no_dev):
        return 1
    step(f"TEST running [{tier}] on {plat_name}: {' '.join(targets)}")
    return sh([py, "-m", "pytest", *targets, *rest])


def _py_version(py: Path) -> str:
    try:
        out = subprocess.run([str(py), "--version"], capture_output=True,
                             text=True, env=_env())
        return (out.stdout or out.stderr).strip()
    except OSError:
        return str(py)


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
def cmd_build(args: list[str]) -> int:
    bundle = "--bundle" in args
    extra = [a for a in args if a not in ("--bundle",)]
    if IS_WINDOWS:
        py = launch_python()
        if not ensure_dev_tool(py, "PyInstaller", "PyInstaller", False):
            return 1
        if not BUILD_OFFLINE.is_file():
            err(f"build script not found: {BUILD_OFFLINE}")
            return 1
        return sh([py, str(BUILD_OFFLINE), *extra])
    if not shutil.which("nix"):
        err("Nix is required to package on Linux/macOS.")
        print("Install Nix, or run from source: ./run.sh", file=sys.stderr)
        return 1
    if bundle:
        return sh(["nix", "bundle", "--bundler",
                   "github:ralismark/nix-appimage",
                   "--extra-experimental-features", "nix-command flakes",
                   ".#default", *extra])
    code = sh(["nix", "build", ".", *extra])
    if code == 0:
        ok(f"Built: {REPO_ROOT / 'result' / 'bin' / 'vt'}")
    return code


# --------------------------------------------------------------------------- #
# clean
# --------------------------------------------------------------------------- #
def cmd_clean(args: list[str]) -> int:
    clean_models = "--models" in args
    assume_yes = ("--yes" in args) or ("-y" in args)
    for a in args:
        if a not in ("--models", "--yes", "-y", "-h", "--help"):
            err(f"unknown option: {a}")
            return 2
    if "-h" in args or "--help" in args:
        print("usage: vt_dev.py clean [--models] [--yes]")
        return 0

    targets = [DEFAULT_VENV, REPO_ROOT / "build", REPO_ROOT / "dist",
               REPO_ROOT / "result"]
    if clean_models:
        targets.append(REPO_ROOT / "models")
    existing = [t for t in targets if t.exists() or t.is_symlink()]
    caches: list[Path] = []
    for root in ("src", "tests", "platforms", "scripts", "tools", "eval"):
        base = REPO_ROOT / root
        if base.is_dir():
            caches.extend(p for p in base.rglob("__pycache__") if p.is_dir())
    pytest_cache = REPO_ROOT / "tests" / ".pytest_cache"

    if not existing and not caches and not pytest_cache.exists():
        ok("Nothing to clean.")
        return 0

    print("Will remove:")
    for t in existing:
        print(f"  {t}")
    if caches:
        print(f"  {len(caches)} __pycache__ directories")
    if pytest_cache.exists():
        print(f"  {pytest_cache}")
    if not assume_yes:
        answer = input("Proceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            warn("Aborted.")
            return 1
    for t in existing:
        _rmtree(t)
        print(_paint("32", f"  removed {t}"))
    for c in caches:
        _rmtree(c)
    if pytest_cache.exists():
        _rmtree(pytest_cache)
    ok("Clean complete.")
    return 0


def _rmtree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except OSError:
            path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #
VERBS = {
    "setup": cmd_setup,
    "run": cmd_run,
    "test": cmd_test,
    "build": cmd_build,
    "clean": cmd_clean,
}

HELP = """\
Voice Transcriber - verb runner (one implementation, every platform)

usage: vt_dev.py <verb> [options]

  setup   prepare the machine: venv + dependencies + model
  run     launch the app (or forward a control verb)
  test    verify the environment, then run a pytest tier
  build   package the app (PyInstaller on Windows, nix build elsewhere)
  clean   remove generated state (.venv, build/, dist/, result, caches)

Options per verb: run `vt_dev.py <verb> --help`.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    verb, rest = argv[0], argv[1:]
    handler = VERBS.get(verb)
    if handler is None:
        err(f"unknown verb: {verb}")
        print(HELP)
        return 2
    try:
        return handler(rest)
    except SystemExit as exc:
        return int(exc.code) if exc.code is not None else 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
