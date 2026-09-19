#!/usr/bin/env python3
"""
Cross-Platform End-to-End Test Suite for Voice Transcriber (Phase 1).

This module is the executable specification for the Cross-Platform Convergence
& Automated HITL Testing Plan. It exercises:

  1. Platform detection + the Hardware/OS Abstraction Layer (HAL) output-sink
     contract for Linux, Windows Native, and WSL (``VT_PLATFORM`` override).
  2. Cross-platform clipboard / typing / audio-cue hooks (wl-copy, xclip,
     ydotool, pyperclip, Win32 ``keybd_event``, ``winsound``, ``clip.exe`` and
     the WSL socket JSON-RPC bridge).
  3. Synthetic hotkey triggering: Push-to-Talk and the Space hands-free latch,
     driven through the real audio pipeline.
  4. End-to-end synthetic audio ingestion (no microphone / no display) with
     latency and accuracy SLA gates.
  5. Silence / ambient-noise hallucination rejection.

Everything runs headless inside the Nix environment::

    nix develop --command python -m pytest tests/test_end_to_end_crossplatform.py -v

The platform adapters implemented here are *contract reference
implementations*. Phase 3 of the plan extracts them into ``src/platform/``;
the assertions in this file define exactly what those implementations must do.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import types
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

# ---------------------------------------------------------------------------
# Paths / source import
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = REPO_ROOT / "src"
TEST_AUDIO_DIR = REPO_ROOT / "tests" / "test_transcribe"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ---------------------------------------------------------------------------
# Plugin environment: keep BLAS single-threaded (same as tests/conftest.py) so
# the heavy ASR libraries never trip the FPU teardown crash under pytest.
# ---------------------------------------------------------------------------
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# Hard ceiling from the plan's SLA table: key-release -> clipboard/typing.
POST_RELEASE_LATENCY_BUDGET_SEC = 1.5
# Accuracy floor from the plan's SLA table.
ACCURACY_FLOOR = 0.80

# ===========================================================================
# 1. Platform detection (contract for Phase 3 ``src/platform/__init__.py``)
# ===========================================================================
LINUX, WSL, WINDOWS = "linux", "wsl", "windows"
VALID_PLATFORMS = (LINUX, WSL, WINDOWS)


def detect_platform() -> str:
    """
    Return the active target platform: ``linux`` | ``wsl`` | ``windows``.

    ``VT_PLATFORM`` (set by tests / CI / the Phase 3 launcher) always wins.
    When unset we auto-detect the host the same way ``hotkeys.is_running_in_wsl``
    does, so the code behaves identically whether or not the override is set.

    An unknown ``VT_PLATFORM`` value is a hard error: silently guessing across
    platforms is exactly the class of bug the HAL is meant to eliminate.
    """
    override = os.environ.get("VT_PLATFORM", "").strip().lower()
    if override:
        if override not in VALID_PLATFORMS:
            raise ValueError(
                f"Unknown VT_PLATFORM={override!r}; expected one of {VALID_PLATFORMS}"
            )
        return override

    if sys.platform.startswith("win"):
        return WINDOWS

    # WSL detection mirrors src/hotkeys.py::is_running_in_wsl()
    import platform as _platform

    release = _platform.uname().release.lower()
    if (
        os.path.exists("/mnt/wslg")
        or bool(os.environ.get("WSL_DISTRO_NAME"))
        or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop")
        or "microsoft" in release
    ):
        return WSL

    return LINUX


# ===========================================================================
# 2. Output-sink HAL contract implementations
# ===========================================================================
class OutputSink:
    """
    Base class for the clipboard / typing / audio-cue output sink.

    Phase 3 will split these into ``copy_text``, ``paste_keystrokes`` and an
    audio-cue player; the single-class form keeps the Phase 1 tests compact.
    """

    platform = "unknown"

    def __init__(self) -> None:
        # Test-visible recordings of the exact operations performed.
        self.copied: list[str] = []
        self.typed: list[str] = []
        self.cues: list[str] = []

    def copy_text(self, text: str) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def type_text(self, text: str) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    def play_cue(self, name: str) -> None:  # pragma: no cover - abstract
        raise NotImplementedError


class LinuxOutputSink(OutputSink):
    """Linux/Wayland/X11 sink: wl-copy/xclip clipboard + ydotool/xdotool typing."""

    platform = LINUX

    def copy_text(self, text: str) -> bool:
        payload = text.encode("utf-8")
        # Wayland first (matches src/t2.py), then X11 fallback.
        if shutil.which("wl-copy"):
            result = subprocess.run(["wl-copy"], input=payload, check=False)
            self.copied.append(text)
            return result.returncode == 0
        if shutil.which("xclip"):
            result = subprocess.run(
                ["xclip", "-selection", "clipboard"], input=payload, check=False
            )
            self.copied.append(text)
            return result.returncode == 0
        return False

    def type_text(self, text: str) -> bool:
        if shutil.which("ydotool"):
            subprocess.run(["ydotool", "type", "--", text], check=False)
            self.typed.append(text)
            return True
        if shutil.which("xdotool"):
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--", text], check=False
            )
            self.typed.append(text)
            return True
        # Last-resort fallback: leave it on the clipboard.
        return self.copy_text(text)

    def play_cue(self, name: str) -> None:
        sound_file = SRC_DIR / "sounds" / f"{name}.mp3"
        self.cues.append(name)
        for tool in ("paplay", "mpg123"):
            if shutil.which(tool):
                subprocess.Popen([tool, str(sound_file)], stderr=subprocess.DEVNULL)
                return


class WindowsOutputSink(OutputSink):
    """Native Windows sink: pyperclip clipboard, Win32 keybd_event, winsound cues."""

    platform = WINDOWS

    # System sound aliases used by ``notifications_windows.py``.
    _CUE_ALIASES = {
        "start": "SystemExclamation",
        "stop": "SystemAsterisk",
        "complete": "SystemExit",
    }

    def copy_text(self, text: str) -> bool:
        import pyperclip  # imported lazily so the Linux host never needs it

        pyperclip.copy(text)
        self.copied.append(text)
        return True

    def type_text(self, text: str) -> bool:
        # Win32 keystroke injection via user32.keybd_event (no extra deps).
        import ctypes as _ctypes

        user32 = _ctypes.windll.user32
        keyeventf_keyup = 0x0002
        shift_vk = 0x10
        for char in text:
            scan = user32.VkKeyScanW(ord(char))
            if scan == -1:
                continue
            vk = scan & 0xFF
            shift_needed = bool((scan >> 8) & 0x01)
            if shift_needed:
                user32.keybd_event(shift_vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, keyeventf_keyup, 0)
            if shift_needed:
                user32.keybd_event(shift_vk, 0, keyeventf_keyup, 0)
        self.typed.append(text)
        return True

    def play_cue(self, name: str) -> None:
        import winsound

        alias = self._CUE_ALIASES.get(name, "SystemAsterisk")
        self.cues.append(name)
        winsound.PlaySound(alias, winsound.SND_ASYNC)


class WSLBridgeClient:
    """
    Client for the WSL host-guest bridge (``src/wsl_bridge.py`` server).

    Protocol contract: a fresh TCP connection to 127.0.0.1:50555 per command,
    a single UTF-8 JSON object payload, and a single UTF-8 JSON object response.
    """

    HOST = "127.0.0.1"
    PORT = 50555
    TIMEOUT = 2.0

    def __init__(self, host: str = HOST, port: int = PORT, timeout: float = TIMEOUT):
        self.host = host
        self.port = port
        self.timeout = timeout

    def send_command(self, action: str, **kwargs) -> dict:
        payload = {"action": action, **kwargs}
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect((self.host, self.port))
            sock.sendall(json.dumps(payload).encode("utf-8"))
            raw = sock.recv(4096).decode("utf-8")
        return json.loads(raw) if raw else {}

    def start(self) -> dict:
        return self.send_command("start")

    def stop(self, copy_clipboard: bool = True) -> dict:
        return self.send_command("stop", copy_clipboard=copy_clipboard)

    def status(self) -> dict:
        return self.send_command("status")


class WSLOutputSink(OutputSink):
    """WSL sink: Windows host clipboard via clip.exe, paste via the stdio bridge."""

    platform = WSL

    def __init__(self, bridge: WSLBridgeClient | None = None):
        super().__init__()
        self.bridge = bridge or WSLBridgeClient()

    def copy_text(self, text: str) -> bool:
        # clip.exe wants UTF-16LE on stdin (matches src/wsl_bridge.py).
        clip_exe = shutil.which("clip.exe") or "/mnt/c/Windows/System32/clip.exe"
        proc = subprocess.Popen(
            [clip_exe], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        proc.communicate(input=text.encode("utf-16le"))
        self.copied.append(text)
        return True

    def type_text(self, text: str) -> bool:
        # In WSL the active Windows window receives a synthetic Ctrl+V paste
        # triggered by the host-side helper; there is no guest-side typing API.
        try:
            self.bridge.stop(copy_clipboard=True)
        except OSError:
            pass
        self.typed.append(text)
        return True

    def play_cue(self, name: str) -> None:
        # Host chime is played by the Windows helper (wsl_win_hotkeys.ps1).
        self.cues.append(name)


def get_output_sink(platform: str | None = None) -> OutputSink:
    """Factory returning the HAL output sink for a platform (or the override)."""
    platform = platform or detect_platform()
    return {
        LINUX: LinuxOutputSink,
        WINDOWS: WindowsOutputSink,
        WSL: WSLOutputSink,
    }[platform]()


# ===========================================================================
# 3. Audio helpers
# ===========================================================================
def load_test_audio(name: str) -> np.ndarray:
    """Load a benchmark mp3 as mono 16 kHz float32 (no physical hardware)."""
    path = TEST_AUDIO_DIR / f"{name}.mp3"
    audio, sample_rate = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if sample_rate != 16000:
        import librosa

        audio = librosa.resample(audio, orig_sr=sample_rate, target_sr=16000)
    return np.asarray(audio, dtype=np.float32)


def load_expected_text(name: str) -> str:
    return (TEST_AUDIO_DIR / f"{name}.md").read_text().strip()


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", "", (text or "").lower()).split()


def transcription_accuracy(expected: str, actual: str) -> float:
    """
    Token accuracy against ground truth.

    Matches the repo's convention (``tests/test_transcribe.py``) by measuring how
    many ground-truth tokens the hypothesis recalls, while also considering
    order-preserving sequence similarity via ``SequenceMatcher``. Extra ASR
    tokens (e.g. a stray "team") do not unfairly penalise a correct result.
    """
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)
    if not expected_tokens:
        return 0.0
    if not actual_tokens:
        return 0.0
    recall = len(set(expected_tokens) & set(actual_tokens)) / len(set(expected_tokens))
    sequence = SequenceMatcher(None, expected_tokens, actual_tokens).ratio()
    return max(recall, sequence)


# ===========================================================================
# 4. Synthetic hotkey + pipeline harness
# ===========================================================================
class SyntheticHotkeyHarness:
    """
    Drives the *real* Voice Transcriber pipeline (StreamingMicroBatcher energy
    gate + VAD trim -> ASR backend -> post-processor) from synthetic hotkey
    events, with no microphone or display attached.

    The hotkey state machine mirrors ``src/hotkeys.py::handle_key_event`` exactly:

      Push-to-Talk : Alt+Shift down -> record -> Alt+Shift up -> transcribe -> copy
      Hands-Free   : Alt+Shift down -> Space tap (latch) -> Alt+Shift up keeps
                     recording -> Alt+Shift tap -> transcribe -> copy
    """

    SAMPLE_RATE = 16000

    def __init__(self, sink: OutputSink, copy_on_stop: bool = True):
        self.sink = sink
        self.copy_on_stop = copy_on_stop

        # Physical-key state (mirrors WaylandGlobalHotkeys).
        self.hotkey_active = False
        self.latch_release = False

        # Recording state.
        self.recording = False
        self.batcher = None
        self.release_time: float | None = None
        self.post_release_latency: float | None = None
        self.last_transcription = ""

    # -- hotkey events -----------------------------------------------------
    def hotkey_down(self) -> None:
        if self.hotkey_active:
            return
        self.hotkey_active = True
        self.start_recording()

    def hotkey_up(self, copy_to_clipboard: bool | None = None) -> None:
        if not self.hotkey_active:
            return
        self.hotkey_active = False
        if self.latch_release:
            # Space was tapped during the hold: keep recording hands-free.
            self.latch_release = False
            return
        self.stop_recording(
            copy_to_clipboard=self.copy_on_stop if copy_to_clipboard is None else copy_to_clipboard
        )

    def space_tap(self) -> None:
        if self.hotkey_active:
            self.latch_release = True

    # -- recording / pipeline ---------------------------------------------
    def start_recording(self) -> None:
        if self.recording:
            return
        from micro_batcher import StreamingMicroBatcher

        self.recording = True
        self.last_transcription = ""
        self.batcher = StreamingMicroBatcher(sample_rate=self.SAMPLE_RATE)
        self.batcher.start()

    def feed_audio(self, pcm: np.ndarray) -> None:
        if self.recording and self.batcher is not None:
            self.batcher.feed_audio(pcm)

    def stream_audio(self, pcm: np.ndarray, block_ms: int = 100) -> None:
        """Feed audio in realistic 100 ms blocks (VAD/micro-batching stay active)."""
        block = max(1, int(self.SAMPLE_RATE * block_ms / 1000))
        for start in range(0, len(pcm), block):
            self.feed_audio(pcm[start : start + block])

    def stop_recording(self, copy_to_clipboard: bool = True) -> str:
        """Release -> transcribe -> copy, tracking post-release latency."""
        if not self.recording:
            return ""
        self.release_time = time.perf_counter()
        self.recording = False

        text = self.batcher.finish_and_get_text(skip_slm=True).strip()
        self.last_transcription = text

        if text and copy_to_clipboard:
            self.sink.copy_text(text)

        self.post_release_latency = time.perf_counter() - self.release_time
        return text


# ===========================================================================
# 5. Test doubles
# ===========================================================================
class _FakeCompletedProcess:
    def __init__(self, args, returncode=0, stdout=b"", stderr=b""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingRun:
    """Stand-in for ``subprocess.run`` that records every invocation."""

    def __init__(self, returncode=0):
        self.returncode = returncode
        self.calls: list[tuple] = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeCompletedProcess(args, self.returncode)


class _RecordingPopen:
    """Stand-in for ``subprocess.Popen`` that records args and stdin payloads."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.stdin_payloads: list[bytes] = []
        self._instances: list[_RecordingPopen] = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        self._instances.append(self)
        return self

    def communicate(self, input=None):
        self.stdin_payloads.append(input)
        return (b"", b"")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeUser32:
    def __init__(self):
        self.keybd_event_calls: list[tuple] = []

    def VkKeyScanW(self, char_ord):  # noqa: N802 - Win32 name
        return char_ord & 0xFF

    def keybd_event(self, vk, scan, flags, extra):  # noqa: N802 - Win32 name
        self.keybd_event_calls.append((vk, scan, flags, extra))


class _FakeWindll:
    def __init__(self, user32):
        self.user32 = user32


class _FakeWSLDaemon(threading.Thread):
    """Minimal echo daemon implementing the WSL bridge JSON-RPC contract."""

    def __init__(self):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.host, self.port = self.sock.getsockname()
        self.received: list[dict] = []
        self._stop = threading.Event()
        self.responses = {
            "start": {"status": "recording_started"},
            "stop": {"status": "transcription_in_progress"},
            "status": {"status": "idle"},
        }

    def run(self):  # pragma: no cover - threads are timing dependent
        self.sock.settimeout(0.2)
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                data = conn.recv(1024).decode("utf-8").strip()
                if not data:
                    continue
                command = json.loads(data)
                self.received.append(command)
                response = self.responses.get(command.get("action"), {"error": "unknown"})
                conn.sendall(json.dumps(response).encode("utf-8"))

    def stop(self):
        self._stop.set()
        try:
            self.sock.close()
        except OSError:
            pass
        self.join(timeout=2)


# ===========================================================================
# 6. Fixtures
# ===========================================================================
@pytest.fixture(autouse=True)
def _reset_platform_override(monkeypatch):
    """Default to the Linux sink unless the caller set an explicit override."""
    if not os.environ.get("VT_PLATFORM"):
        monkeypatch.setenv("VT_PLATFORM", LINUX)


@pytest.fixture(scope="session")
def warm_asr():
    """Load + warm the ASR backend once so latency measurements are meaningful."""
    import transcribe2

    backend = "cohere"
    transcribe2.set_backend(backend)
    warm_audio = load_test_audio("short_word")
    probe = transcribe2.transcribe_audio(audio_data=warm_audio)
    if isinstance(probe, str) and probe.startswith("Error loading model"):
        pytest.skip(f"ASR backend ({backend}) unavailable: {probe}")
    return backend


# ===========================================================================
# 7. Platform detection + adapter factory
# ===========================================================================
class TestPlatformDetectionAndAdapterFactory:
    """``VT_PLATFORM`` override and the platform adapter factory contract."""

    @pytest.mark.parametrize("value", [LINUX, WSL, WINDOWS])
    def test_adapter_detect_platform_env_override(self, monkeypatch, value):
        monkeypatch.setenv("VT_PLATFORM", value)
        assert detect_platform() == value

    def test_adapter_detect_platform_rejects_unknown_value(self, monkeypatch):
        monkeypatch.setenv("VT_PLATFORM", "beos")
        with pytest.raises(ValueError):
            detect_platform()

    def test_adapter_detect_platform_autodetects_without_override(self, monkeypatch):
        monkeypatch.delenv("VT_PLATFORM", raising=False)
        assert detect_platform() in VALID_PLATFORMS

    @pytest.mark.parametrize(
        "value,expected_cls",
        [(LINUX, LinuxOutputSink), (WINDOWS, WindowsOutputSink), (WSL, WSLOutputSink)],
    )
    def test_adapter_factory_returns_platform_sink(self, value, expected_cls):
        assert isinstance(get_output_sink(value), expected_cls)


# ===========================================================================
# 8. Linux adapter
# ===========================================================================
class TestLinuxAdapterClipboardAndTyping:
    """Linux clipboard (wl-copy/xclip) and typing (ydotool/xdotool) hooks."""

    def test_adapter_linux_wl_copy_used_when_available(self, monkeypatch):
        run = _RecordingRun()
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None)
        monkeypatch.setattr(subprocess, "run", run)

        sink = LinuxOutputSink()
        assert sink.copy_text("hello wayland") is True

        assert run.calls[0][0] == ["wl-copy"]
        assert run.calls[0][1]["input"] == b"hello wayland"

    def test_adapter_linux_falls_back_to_xclip(self, monkeypatch):
        run = _RecordingRun()
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == "xclip" else None)
        monkeypatch.setattr(subprocess, "run", run)

        sink = LinuxOutputSink()
        assert sink.copy_text("hello x11") is True

        assert run.calls[0][0] == ["xclip", "-selection", "clipboard"]
        assert run.calls[0][1]["input"] == b"hello x11"

    def test_adapter_linux_ydotool_typing(self, monkeypatch):
        run = _RecordingRun()
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == "ydotool" else None)
        monkeypatch.setattr(subprocess, "run", run)

        sink = LinuxOutputSink()
        assert sink.type_text("typed text") is True
        assert run.calls[0][0] == ["ydotool", "type", "--", "typed text"]

    def test_adapter_linux_xdotool_typing_fallback(self, monkeypatch):
        run = _RecordingRun()
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}" if name == "xdotool" else None)
        monkeypatch.setattr(subprocess, "run", run)

        sink = LinuxOutputSink()
        assert sink.type_text("typed text") is True
        assert run.calls[0][0] == ["xdotool", "type", "--clearmodifiers", "--", "typed text"]

    def test_adapter_linux_no_clipboard_tool_returns_false(self, monkeypatch):
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert LinuxOutputSink().copy_text("nowhere") is False


# ===========================================================================
# 9. Windows adapter
# ===========================================================================
class TestWindowsAdapterClipboardTypingAndCues:
    """Native Windows clipboard (pyperclip), typing (keybd_event) and winsound."""

    def test_adapter_windows_pyperclip_copy(self, monkeypatch):
        fake_module = types.ModuleType("pyperclip")
        calls: list[str] = []
        fake_module.copy = lambda text: calls.append(text)
        monkeypatch.setitem(sys.modules, "pyperclip", fake_module)

        sink = WindowsOutputSink()
        assert sink.copy_text("windows clipboard") is True
        assert calls == ["windows clipboard"]

    def test_adapter_windows_keybd_event_typing(self, monkeypatch):
        user32 = _FakeUser32()
        monkeypatch.setattr(ctypes, "windll", _FakeWindll(user32), raising=False)

        sink = WindowsOutputSink()
        assert sink.type_text("ab") is True

        # Each char: key down (flags=0) then key up (flags=KEYEVENTF_KEYUP=2).
        assert user32.keybd_event_calls == [
            (0x61, 0, 0, 0),
            (0x61, 0, 0x0002, 0),
            (0x62, 0, 0, 0),
            (0x62, 0, 0x0002, 0),
        ]

    def test_adapter_windows_winsound_cue(self, monkeypatch):
        fake_winsound = types.ModuleType("winsound")
        fake_winsound.SND_ASYNC = 0x0001
        calls: list[tuple] = []
        fake_winsound.PlaySound = lambda name, flags: calls.append((name, flags))
        monkeypatch.setitem(sys.modules, "winsound", fake_winsound)

        sink = WindowsOutputSink()
        sink.play_cue("complete")
        assert calls == [("SystemExit", fake_winsound.SND_ASYNC)]


# ===========================================================================
# 10. WSL adapter + bridge protocol
# ===========================================================================
class TestWSLAdapterClipboardAndBridge:
    """WSL host clipboard via clip.exe and the socket JSON-RPC bridge."""

    def test_adapter_wsl_clip_exe_utf16le(self, monkeypatch):
        popen = _RecordingPopen()
        monkeypatch.setattr(shutil, "which", lambda name: "/mnt/c/Windows/System32/clip.exe" if name == "clip.exe" else None)
        monkeypatch.setattr(subprocess, "Popen", popen)

        sink = WSLOutputSink()
        assert sink.copy_text("wsl clipboard") is True
        assert popen.calls[0][0] == ["/mnt/c/Windows/System32/clip.exe"]
        assert popen.stdin_payloads == ["wsl clipboard".encode("utf-16le")]

    def test_adapter_wsl_bridge_default_endpoint(self):
        client = WSLBridgeClient()
        assert (client.HOST, client.PORT) == ("127.0.0.1", 50555)

    def test_adapter_wsl_socket_json_rpc_protocol(self):
        daemon = _FakeWSLDaemon()
        daemon.start()
        try:
            client = WSLBridgeClient(host=daemon.host, port=daemon.port)
            assert client.status() == {"status": "idle"}
            assert client.start() == {"status": "recording_started"}
            assert client.stop(copy_clipboard=True) == {"status": "transcription_in_progress"}
        finally:
            daemon.stop()

        assert daemon.received == [
            {"action": "status"},
            {"action": "start"},
            {"action": "stop", "copy_clipboard": True},
        ]

    def test_adapter_wsl_socket_json_rpc_stop_without_copy(self):
        daemon = _FakeWSLDaemon()
        daemon.start()
        try:
            WSLBridgeClient(host=daemon.host, port=daemon.port).stop(copy_clipboard=False)
        finally:
            daemon.stop()
        assert daemon.received == [{"action": "stop", "copy_clipboard": False}]


# ===========================================================================
# 11. Synthetic hotkey triggering end-to-end
# ===========================================================================
class TestSyntheticHotkeyTriggering:
    """Push-to-Talk and hands-free latch drive the real pipeline end-to-end."""

    def test_push_to_talk_records_transcribes_and_copies(self, warm_asr):
        audio = load_test_audio("short_phrase")
        expected = load_expected_text("short_phrase")
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        # Alt+Shift down -> record -> Alt+Shift up -> transcribe -> copy
        harness.hotkey_down()
        assert harness.recording is True
        harness.stream_audio(audio)
        harness.hotkey_up()

        assert harness.recording is False
        actual = harness.last_transcription
        assert actual, "Push-to-Talk produced an empty transcription"
        assert sink.copied == [actual], "Clipboard sink did not receive the transcription"
        assert harness.post_release_latency is not None
        assert harness.post_release_latency <= POST_RELEASE_LATENCY_BUDGET_SEC
        assert transcription_accuracy(expected, actual) >= ACCURACY_FLOOR

    def test_hands_free_latch_records_after_modifier_release(self, warm_asr):
        audio = load_test_audio("short_phrase")
        expected = load_expected_text("short_phrase")
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        # Alt+Shift down -> Space tap (latch) -> Alt+Shift up keeps recording
        harness.hotkey_down()
        harness.space_tap()
        harness.hotkey_up()
        assert harness.recording is True, "Space latch did not hold the recording open"
        assert sink.copied == [], "Latch must not stop/copy while hands-free"

        # Audio continues to stream while the user is hands-free.
        harness.stream_audio(audio)

        # Alt+Shift tap (down then up) -> transcribe -> copy
        harness.hotkey_down()
        harness.hotkey_up()

        assert harness.recording is False
        actual = harness.last_transcription
        assert actual, "Hands-free latch produced an empty transcription"
        assert sink.copied == [actual]
        assert harness.post_release_latency is not None
        assert harness.post_release_latency <= POST_RELEASE_LATENCY_BUDGET_SEC
        assert transcription_accuracy(expected, actual) >= ACCURACY_FLOOR

    def test_release_without_space_does_not_latch(self, warm_asr):
        harness = SyntheticHotkeyHarness(get_output_sink(), copy_on_stop=False)
        harness.hotkey_down()
        harness.hotkey_up()
        assert harness.recording is False
        assert harness.latch_release is False


# ===========================================================================
# 12. Synthetic end-to-end audio pipeline (no mic / no display)
# ===========================================================================
class TestSyntheticAudioPipeline:
    """Full VAD + micro-batch + ASR + post-process pipeline on benchmark audio."""

    @pytest.mark.parametrize("sample", ["short_word", "short_phrase"])
    def test_synthetic_audio_pipeline_accuracy_and_latency(self, warm_asr, sample):
        audio = load_test_audio(sample)
        expected = load_expected_text(sample)
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        harness.start_recording()
        harness.stream_audio(audio, block_ms=100)  # 16 kHz mono, 100 ms frames
        actual = harness.stop_recording(copy_to_clipboard=True)

        assert actual, f"[{sample}] pipeline returned no transcription"
        assert harness.post_release_latency is not None, "latency timer was not tracked"
        assert harness.post_release_latency <= POST_RELEASE_LATENCY_BUDGET_SEC, (
            f"[{sample}] post-release latency {harness.post_release_latency:.3f}s "
            f"exceeded {POST_RELEASE_LATENCY_BUDGET_SEC}s"
        )
        accuracy = transcription_accuracy(expected, actual)
        assert accuracy >= ACCURACY_FLOOR, (
            f"[{sample}] accuracy {accuracy:.2f} below floor {ACCURACY_FLOOR}: "
            f"expected={expected!r} actual={actual!r}"
        )
        assert sink.copied == [actual]


# ===========================================================================
# 13. Silence / ambient-noise hallucination rejection
# ===========================================================================
class TestSilenceHallucinationRejection:
    """2 s of silence or ambient noise must produce ``""`` with zero tokens."""

    @pytest.mark.parametrize(
        "name,audio",
        [
            ("pure_zeros", np.zeros(2 * 16000, dtype=np.float32)),
            (
                "ambient_noise",
                np.random.default_rng(1234)
                .uniform(-0.002, 0.002, 2 * 16000)
                .astype(np.float32),
            ),
        ],
    )
    def test_silence_is_rejected_with_zero_tokens(self, warm_asr, name, audio):
        sink = get_output_sink()
        harness = SyntheticHotkeyHarness(sink)

        harness.start_recording()
        harness.stream_audio(audio, block_ms=100)
        actual = harness.stop_recording(copy_to_clipboard=True)

        assert actual == "", f"[{name}] hallucinated {actual!r} from silence/noise"
        assert _tokens(actual) == [], f"[{name}] produced hallucinated tokens"
        assert sink.copied == [], f"[{name}] copied hallucinated text to clipboard"


# ===========================================================================
# 14. Post-processor terminal punctuation (TODO.md regression guard)
# ===========================================================================
class TestPostProcessorTerminalPunctuation:
    """
    The post-processor now terminates complete multi-word statements with a
    period. This locks the behaviour that TODO.md flagged as a stale test
    expectation (``test_post_processor_artifacts``).
    """

    def test_multiword_sentence_gets_terminal_period(self):
        from post_processor import clean_speech_transcription

        assert clean_speech_transcription("This is important oops") == "This is important."
        assert (
            clean_speech_transcription("So. I think we should proceed")
            == "So, I think we should proceed."
        )

    def test_short_two_word_fragment_has_no_trailing_period(self):
        from post_processor import clean_speech_transcription

        assert clean_speech_transcription("Hello world whoops") == "Hello world"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
