#!/usr/bin/env python3
"""Warm input-stream cache behavior (Windows/WASAPI capture-start fix).

These run on every platform: they monkeypatch ``sys.platform`` and a fake
``sounddevice`` so no real audio device is needed.
"""
import sys
import types
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2  # noqa: E402


class _FakeStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = 0
        self.stopped = 0
        self.closed = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1

    def close(self):
        self.closed += 1


@pytest.fixture
def fake_sd(monkeypatch):
    created = []

    def _make(**kwargs):
        stream = _FakeStream(**kwargs)
        created.append(stream)
        return stream

    monkeypatch.setattr(t2, "sd", types.SimpleNamespace(InputStream=_make))
    yield created
    t2._close_cached_input_streams()


class TestWarmMicGating:
    def test_disabled_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(t2.sys, "platform", "linux")
        monkeypatch.delenv("VT_WARM_MIC", raising=False)
        assert t2._warm_mic_enabled() is False

    def test_enabled_by_default_on_windows(self, monkeypatch):
        monkeypatch.setattr(t2.sys, "platform", "win32")
        monkeypatch.delenv("VT_WARM_MIC", raising=False)
        assert t2._warm_mic_enabled() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off"])
    def test_env_opt_out(self, monkeypatch, value):
        monkeypatch.setattr(t2.sys, "platform", "win32")
        monkeypatch.setenv("VT_WARM_MIC", value)
        assert t2._warm_mic_enabled() is False


class TestStreamCache:
    def test_reuses_stream_for_same_device(self, fake_sd, monkeypatch):
        monkeypatch.setattr(t2.sys, "platform", "win32")
        t2._close_cached_input_streams()
        s1 = t2._get_cached_input_stream(3, 16000)
        s2 = t2._get_cached_input_stream(3, 16000)
        assert s1 is s2
        assert len(fake_sd) == 1  # constructed once

    def test_new_device_replaces_and_closes_old(self, fake_sd, monkeypatch):
        monkeypatch.setattr(t2.sys, "platform", "win32")
        t2._close_cached_input_streams()
        s1 = t2._get_cached_input_stream(3, 16000)
        s2 = t2._get_cached_input_stream(4, 16000)
        assert s2 is not s1
        assert len(fake_sd) == 2
        assert s1.closed == 1  # stale stream cleaned up

    def test_prewarm_is_noop_off_windows(self, fake_sd, monkeypatch):
        monkeypatch.setattr(t2.sys, "platform", "linux")
        t2._close_cached_input_streams()
        t2.prewarm_input_stream()
        assert fake_sd == []  # nothing constructed
