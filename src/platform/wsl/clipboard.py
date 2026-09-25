"""WSL clipboard backend: Windows host clipboard via ``clip.exe``.

Contract (locked by ``tests/test_end_to_end_crossplatform.py``):

* ``copy_text`` feeds UTF-16LE bytes to ``clip.exe`` (found on ``PATH`` or at
  the canonical ``/mnt/c/...`` location).
* ``type_text`` triggers the host-side synthetic Ctrl+V via the bridge (there is
  no guest-side typing API in WSL).

A PowerShell ``Set-Clipboard`` fallback is used when ``clip.exe`` is missing or
fails.
"""
from __future__ import annotations

import logging
import shutil
import subprocess

from ..base import BaseClipboardSink

logger = logging.getLogger(__name__)

_CLIP_FALLBACK = "/mnt/c/Windows/System32/clip.exe"
_POWERSHELL = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
)


class WSLClipboardSink(BaseClipboardSink):
    """Copy to the Windows host clipboard from inside WSL."""

    platform = "wsl"

    def __init__(self, bridge=None):
        self.bridge = bridge

    def set_bridge(self, bridge) -> None:
        self.bridge = bridge

    def copy_text(self, text: str) -> bool:
        clip_exe = shutil.which("clip.exe") or _CLIP_FALLBACK
        try:
            proc = subprocess.Popen(
                [clip_exe], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            proc.communicate(input=text.encode("utf-16le"))
            return True
        except Exception as e:
            logger.warning(f"clip.exe failed, trying PowerShell: {e}")
        return self._copy_via_powershell(text)

    def _copy_via_powershell(self, text: str) -> bool:
        try:
            subprocess.run(
                [
                    _POWERSHELL, "-NoProfile", "-Command",
                    f"Set-Clipboard -Value @'\n{text}\n'@",
                ],
                check=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to copy to Windows clipboard: {e}")
            return False

    def type_text(self, text: str, fast: bool = False) -> bool:
        """Trigger a synthetic Ctrl+V paste in the active Windows window."""
        if self.bridge is not None:
            try:
                if hasattr(self.bridge, "paste_text"):
                    return bool(self.bridge.paste_text())
                elif hasattr(self.bridge, "stop"):
                    self.bridge.stop(copy_clipboard=True)
                    return True
            except Exception as e:
                logger.warning(f"WSL bridge paste failed: {e}")
        return False
