#!/usr/bin/env python3
"""Re-scanning audio devices recovers a mic PortAudio left out of its list.

PortAudio builds its device list once, inside ``Pa_Initialize()``, and *drops*
any device it cannot open at that moment instead of marking it unavailable — so a
mic another app was holding at startup stays invisible to the picker for the life
of the process (that is how a connected Blue Snowball went missing). These tests
pin the recovery: re-initialise PortAudio, re-resolve the chosen mic by name
(device indices shift whenever the list changes), and report which inputs are
still missing and who is holding them.

No hardware is touched: ``sounddevice`` is faked, including the device list.
These are model-free and run on Linux, Windows, and WSL.
"""
import contextlib
import sys
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import t2  # noqa: E402

# The maintainer's stack: the Snowball is present but was held at startup.
DEVICES = [
    {"index": 0, "name": "pipewire", "max_input_channels": 128},
    {"index": 1, "name": "default", "max_input_channels": 128},
    {"index": 2, "name": "HD-Audio Generic: ALC257 Analog (hw:1,0)", "max_input_channels": 2},
    {"index": 3, "name": "Blue Snowball: USB Audio (hw:4,0)", "max_input_channels": 1},
]


@pytest.fixture
def audio(monkeypatch):
    """A fake sounddevice that records what the re-scan does, in order."""
    from unittest.mock import MagicMock

    calls = []
    state = {"devices": list(DEVICES), "missing": [], "fail": None}

    def record(name):
        calls.append(name)

    mock_sd = MagicMock()

    def terminate():
        record("terminate")
        if state["fail"]:
            raise RuntimeError(state["fail"])

    mock_sd._terminate.side_effect = terminate
    mock_sd._initialize.side_effect = lambda: record("initialize")

    monkeypatch.setattr(t2, "sd", mock_sd)
    monkeypatch.setattr(t2, "silence_stderr", contextlib.nullcontext)
    monkeypatch.setattr(t2, "_close_cached_input_streams", lambda: record("close_streams"))
    monkeypatch.setattr(t2, "prewarm_input_stream", lambda: record("prewarm"))
    monkeypatch.setattr(t2, "save_audio_config", lambda: record("save_config"))
    monkeypatch.setattr(t2, "get_input_devices", lambda: list(state["devices"]))
    monkeypatch.setattr(t2, "set_default_input_device", lambda idx: record(f"default:{idx}"))
    monkeypatch.setattr(
        t2, "find_device_index", lambda name: _find(state["devices"], name)
    )

    import audio_state

    monkeypatch.setattr(
        audio_state, "missing_input_devices", lambda names: list(state["missing"])
    )
    return {"calls": calls, "state": state}


def _find(devices, name):
    """The real ``find_device_index`` rule: first name substring that can capture."""
    if not name:
        return None
    for dev in devices:
        if dev["max_input_channels"] > 0 and name.lower() in dev["name"].lower():
            return dev["index"]
    return None


class TestRescanAndReinit:
    def test_drops_cached_streams_before_terminating_portaudio(self, audio, monkeypatch):
        """Pa_Terminate closes open streams, so ours must go first."""
        monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 1, raising=False)
        monkeypatch.setattr(t2, "PRIMARY_DEVICE_NAME", "default", raising=False)

        summary = t2.rescan_audio_devices()

        assert summary["ok"] is True
        assert audio["calls"][:3] == ["close_streams", "terminate", "initialize"]
        assert summary["count"] == len(DEVICES)
        assert "Blue Snowball: USB Audio (hw:4,0)" in summary["devices"]

    def test_a_refused_reinitialise_is_reported_not_raised(self, audio):
        audio["state"]["fail"] = "Pa_Terminate exploded"

        summary = t2.rescan_audio_devices()

        assert summary["ok"] is False
        assert "Pa_Terminate exploded" in summary["message"]
        assert "initialize" not in audio["calls"]      # never half-initialised

    def test_the_warm_stream_is_prewarmed_again(self, audio):
        t2.rescan_audio_devices()

        assert "prewarm" in audio["calls"]


class TestSelectionIsReResolved:
    def test_the_selected_mic_is_re_pointed_at_its_new_index(self, audio, monkeypatch):
        """Indices are positions, so they shift when the list changes."""
        monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 1, raising=False)   # stale
        monkeypatch.setattr(t2, "PRIMARY_DEVICE_NAME", "Blue Snowball", raising=False)

        t2.rescan_audio_devices()

        assert t2.INPUT_DEVICE_INDEX == 3
        assert "default:3" in audio["calls"]
        assert "save_config" in audio["calls"]

    def test_an_unresolvable_selection_is_left_alone(self, audio, monkeypatch):
        """A mic that is still missing must not silently become another device."""
        monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 2, raising=False)
        monkeypatch.setattr(t2, "PRIMARY_DEVICE_NAME", "Some Absent Mic", raising=False)

        t2.rescan_audio_devices()

        assert t2.INPUT_DEVICE_INDEX == 2
        assert "save_config" not in audio["calls"]

    def test_a_stale_index_past_the_end_is_dropped(self, audio, monkeypatch):
        """A shorter list must not leave a saved index pointing at nothing."""
        monkeypatch.setattr(t2, "INPUT_DEVICE_INDEX", 9, raising=False)
        monkeypatch.setattr(t2, "PRIMARY_DEVICE_NAME", None, raising=False)

        t2.rescan_audio_devices()

        assert t2.INPUT_DEVICE_INDEX is None
        assert "save_config" in audio["calls"]


class TestWhatIsStillMissing:
    def test_names_the_app_holding_a_missing_mic(self, audio):
        audio["state"]["missing"] = [
            {"name": "Blue Snowball", "card_id": "Snowball", "holder": "GNOME Settings"}
        ]

        summary = t2.rescan_audio_devices()

        assert summary["missing"][0]["holder"] == "GNOME Settings"
        assert summary["notice"] == "Blue Snowball is being used by GNOME Settings"
        assert "GNOME Settings" in summary["message"]
        assert "still missing" in summary["message"]

    def test_a_missing_mic_without_a_holder_reads_as_busy(self, audio):
        audio["state"]["missing"] = [
            {"name": "Arctis Nova 7", "card_id": "A7", "holder": None}
        ]

        summary = t2.rescan_audio_devices()

        assert summary["notice"] == "Arctis Nova 7 is busy or unusable"

    def test_a_clean_rescan_says_nothing_about_missing_inputs(self, audio):
        summary = t2.rescan_audio_devices()

        assert summary["missing"] == []
        assert summary["notice"] == ""
        assert summary["message"] == f"{len(DEVICES)} input devices found"

    def test_every_held_input_is_listed(self):
        notice = t2._mic_rescan_notice(
            {
                "missing": [
                    {"name": "Blue Snowball", "holder": "GNOME Settings"},
                    {"name": "Arctis Nova 7", "holder": None},
                ]
            }
        )

        assert notice == (
            "Blue Snowball is being used by GNOME Settings; "
            "Arctis Nova 7 is busy or unusable"
        )
