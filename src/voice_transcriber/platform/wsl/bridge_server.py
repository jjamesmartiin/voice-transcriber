#!/usr/bin/env python3
"""WSL host-guest transcription bridge server.

Runs inside NixOS WSL and listens on ``127.0.0.1:50555`` for newline-free,
single-JSON-object commands from the Windows host helper:

    {"action": "start"}
    {"action": "stop", "copy_clipboard": true}
    {"action": "status"}

Ported from ``main-wsl:src/wsl_bridge.py``. The import bootstrap was updated so
the module works from its new home under ``src/platform/wsl/``.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import threading

# Ensure the ``src`` directory (two levels up) is importable so ``t2`` resolves.
_SRC_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import t2  # noqa: E402
from t2 import (  # noqa: E402
    preload_model,
    DEVICE,
    record_audio_stream,
    process_audio_stream,
    stop_recording,
    load_audio_config,
    get_active_device_name,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [WSL-Bridge] %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 50555

_POWERSHELL = "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
_CLIP_EXE = "/mnt/c/Windows/System32/clip.exe"


def copy_to_windows_clipboard(text: str) -> bool:
    """Copy text to the Windows clipboard from inside WSL."""
    try:
        if os.path.exists(_CLIP_EXE):
            p = subprocess.Popen([_CLIP_EXE], stdin=subprocess.PIPE, close_fds=True)
            p.communicate(input=text.encode("utf-16le"))
            logger.info("Copied transcription to Windows clipboard via clip.exe")
            return True
        subprocess.run(
            [
                _POWERSHELL, "-NoProfile", "-Command",
                f"Set-Clipboard -Value @'\n{text}\n'@",
            ],
            check=True,
        )
        logger.info("Copied transcription to Windows clipboard via PowerShell")
        return True
    except Exception as e:
        logger.warning(f"Failed to copy to Windows clipboard: {e}")
        return False


class WSLTranscriptionDaemon:
    """Stateful transcription daemon driven by bridge commands."""

    def __init__(self):
        self.recording = False
        self.record_thread = None
        self.process_thread = None
        load_audio_config()

        logger.info(f"Active audio input device: {get_active_device_name()}")
        logger.info(f"PULSE_SERVER: {os.environ.get('PULSE_SERVER', 'default')}")
        logger.info("Preloading transcription model in background...")
        preload_model(device=DEVICE)

    def start_recording(self):
        if self.recording:
            return {"status": "already_recording"}

        self.recording = True
        stop_recording.clear()

        self.record_thread = threading.Thread(
            target=record_audio_stream, daemon=True
        )
        self.record_thread.start()
        logger.info("🎤 Recording started...")
        return {"status": "recording_started"}

    def stop_recording(self, copy_clipboard: bool = True):
        if not self.recording:
            return {"status": "not_recording"}

        self.recording = False
        stop_recording.set()
        logger.info("🛑 Recording stopped. Transcribing...")

        def _transcribe():
            result = process_audio_stream()
            if result:
                logger.info(f"📝 Transcription: {result}")
                if copy_clipboard:
                    copy_to_windows_clipboard(result)
            return result

        self.process_thread = threading.Thread(target=_transcribe, daemon=True)
        self.process_thread.start()
        return {"status": "transcription_in_progress"}

    def status(self) -> dict:
        return {"status": "recording" if self.recording else "idle"}

    def handle_command(self, cmd: dict) -> dict:
        action = cmd.get("action")
        if action == "start":
            return self.start_recording()
        if action == "stop":
            return self.stop_recording(
                copy_clipboard=cmd.get("copy_clipboard", True)
            )
        if action == "status":
            return self.status()
        return {"error": f"Unknown action: {action}"}


def run_server(host: str = BRIDGE_HOST, port: int = BRIDGE_PORT) -> None:
    daemon = WSLTranscriptionDaemon()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)

    logger.info(f"🚀 WSL Transcription Bridge listening on {host}:{port}")

    while True:
        try:
            conn, addr = server.accept()
            data = conn.recv(1024).decode("utf-8").strip()
            if not data:
                conn.close()
                continue

            try:
                cmd = json.loads(data)
                resp = daemon.handle_command(cmd)
            except Exception as e:
                resp = {"error": str(e)}

            conn.sendall(json.dumps(resp).encode("utf-8"))
            conn.close()
        except KeyboardInterrupt:
            break
        except Exception as e:
            logger.error(f"Error handling connection: {e}")


if __name__ == "__main__":
    run_server()
