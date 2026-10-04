#!/usr/bin/env python3
"""Mic-picker live level plumbing.

The picker meters *every visible device at once*, so switching mics is a matter
of looking at which bar is moving rather than selecting a device and hoping.
These tests pin the two halves of that contract:

  * ``RatatuiTui.start_mic_monitor`` opens one capture stream per visible row,
    diffs the set instead of restarting everything, survives devices that refuse
    to open, and publishes a per-device snapshot on the wire.
  * the snapshot shape stays backward compatible with the scalar-only ``vu``
    message that the recording VU meter still sends.

No real audio hardware is touched: ``sounddevice`` is replaced with a fake that
records stream lifecycle.
"""
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from tui_ratatui import RatatuiTui


class FakeStream:
    """Captures what the picker asked for and whether it cleaned up."""

    def __init__(self, device, samplerate, blocksize, callback):
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.callback = callback
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    """Stand-in for ``sounddevice``: no hardware, full lifecycle bookkeeping."""

    def __init__(self, native_rates=None, reject_rates=(), open_failures=()):
        #: device index -> its native/default samplerate
        self.native_rates = dict(native_rates or {})
        #: {(device, samplerate)} that the rate probe refuses
        self.reject_rates = set(reject_rates)
        #: {(device, samplerate)} that pass the probe but fail to actually open
        #: (the exclusive-access case: another app holds the device)
        self.open_failures = set(open_failures)
        #: every (device, samplerate) we handed out, in order
        self.opened = []
        #: every (device, samplerate) probed via check_input_settings
        self.checked = []
        self.streams = []

    def query_devices(self, device):
        return {"default_samplerate": self.native_rates.get(device, 16000)}

    def check_input_settings(self, device, channels, samplerate):
        """Mirrors ``Pa_IsFormatSupported``: reports without opening anything."""
        self.checked.append((device, samplerate))
        if (device, samplerate) in self.reject_rates:
            raise RuntimeError(f"device {device} refused {samplerate} Hz")

    def InputStream(self, device, channels, samplerate, blocksize, callback):
        if (device, samplerate) in self.reject_rates | self.open_failures:
            raise RuntimeError(f"device {device} refused {samplerate} Hz")
        stream = FakeStream(device, samplerate, blocksize, callback)
        self.opened.append((device, samplerate))
        self.streams.append(stream)
        return stream


def _bare_tui():
    """A RatatuiTui with the IPC parts replaced by a recording ``_send``."""
    tui = RatatuiTui.__new__(RatatuiTui)
    tui.vu_level = 0.0
    tui._mic_monitor_streams = {}
    tui._mic_levels = {}
    tui._last_vu_sent = 0.0
    tui._vu_lock = threading.Lock()
    tui._closed = False
    tui.sent = []
    tui._send = tui.sent.append
    return tui


@pytest.fixture
def fake_sd(monkeypatch):
    """Install a fake ``sounddevice`` module for the duration of a test."""

    def _install(**kwargs):
        fake = FakeSoundDevice(**kwargs)
        monkeypatch.setitem(sys.modules, "sounddevice", fake)
        return fake

    return _install


# --------------------------------------------------------------------- wire

def test_scalar_vu_message_stays_backward_compatible():
    """The recording VU meter sends no ``levels`` key, and must keep working."""
    tui = _bare_tui()

    tui.update_vu_level(0.6)

    assert tui.sent == [{"t": "vu", "level": 0.6}]
    assert "levels" not in tui.sent[0]


def test_levels_snapshot_is_sorted_by_device_index():
    """Rust maps ``i`` -> row level, so a stable order keeps diffs readable."""
    tui = _bare_tui()

    tui.update_vu_level(0.9, levels={9: 0.1, 4: 0.0, 5: 0.44}, force=True)

    assert tui.sent[-1] == {
        "t": "vu",
        "level": 0.9,
        "levels": [
            {"i": 4, "level": 0.0},
            {"i": 5, "level": 0.44},
            {"i": 9, "level": 0.1},
        ],
    }


def test_rapid_snapshots_are_throttled():
    """One callback per monitored device must not flood the socket."""
    tui = _bare_tui()
    tui.update_vu_level(0.1, levels={4: 0.1}, force=True)
    sent = len(tui.sent)

    for _ in range(50):
        tui.update_vu_level(0.2, levels={4: 0.2})

    assert len(tui.sent) == sent, "no time passed, so nothing should be sent"


def test_throttle_is_race_free_across_device_threads():
    """Each device ticks on its own PortAudio thread; only one may pass the gate."""
    tui = _bare_tui()
    barrier = threading.Barrier(8)

    def tick():
        barrier.wait()
        tui.update_vu_level(0.5, levels={4: 0.5})

    threads = [threading.Thread(target=tick) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(tui.sent) == 1


# ---------------------------------------------------------------- lifecycle

def test_monitors_every_visible_device_at_once(fake_sd):
    fake = fake_sd()
    tui = _bare_tui()

    tui.start_mic_monitor([4, 5, 8])

    assert fake.opened == [(4, 16000), (5, 16000), (8, 16000)]
    assert set(tui._mic_monitor_streams) == {4, 5, 8}
    # Opening a stream pushes a zeroed snapshot immediately, so a row shows 0%
    # rather than whatever the previous device on that row last reported.
    assert tui.sent[-1] == {
        "t": "vu",
        "level": 0.0,
        "levels": [
            {"i": 4, "level": 0.0},
            {"i": 5, "level": 0.0},
            {"i": 8, "level": 0.0},
        ],
    }


def test_scrolling_diffs_the_stream_set(fake_sd):
    """Scrolling must not tear down and rebuild every stream."""
    fake = fake_sd()
    tui = _bare_tui()

    tui.start_mic_monitor([4, 5, 6])
    before = dict(tui._mic_monitor_streams)
    tui.start_mic_monitor([5, 6, 7])

    assert fake.opened == [(4, 16000), (5, 16000), (6, 16000), (7, 16000)]
    assert set(tui._mic_monitor_streams) == {5, 6, 7}
    # Devices that stayed visible keep their existing stream...
    assert tui._mic_monitor_streams[5] is before[5]
    assert tui._mic_monitor_streams[6] is before[6]
    # ...and the one that scrolled away is released.
    assert before[4].stopped and before[4].closed


def test_single_index_and_string_indices_are_accepted(fake_sd):
    """Older callers send one index; the picker sends a list."""
    fake = fake_sd()
    tui = _bare_tui()

    tui.start_mic_monitor(4)
    assert set(tui._mic_monitor_streams) == {4}

    # String indices (JSON numbers can arrive as str) and junk are tolerated.
    tui.start_mic_monitor(["4", "nonsense", 9, 9])
    assert set(tui._mic_monitor_streams) == {4, 9}
    assert [dev for dev, _ in fake.opened] == [4, 9]


def test_defaults_to_the_configured_input_device(fake_sd, monkeypatch):
    import t2

    fake_sd()
    tui = _bare_tui()
    monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 6)

    tui.start_mic_monitor()

    assert set(tui._mic_monitor_streams) == {6}


def test_hw_device_falls_back_to_its_native_rate(fake_sd):
    """Raw ALSA ``hw:`` devices routinely reject 16 kHz."""
    fake = fake_sd(native_rates={4: 44100}, reject_rates={(4, 16000)})
    tui = _bare_tui()

    tui.start_mic_monitor([4])

    assert fake.opened == [(4, 44100)]
    assert set(tui._mic_monitor_streams) == {4}


def test_rate_is_probed_before_opening(fake_sd):
    """A refused rate must be discovered without opening a stream.

    A *failed* open makes PortAudio write ALSA errors to stderr, which is the
    terminal the TUI is drawing on, so it corrupts the picker's rendering.
    """
    fake = fake_sd(native_rates={4: 44100}, reject_rates={(4, 16000)})
    tui = _bare_tui()

    tui.start_mic_monitor([4])

    assert fake.checked == [(4, 16000), (4, 44100)]
    assert fake.opened == [(4, 44100)], "exactly one open, at the rate that works"


def test_device_that_fails_to_open_after_its_probe_is_skipped(fake_sd):
    """The exclusive-access case: the probe passes but the open still fails."""
    fake_sd(open_failures={(4, 16000)})
    tui = _bare_tui()

    tui.start_mic_monitor([4, 5])

    assert set(tui._mic_monitor_streams) == {5}
    assert tui._mic_levels == {5: 0.0}


def test_a_device_that_refuses_every_rate_is_skipped(fake_sd):
    """One busy device must not stop the other rows from metering."""
    fake_sd(native_rates={4: 44100}, reject_rates={(4, 16000), (4, 44100)})
    tui = _bare_tui()

    tui.start_mic_monitor([4, 5])

    assert set(tui._mic_monitor_streams) == {5}
    assert tui._mic_levels == {5: 0.0}


def test_callback_publishes_only_its_own_device(fake_sd):
    fake_sd()
    tui = _bare_tui()
    tui.start_mic_monitor([4, 5])
    tui.sent.clear()
    tui._last_vu_sent = 0.0  # bypass the 30 ms send throttle

    block = np.full((800, 1), 0.1, dtype=np.float32)  # rms 0.1 -> 0.8
    tui._mic_monitor_streams[5].callback(block, 800, None, None)

    assert tui._mic_levels == {4: 0.0, 5: pytest.approx(0.8)}
    assert tui.sent[-1]["levels"] == [
        {"i": 4, "level": 0.0},
        {"i": 5, "level": pytest.approx(0.8)},
    ]


def test_callback_ignores_a_device_that_was_released(fake_sd):
    """A stream can deliver one last block after stop(); it must not resurrect
    a device the picker has already scrolled away from."""
    fake_sd()
    tui = _bare_tui()
    tui.start_mic_monitor([4, 5])
    stale = tui._mic_monitor_streams[4]
    tui.start_mic_monitor([5])
    tui.sent.clear()

    block = np.full((800, 1), 0.1, dtype=np.float32)
    stale.callback(block, 800, None, None)

    assert 4 not in tui._mic_levels
    assert 4 not in tui._mic_monitor_streams
    assert tui.sent == []


def test_stop_releases_every_stream_and_clears_the_frontend(fake_sd):
    fake_sd()
    tui = _bare_tui()
    tui.start_mic_monitor([4, 5])
    streams = list(tui._mic_monitor_streams.values())
    tui.sent.clear()

    tui.stop_mic_monitor()

    assert all(s.stopped and s.closed for s in streams)
    assert tui._mic_monitor_streams == {}
    assert tui._mic_levels == {}
    # An empty list is how the frontend is told to blank every row.
    assert tui.sent[-1] == {"t": "vu", "level": 0.0, "levels": []}


def test_empty_visible_set_clears_the_frontend(fake_sd):
    """A filter matching nothing monitors nothing -- and says so."""
    fake_sd()
    tui = _bare_tui()
    tui.start_mic_monitor([4])
    tui.sent.clear()

    tui.start_mic_monitor([])

    assert tui._mic_monitor_streams == {}
    assert tui.sent[-1] == {"t": "vu", "level": 0.0, "levels": []}


def test_unavailable_sounddevice_is_not_fatal(monkeypatch):
    """No PortAudio at all: the picker still opens, it just shows flat bars."""
    tui = _bare_tui()
    # A ``None`` entry in sys.modules makes ``import sounddevice`` raise
    # ImportError, which is what a machine without PortAudio looks like.
    monkeypatch.setitem(sys.modules, "sounddevice", None)

    tui.start_mic_monitor([4])

    assert tui._mic_monitor_streams == {}
    assert tui.sent == []


def test_sounddevice_without_stream_support_is_not_fatal(monkeypatch):
    """A bare module (no InputStream) fails per device, it does not propagate."""
    monkeypatch.setitem(sys.modules, "sounddevice", types.ModuleType("sounddevice"))
    tui = _bare_tui()

    tui.start_mic_monitor([4, 5])

    assert tui._mic_monitor_streams == {}
    assert tui.sent[-1] == {"t": "vu", "level": 0.0, "levels": []}
