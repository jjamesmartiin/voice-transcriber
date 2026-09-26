#!/usr/bin/env python3
"""
Cross-platform runtime tests for the Windows CPU-speed fixes.

Covers three things that are easy to regress and must behave identically across
Linux / Windows / WSL:

1. ``transcribe_cohere._cpu_supports_bf16()`` — must keep using /proc/cpuinfo on
   Linux/WSL, and fall back to NumPy's CPUID feature table on Windows (where
   /proc does not exist). Before the fix Windows always loaded float32.
2. ``transcribe_cohere._configure_torch_runtime()`` — CPU thread count must use
   physical cores on Windows and keep the historical ``min(cpu_cnt, 8)`` on
   Linux/WSL.
3. ``SimpleVoiceTranscriber.process_recording()`` — the empty-audio guard must
   accept both a Python list (what ``record_audio_stream`` returns on the
   Windows/native fast path) and a NumPy array, without calling ``.size`` on a
   list.

The transcribe_cohere tests require torch/transformers and are skipped where it
is not installed (the lightweight Windows CI runner). The process_recording
tests only need ``main`` and run on every platform.
"""
import builtins
import io
import sys
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


def _import_tc():
    """Import transcribe_cohere, skipping the test if torch isn't available."""
    return pytest.importorskip(
        "transcribe_cohere", reason="requires torch/transformers"
    )


class _FakeUm:
    def __init__(self, feats):
        self.__cpu_features__ = feats


class _FakeCore:
    def __init__(self, feats):
        self._multiarray_umath = _FakeUm(feats)


class _FakeNumpy:
    """Minimal stand-in exposing NumPy's nested __cpu_features__ table."""

    def __init__(self, feats):
        self._core = _FakeCore(feats)


# ===========================================================================
# 1. bf16 CPU detection (the exact behavior that made Windows 2x slower)
# ===========================================================================
class TestCpuBf16Detection:
    def _proc_readable_as(self, monkeypatch, content):
        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if path == "/proc/cpuinfo":
                return io.StringIO(content)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)

    def _proc_unreadable(self, monkeypatch):
        real_open = builtins.open

        def fake_open(path, *args, **kwargs):
            if path == "/proc/cpuinfo":
                raise FileNotFoundError(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", fake_open)

    def test_proc_cpuinfo_avx512_bf16(self, monkeypatch):
        tc = _import_tc()
        self._proc_readable_as(monkeypatch, "flags\t\t: fpu sse avx512_bf16\n")
        assert tc._cpu_supports_bf16() is True

    def test_proc_cpuinfo_amx_bf16(self, monkeypatch):
        tc = _import_tc()
        self._proc_readable_as(monkeypatch, "flags\t\t: fpu sse amx_bf16\n")
        assert tc._cpu_supports_bf16() is True

    def test_proc_cpuinfo_without_bf16(self, monkeypatch):
        tc = _import_tc()
        self._proc_readable_as(monkeypatch, "flags\t\t: fpu sse avx2 avx512f\n")
        assert tc._cpu_supports_bf16() is False

    def test_windows_numpy_fallback_reports_bf16(self, monkeypatch):
        """No /proc (Windows) -> NumPy CPUID table enables bf16."""
        tc = _import_tc()
        self._proc_unreadable(monkeypatch)
        monkeypatch.setattr(tc, "np", _FakeNumpy({"AVX512BF16": True}), raising=True)
        assert tc._cpu_supports_bf16() is True

    def test_windows_numpy_fallback_amx_bf16(self, monkeypatch):
        tc = _import_tc()
        self._proc_unreadable(monkeypatch)
        monkeypatch.setattr(tc, "np", _FakeNumpy({"AMXBF16": True}), raising=True)
        assert tc._cpu_supports_bf16() is True

    def test_windows_numpy_fallback_without_bf16(self, monkeypatch):
        tc = _import_tc()
        self._proc_unreadable(monkeypatch)
        monkeypatch.setattr(
            tc, "np", _FakeNumpy({"AVX512BF16": False, "AMXBF16": False}), raising=True
        )
        assert tc._cpu_supports_bf16() is False

    def test_no_detection_source_returns_false(self, monkeypatch):
        """If neither /proc nor a NumPy features table is available -> False."""
        tc = _import_tc()
        self._proc_unreadable(monkeypatch)
        monkeypatch.setattr(tc, "np", object(), raising=True)
        assert tc._cpu_supports_bf16() is False


# ===========================================================================
# 2. CPU thread selection (physical cores on Windows, 8-cap elsewhere)
# ===========================================================================
class TestCpuThreadSelection:
    def _capture(self, monkeypatch):
        tc = _import_tc()
        calls = []
        # Force the setter branch to run and record the chosen thread count.
        monkeypatch.setattr(tc, "_cached_cpu_threads", None, raising=False)
        monkeypatch.setattr(tc.torch, "set_num_threads", lambda n: calls.append(int(n)))
        return tc, calls

    def test_linux_caps_at_8_threads(self, monkeypatch):
        tc, calls = self._capture(monkeypatch)
        monkeypatch.delenv("VT_CPU_THREADS", raising=False)
        monkeypatch.setattr(tc.sys, "platform", "linux")
        monkeypatch.setattr(tc.os, "cpu_count", lambda: 32)
        tc._configure_torch_runtime("cpu")
        assert calls == [8]

    def test_windows_uses_physical_cores(self, monkeypatch):
        tc, calls = self._capture(monkeypatch)
        monkeypatch.delenv("VT_CPU_THREADS", raising=False)
        monkeypatch.setattr(tc.sys, "platform", "win32")
        monkeypatch.setattr(tc.os, "cpu_count", lambda: 24)
        fake_psutil = types.SimpleNamespace(cpu_count=lambda logical=True: 12)
        monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
        tc._configure_torch_runtime("cpu")
        assert calls == [12]

    def test_windows_falls_back_when_psutil_unavailable(self, monkeypatch):
        tc, calls = self._capture(monkeypatch)
        monkeypatch.delenv("VT_CPU_THREADS", raising=False)
        monkeypatch.setattr(tc.sys, "platform", "win32")
        monkeypatch.setattr(tc.os, "cpu_count", lambda: 24)
        # A None entry in sys.modules makes ``import psutil`` raise ImportError.
        monkeypatch.setitem(sys.modules, "psutil", None)
        tc._configure_torch_runtime("cpu")
        assert calls == [12]  # cpu_cnt // 2 fallback

    def test_env_override_wins_on_all_platforms(self, monkeypatch):
        tc, calls = self._capture(monkeypatch)
        monkeypatch.setenv("VT_CPU_THREADS", "5")
        monkeypatch.setattr(tc.sys, "platform", "linux")
        monkeypatch.setattr(tc.os, "cpu_count", lambda: 24)
        tc._configure_torch_runtime("cpu")
        assert calls == [5]


# ===========================================================================
# 3. process_recording empty-audio check: list vs NumPy (the .size crash)
# ===========================================================================
class TestAudioFramesEmptyCheck:
    def _make_app(self, monkeypatch, audio_frames, output_mode="clipboard"):
        from main import SimpleVoiceTranscriber
        import t2 as t2mod

        monkeypatch.setattr(t2mod, "ENABLE_SLM", False)
        monkeypatch.setattr(t2mod, "OUTPUT_MODE", output_mode)
        monkeypatch.setattr(t2mod, "AUTO_TYPE_TRAILING_SPACE", True)
        monkeypatch.setattr(t2mod, "AUTO_TYPE_AUTO_PUNCTUATE", True)
        monkeypatch.setattr(t2mod, "IS_MUTED", True)

        app = SimpleVoiceTranscriber.__new__(SimpleVoiceTranscriber)
        app.recording = False
        app.copy_to_clipboard = False
        app.start_time = 0.0
        app.release_time = 1.0
        app.last_finish_time = 0.0
        app.audio_frames = audio_frames
        app.last_transcription = ""
        app.model_load_error = None
        app._model_ready_event = MagicMock()
        app._model_ready_event.is_set.return_value = True
        app.tui = MagicMock()
        app.visual_notification = MagicMock()
        app.audio_cues = MagicMock()
        app.hotkey_system = MagicMock()
        app.hotkey_system.are_modifiers_pressed.return_value = False
        app.hotkey_system.type_text.return_value = True
        app.clipboard_sink = MagicMock()

        copy_done = threading.Event()

        def _fake_copy(text, sink=None):
            copy_done.set()
            return True

        monkeypatch.setattr("main.copy_to_clipboard_crossplatform", _fake_copy)
        app._copy_done = copy_done
        return app

    def test_list_audio_frames_without_batcher_does_not_raise(self, monkeypatch):
        """Regression: a list has no .size; the old guard raised AttributeError."""
        frames = [np.zeros(1024, dtype=np.float32)] * 3
        app = self._make_app(monkeypatch, frames)
        seen = []

        def _fake_process(f):
            seen.append(f)
            return "list path ok", 0.01

        monkeypatch.setattr("main.process_audio_stream", _fake_process)

        app.process_recording()  # must not raise
        assert len(seen) == 1
        assert isinstance(seen[0], list)

    def test_list_audio_frames_with_batcher_does_not_raise(self, monkeypatch):
        """Regression: the batcher check must short-circuit before touching .size."""
        frames = [np.zeros(1024, dtype=np.float32)] * 3
        app = self._make_app(monkeypatch, frames)
        app.micro_batcher = MagicMock()
        app.micro_batcher.finish_and_get_text.return_value = "batcher path ok"
        fallback_calls = []
        monkeypatch.setattr(
            "main.process_audio_stream", lambda f: (fallback_calls.append(f) or ("", 0.0))
        )

        app.process_recording()  # must not raise

        app.micro_batcher.finish_and_get_text.assert_called_once()
        assert fallback_calls == []

    def test_numpy_audio_frames_still_work(self, monkeypatch):
        app = self._make_app(
            monkeypatch, np.ones(16000, dtype=np.float32) * 0.05
        )
        seen = []
        monkeypatch.setattr(
            "main.process_audio_stream",
            lambda f: (seen.append(f) or ("numpy path ok", 0.01)),
        )

        app.process_recording()
        assert len(seen) == 1
        assert isinstance(seen[0], np.ndarray)

    def test_empty_list_without_batcher_returns_early(self, monkeypatch):
        app = self._make_app(monkeypatch, [])
        seen = []
        monkeypatch.setattr(
            "main.process_audio_stream", lambda f: (seen.append(f) or ("", 0.0))
        )

        app.process_recording()
        assert seen == []  # no transcription attempted

    def test_none_audio_frames_without_batcher_returns_early(self, monkeypatch):
        app = self._make_app(monkeypatch, None)
        seen = []
        monkeypatch.setattr(
            "main.process_audio_stream", lambda f: (seen.append(f) or ("", 0.0))
        )

        app.process_recording()
        assert seen == []
