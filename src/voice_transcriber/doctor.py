"""Voice Transcriber Doctor / Diagnostic Self-Check.

Inspects the host environment, audio capture devices, hotkey and injection
permissions, model weight caches, and running daemon state across Linux, macOS,
Windows, and WSL.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

import audio_state
import hal


def check_platform() -> Dict[str, Any]:
    import platform as sys_platform

    detected = hal.detect_platform()
    return {
        "ok": True,
        "platform": detected,
        "system": sys_platform.system(),
        "release": sys_platform.release(),
        "machine": sys_platform.machine(),
        "python": sys_platform.python_version(),
    }


def check_torch_acceleration() -> Dict[str, Any]:
    try:
        import torch

        version = torch.__version__
        has_cuda = torch.cuda.is_available()
        has_mps = hasattr(torch.backends, "mps") and torch.backends.mps.is_available()

        if has_cuda:
            device = "cuda"
            device_name = torch.cuda.get_device_name(0)
        elif has_mps:
            device = "mps"
            device_name = "Apple Silicon GPU (MPS)"
        else:
            device = "cpu"
            device_name = "CPU"

        return {
            "ok": True,
            "version": version,
            "device": device,
            "device_name": device_name,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "device": "unknown",
            "device_name": "Unavailable",
        }


def check_audio_devices() -> Dict[str, Any]:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        input_devices: List[Dict[str, Any]] = []
        default_in = None

        try:
            default_in = sd.default.device[0]
        except Exception:
            pass

        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                supported_16k = True
                try:
                    sd.check_input_settings(device=i, samplerate=16000, channels=1)
                except Exception:
                    supported_16k = False

                input_devices.append({
                    "id": i,
                    "name": d.get("name"),
                    "channels": d.get("max_input_channels"),
                    "samplerate": d.get("default_samplerate"),
                    "supports_16k": supported_16k,
                    "is_default": (i == default_in),
                })

        has_inputs = len(input_devices) > 0
        return {
            "ok": has_inputs,
            "count": len(input_devices),
            "default_device": default_in,
            "devices": input_devices,
            "error": None if has_inputs else "No audio input devices found",
        }
    except Exception as e:
        return {
            "ok": False,
            "count": 0,
            "devices": [],
            "error": f"Failed to query audio devices: {e}",
        }


def check_microphone_mute(fix: bool = False) -> Dict[str, Any]:
    """Report (and optionally repair) a muted default microphone.

    A muted source is the one capture failure that looks exactly like success:
    the device opens, the channel count and sample rate are right, and every
    sample is zero. Discord surfaces it ("no audio input detected"); nothing else
    does — which is why this is a first-class doctor check and not a footnote.

    Read-only unless ``fix`` is set. A mute can be deliberate, so repairing one
    is always the caller's explicit choice (``doctor --fix``).
    """
    state = audio_state.describe_state()
    if not state["supported"]:
        return {
            "ok": True,
            "checked": False,
            "muted": None,
            "source": None,
            "fixed": False,
            "fix_command": state["fix_command"],
            "detail": "Host has no wpctl (not a PipeWire platform); mute state not checked.",
        }

    muted = state["muted"]
    source = state["name"]
    label = source or "the default input"
    result: Dict[str, Any] = {
        "ok": not muted,
        "checked": True,
        "muted": muted,
        "source": source,
        "fixed": False,
        "fix_command": state["fix_command"],
        "detail": None,
    }

    if muted is None:
        result["detail"] = "No default input to inspect right now (no mute state to report)."
        return result
    if not muted:
        result["detail"] = f"Default input '{label}' is live."
        return result
    if fix:
        if audio_state.unmute_default_source():
            result.update(
                ok=True,
                muted=False,
                fixed=True,
                detail=f"Unmuted '{label}' (it was muted).",
            )
        else:
            result["detail"] = (
                f"Default input '{label}' is MUTED and wpctl could not unmute it — "
                f"run: {state['fix_command']}"
            )
        return result

    result["detail"] = (
        f"Default input '{label}' is MUTED — every app reading it records pure silence "
        f"(Discord calls this \"no audio input detected\")."
    )
    return result


def check_bluetooth_profile_policy() -> Dict[str, Any]:
    """Advisory: WirePlumber's Bluetooth headset-profile autoswitch policy.

    The app borrows this setting while recording (so a Bluetooth headset can serve
    as a microphone) and restores the previous value on exit. Installs that ran a
    version *before* that change left ``false`` recorded permanently — the app
    cannot know whether the user or its former self set it, so this only reports,
    with the command that resets it.

    The symptom is a headset that never switches to hands-free, which some
    applications surface as "no microphone at all". It never fails the run: a user
    may have disabled autoswitch deliberately.
    """
    if not shutil.which("wpctl"):
        return {
            "ok": True,
            "checked": False,
            "value": None,
            "detail": "Host has no wpctl (not a PipeWire platform); not checked.",
        }

    import t2

    current = t2.get_wireplumber_bt_autoswitch()
    if current is None:
        return {
            "ok": True,
            "checked": False,
            "value": None,
            "detail": "Could not read the WirePlumber setting; not checked.",
        }
    if current:
        return {
            "ok": True,
            "checked": True,
            "value": True,
            "detail": "Bluetooth headset-profile autoswitch is enabled.",
        }
    return {
        "ok": True,  # advisory: disabling it may be deliberate
        "checked": True,
        "value": False,
        "detail": (
            "bluetooth.autoswitch-to-headset-profile is disabled, so a Bluetooth "
            "headset will not switch to hands-free when an app opens its "
            "microphone (some apps then report no microphone at all). Versions of "
            "this app before 1.2.1 disabled it permanently; if you did not do that "
            "on purpose, restore the WirePlumber default with:"
        ),
        "fix_command": "wpctl settings -s bluetooth.autoswitch-to-headset-profile true",
    }


def check_hotkeys_and_permissions(plat: str) -> Dict[str, Any]:
    status = {"ok": True, "details": [], "warnings": [], "errors": []}

    if plat == hal.LINUX:
        # Check /dev/input
        dev_input = Path("/dev/input")
        if dev_input.exists():
            readable_events = list(dev_input.glob("event*"))
            can_read_any = any(os.access(p, os.R_OK) for p in readable_events) if readable_events else False
            if can_read_any or os.geteuid() == 0:
                status["details"].append("Input event device access is available")
            else:
                status["ok"] = False
                status["errors"].append(
                    "Cannot read /dev/input/event*. Add your user to the 'input' group:\n"
                    "  sudo usermod -aG input $USER  (then log out and log back in)"
                )
        # Check /dev/uinput
        uinput = Path("/dev/uinput")
        if uinput.exists() and os.access(uinput, os.W_OK | os.R_OK):
            status["details"].append("/dev/uinput is writable")
        elif os.geteuid() == 0:
            status["details"].append("Running as root (uinput accessible)")
        else:
            status["warnings"].append(
                "/dev/uinput not directly writable by user. Synthetic typing may require clipboard fallback or udev rules."
            )

    elif plat == hal.MACOS:
        # Load the backend through the HAL, which registers the package under the
        # private name `vt_platform`. A bare `from platform.macos.hotkeys import
        # ...` resolved to the *stdlib* `platform` module instead and raised
        # "No module named 'platform.macos'; 'platform' is not a package" —
        # invisible on Linux, because this branch is darwin-only.
        check_accessibility_permissions = hal.load_backend(
            "macos", "hotkeys"
        ).check_accessibility_permissions

        trusted = check_accessibility_permissions()
        if trusted:
            status["details"].append("macOS Accessibility permissions granted")
        else:
            status["ok"] = False
            status["errors"].append(
                "macOS Accessibility permission is not granted.\n"
                "Grant permission in: System Settings -> Privacy & Security -> Accessibility"
            )

    elif plat == hal.WINDOWS:
        status["details"].append("Windows Win32 hotkey / pynput hooks available")

    elif plat == hal.WSL:
        powershell = shutil.which("powershell.exe")
        if powershell:
            status["details"].append("Windows host PowerShell interop available for hotkeys")
        else:
            status["ok"] = False
            status["errors"].append("powershell.exe not found on PATH in WSL")

    return status


def check_clipboard_and_typing(plat: str) -> Dict[str, Any]:
    tools = {}
    if plat == hal.LINUX:
        tools["wl-copy"] = bool(shutil.which("wl-copy"))
        tools["xclip"] = bool(shutil.which("xclip"))
        tools["ydotool"] = bool(shutil.which("ydotool"))
        tools["xdotool"] = bool(shutil.which("xdotool"))
        has_clipboard = tools["wl-copy"] or tools["xclip"]
        has_typing = tools["ydotool"] or tools["xdotool"]
        return {
            "ok": has_clipboard,
            "has_clipboard": has_clipboard,
            "has_typing": has_typing,
            "tools": tools,
            "recommendation": (
                None if (has_clipboard and has_typing)
                else "Install wl-clipboard/xclip and ydotool/xdotool for optimal text injection."
            ),
        }
    elif plat == hal.MACOS:
        tools["pbcopy"] = bool(shutil.which("pbcopy"))
        tools["osascript"] = bool(shutil.which("osascript"))
        return {
            "ok": tools["pbcopy"],
            "has_clipboard": tools["pbcopy"],
            "has_typing": tools["osascript"],
            "tools": tools,
            "recommendation": None,
        }
    elif plat == hal.WINDOWS:
        return {
            "ok": True,
            "has_clipboard": True,
            "has_typing": True,
            "tools": {"pyperclip": True, "SendInput": True},
            "recommendation": None,
        }
    elif plat == hal.WSL:
        tools["clip.exe"] = bool(shutil.which("clip.exe"))
        tools["powershell.exe"] = bool(shutil.which("powershell.exe"))
        return {
            "ok": tools["clip.exe"],
            "has_clipboard": tools["clip.exe"],
            "has_typing": tools["powershell.exe"],
            "tools": tools,
            "recommendation": None,
        }
    return {"ok": True, "tools": {}}


def check_model_weights() -> Dict[str, Any]:
    try:
        import model_download

        model_dir = Path(model_download.cohere_models_dir())
        is_cached = model_download.is_local_model_complete(str(model_dir))
        size_mb = 0.0
        if model_dir.exists():
            size_mb = sum(f.stat().st_size for f in model_dir.glob("*") if f.is_file()) / (1024 * 1024)

        return {
            "ok": True,
            "cached": is_cached,
            "path": str(model_dir),
            "size_mb": round(size_mb, 1),
            "message": (
                f"Model weights verified ({size_mb:.1f} MB)"
                if is_cached
                else f"Weights not downloaded yet. Run setup (setup.bat / ./setup.sh) or let VT auto-download on first launch to {model_dir}"
            ),
        }
    except Exception as e:
        return {
            "ok": False,
            "cached": False,
            "path": "unknown",
            "size_mb": 0.0,
            "message": f"Could not verify model weights: {e}",
        }


def check_daemon() -> Dict[str, Any]:
    try:
        import control

        resp = control.send_command("ping", timeout=0.5)
        if resp.get("ok"):
            return {
                "running": True,
                "pid": resp.get("pid"),
                "socket": control.default_socket_path(),
            }
    except Exception:
        pass
    return {"running": False, "pid": None, "socket": None}


def run_doctor(json_format: bool = False, stream=None, fix: bool = False) -> Dict[str, Any]:
    """Execute the full doctor diagnostic check.

    ``fix`` repairs what doctor can repair on its own — currently: unmute a muted
    default microphone. Everything else stays read-only.
    """
    out = stream or sys.stdout

    plat_info = check_platform()
    plat = plat_info["platform"]
    torch_info = check_torch_acceleration()
    audio_info = check_audio_devices()
    mic_mute_info = check_microphone_mute(fix=fix)
    bt_info = check_bluetooth_profile_policy()
    hotkey_info = check_hotkeys_and_permissions(plat)
    clip_info = check_clipboard_and_typing(plat)
    model_info = check_model_weights()
    daemon_info = check_daemon()

    all_ok = (
        plat_info["ok"]
        and torch_info["ok"]
        and audio_info["ok"]
        and mic_mute_info["ok"]
        and hotkey_info["ok"]
        and clip_info["ok"]
    )

    report = {
        "ok": all_ok,
        "platform": plat_info,
        "acceleration": torch_info,
        "audio": audio_info,
        "microphone": mic_mute_info,
        "bluetooth_profile": bt_info,
        "hotkeys": hotkey_info,
        "clipboard_and_typing": clip_info,
        "model_weights": model_info,
        "daemon": daemon_info,
    }

    if json_format:
        if stream:
            json.dump(report, out, indent=2)
        return report

    print("\n" + "=" * 60, file=out)
    print("      🩺 Voice Transcriber System Diagnostics (Doctor)", file=out)
    print("=" * 60, file=out)

    # 1. Platform
    print(
        f"\n[✓] Platform: {plat.upper()} ({plat_info['system']} {plat_info['release']} {plat_info['machine']}) | Python {plat_info['python']}",
        file=out,
    )

    # 2. PyTorch & Hardware Acceleration
    if torch_info["ok"]:
        dev = torch_info["device"].upper()
        name = torch_info["device_name"]
        print(f"[✓] Inference Engine: PyTorch {torch_info['version']} ({dev}: {name})", file=out)
    else:
        print(f"[✗] Inference Engine: PyTorch error ({torch_info.get('error')})", file=out)

    # 3. Audio Devices
    if audio_info["ok"]:
        default_dev = next((d["name"] for d in audio_info["devices"] if d["is_default"]), "Default")
        print(f"[✓] Audio Capture: {audio_info['count']} input device(s) found (Active: {default_dev})", file=out)
    else:
        print(f"[✗] Audio Capture: {audio_info.get('error')}", file=out)

    # 3b. Microphone Mute State — invisible capture failure (device healthy, samples all zero)
    if mic_mute_info["checked"]:
        mark = "✓" if mic_mute_info["ok"] else "✗"
        print(f"[{mark}] Microphone Mute: {mic_mute_info['detail']}", file=out)
        if not mic_mute_info["ok"]:
            print(f"    Fix: {mic_mute_info['fix_command']}   (or re-run: doctor --fix)", file=out)

    # 3c. Bluetooth headset-profile policy — advisory, never fails the run.
    if bt_info["checked"]:
        mark = "✓" if bt_info["value"] else "!"
        print(f"[{mark}] Bluetooth Headset: {bt_info['detail']}", file=out)
        if not bt_info["value"]:
            print(f"    Fix: {bt_info['fix_command']}", file=out)

    # 4. Hotkeys & Permissions
    if hotkey_info["ok"]:
        for d in hotkey_info["details"]:
            print(f"[✓] Hotkeys & Permissions: {d}", file=out)
    else:
        for e in hotkey_info["errors"]:
            print(f"[✗] Hotkeys & Permissions: {e}", file=out)
    for w in hotkey_info["warnings"]:
        print(f"[!] Warning: {w}", file=out)

    # 5. Text Injection & Clipboard
    if clip_info["ok"]:
        tools_str = ", ".join(k for k, v in clip_info["tools"].items() if v)
        print(f"[✓] Clipboard & Typing: Available tools: {tools_str}", file=out)
    else:
        print("[✗] Clipboard & Typing: No system clipboard or injection tool found", file=out)
    if clip_info.get("recommendation"):
        print(f"[!] Suggestion: {clip_info['recommendation']}", file=out)

    # 6. Model Weights
    if model_info["cached"]:
        print(f"[✓] Model Weights: Cached at {model_info['path']} ({model_info['size_mb']} MB)", file=out)
    else:
        print(f"[!] Model Weights: {model_info['message']}", file=out)

    # 7. Engine Daemon Status
    if daemon_info["running"]:
        print(f"[✓] Active Instance: Engine running (PID {daemon_info['pid']})", file=out)
    else:
        print("[i] Active Instance: No background engine instance currently running", file=out)

    print("\n" + "-" * 60, file=out)
    if all_ok:
        print("✓ System status: READY. You can start Voice Transcriber!", file=out)
    else:
        print("✗ System status: ISSUES DETECTED. Please review the items marked [✗] above.", file=out)
    print("-" * 60 + "\n", file=out)

    return report
