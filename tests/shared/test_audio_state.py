"""A muted microphone must be visible, and a mute we apply must not outlive us.

The failure these tests pin is the one that actually bit the maintainer: a muted
PipeWire source opens fine, reports sane channels/rates, and records exact zeros,
so every existing check said "microphone healthy" while Discord reported
``no-audio-input-detected`` and nothing was audible. Nothing here touches real
hardware or the developer's audio stack — ``wpctl`` is faked.
"""
from __future__ import annotations

import os
import subprocess
from io import StringIO

import pytest


class FakeWpctl:
    """A stand-in for WirePlumber's CLI, faithful about the bits we parse.

    Models one default source: its mute flag and its description, plus the two
    degenerate cases that matter — wpctl missing entirely, and wpctl present but
    failing (timeout/crash), which must read as "unknown", never as "unmuted".
    """

    def __init__(self, muted: bool = False, name: str = "Blue Snowball Mono"):
        self.muted = muted
        self.name = name
        self.calls: list[list[str]] = []
        self.broken: str | None = None      # None | "timeout" | "crash"
        self.stubborn = False               # set-mute succeeds but does nothing
        self.has_description = True
        self.status = ""                    # verbatim `wpctl status` output

    # -- the subprocess.run replacement -----------------------------------
    def run(self, args, **kwargs):
        self.calls.append(list(args))
        if self.broken == "timeout":
            raise subprocess.TimeoutExpired(args, 1)
        if self.broken == "crash":
            raise FileNotFoundError("wpctl vanished")
        sub = args[1] if len(args) > 1 else ""
        if sub == "get-volume":
            out = "Volume: 0.44 [MUTED]\n" if self.muted else "Volume: 0.44\n"
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
        if sub == "inspect":
            out = f'  * node.description = "{self.name}"\n' if self.has_description else ""
            return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")
        if sub == "status":
            return subprocess.CompletedProcess(args, 0, stdout=self.status, stderr="")
        if sub == "set-mute":
            if not self.stubborn:
                self.muted = args[3] == "1"
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="")

    # -- assertions helpers ------------------------------------------------
    def mute_calls(self):
        return [c for c in self.calls if len(c) > 1 and c[1] == "set-mute"]

    def muted_by(self, value: bool):
        self.muted = value


@pytest.fixture
def wpctl(monkeypatch):
    """Fake wpctl: present on PATH, answering from a model of the audio stack."""
    import audio_state

    fake = FakeWpctl()
    monkeypatch.setattr(audio_state.shutil, "which", lambda tool: "/usr/bin/wpctl" if tool == "wpctl" else None)
    monkeypatch.setattr(audio_state.subprocess, "run", fake.run)
    return fake


@pytest.fixture
def no_wpctl(monkeypatch):
    """Host without WirePlumber (native Windows / macOS / no PipeWire at all)."""
    import audio_state

    monkeypatch.setattr(audio_state.shutil, "which", lambda tool: None)
    return audio_state


# ---------------------------------------------------------------------------
# Reading the state
# ---------------------------------------------------------------------------
class TestReadingMuteState:
    def test_reports_muted(self, wpctl):
        import audio_state

        wpctl.muted_by(True)
        assert audio_state.get_default_source_mute() is True

    def test_reports_unmuted(self, wpctl):
        import audio_state

        assert audio_state.get_default_source_mute() is False

    def test_reads_the_default_source_alias_not_a_pinned_node(self, wpctl):
        import audio_state

        audio_state.get_default_source_mute()
        assert ["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"] in wpctl.calls

    def test_parses_the_description_with_its_star_marker(self, wpctl):
        import audio_state

        assert audio_state.get_default_source_name() == "Blue Snowball Mono"

    def test_missing_description_is_unknown_not_empty_string(self, wpctl):
        import audio_state

        wpctl.has_description = False
        assert audio_state.get_default_source_name() is None


class TestUnknownIsNeverHealthy:
    """`None` means unknown. Callers must not read it as "unmuted"."""

    def test_no_wpctl_means_unsupported_and_unknown(self, no_wpctl):
        assert no_wpctl.wpctl_available() is False
        assert no_wpctl.get_default_source_mute() is None
        assert no_wpctl.get_default_source_name() is None
        state = no_wpctl.describe_state()
        assert state["supported"] is False
        assert state["muted"] is None

    def test_no_wpctl_means_mutating_calls_are_inert(self, no_wpctl):
        assert no_wpctl.set_default_source_mute(True) is False
        assert no_wpctl.unmute_default_source() is False

    @pytest.mark.parametrize("failure", ["timeout", "crash"])
    def test_broken_wpctl_degrades_to_unknown_without_raising(self, wpctl, failure):
        import audio_state

        wpctl.broken = failure
        assert audio_state.get_default_source_mute() is None
        assert audio_state.get_default_source_name() is None
        assert audio_state.set_default_source_mute(False) is False
        assert audio_state.describe_state()["muted"] is None


# ---------------------------------------------------------------------------
# Repairing it (only on request)
# ---------------------------------------------------------------------------
class TestUnmuteDefaultSource:
    def test_unmutes_a_muted_source(self, wpctl):
        import audio_state

        wpctl.muted_by(True)
        assert audio_state.unmute_default_source() is True
        assert wpctl.muted is False
        assert ["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "0"] in wpctl.calls

    def test_already_unmuted_issues_no_command(self, wpctl):
        import audio_state

        assert audio_state.unmute_default_source() is True
        assert wpctl.mute_calls() == []

    def test_a_stubborn_mute_is_reported_as_failure(self, wpctl):
        """wpctl can accept set-mute and still leave the node muted; verify, don't assume."""
        import audio_state

        wpctl.muted_by(True)
        wpctl.stubborn = True
        assert audio_state.unmute_default_source() is False


# ---------------------------------------------------------------------------
# The guard: a mute must never outlive the code that set it
# ---------------------------------------------------------------------------
class TestPreserveDefaultSourceMute:
    def test_restores_after_a_mute(self, wpctl):
        import audio_state

        with audio_state.preserve_default_source_mute() as before:
            assert before is False
            audio_state.set_default_source_mute(True)
            assert wpctl.muted is True
        assert wpctl.muted is False
        assert wpctl.mute_calls()[-1][-1] == "0"

    def test_restores_when_the_body_raises(self, wpctl):
        import audio_state

        with pytest.raises(RuntimeError):
            with audio_state.preserve_default_source_mute():
                audio_state.set_default_source_mute(True)
                raise RuntimeError("audio test blew up")
        assert wpctl.muted is False

    def test_does_not_fight_a_mute_the_user_asked_for(self, wpctl):
        """Entering already muted and changing nothing must issue no command."""
        import audio_state

        wpctl.muted_by(True)
        with audio_state.preserve_default_source_mute() as before:
            assert before is True
        assert wpctl.muted is True
        assert wpctl.mute_calls() == []

    def test_restores_an_intentional_mute_that_code_cleared(self, wpctl):
        import audio_state

        wpctl.muted_by(True)
        with audio_state.preserve_default_source_mute():
            audio_state.unmute_default_source()
            assert wpctl.muted is False
        assert wpctl.muted is True

    def test_is_a_noop_when_the_state_is_unknown(self, no_wpctl):
        with no_wpctl.preserve_default_source_mute() as before:
            assert before is None
            no_wpctl.set_default_source_mute(True)
        # No crash, no claim of restoration.

    def test_never_raises_when_restoring_fails(self, wpctl, monkeypatch):
        """A failed cleanup is logged, not thrown at a user mid-dictation."""
        import audio_state

        def explode(_muted):
            raise OSError("wireplumber died mid-restore")

        monkeypatch.setattr(audio_state, "set_default_source_mute", explode)
        with audio_state.preserve_default_source_mute() as before:
            assert before is False
            wpctl.muted_by(True)      # muted behind the API's back, restore must fail
        assert wpctl.muted is True    # the mute stands — we never claim a fix we did not make


# ---------------------------------------------------------------------------
# doctor: detect it loudly, and fix it only when asked
# ---------------------------------------------------------------------------
class TestDoctorMuteCheck:
    def test_flags_a_muted_microphone(self, wpctl):
        import doctor

        wpctl.muted_by(True)
        info = doctor.check_microphone_mute()
        assert info["checked"] is True
        assert info["ok"] is False
        assert info["muted"] is True
        assert "MUTED" in info["detail"]
        assert "no audio input detected" in info["detail"]
        assert "set-mute" in info["fix_command"]

    def test_a_live_microphone_passes(self, wpctl):
        import doctor

        info = doctor.check_microphone_mute()
        assert info["ok"] is True
        assert info["muted"] is False
        assert wpctl.mute_calls() == []

    def test_reports_unchecked_on_hosts_without_wpctl(self, no_wpctl):
        import doctor

        info = doctor.check_microphone_mute()
        assert info["ok"] is True
        assert info["checked"] is False
        assert info["muted"] is None

    def test_fix_unmutes_and_clears_the_failure(self, wpctl):
        import doctor

        wpctl.muted_by(True)
        info = doctor.check_microphone_mute(fix=True)
        assert info["ok"] is True
        assert info["fixed"] is True
        assert info["muted"] is False
        assert wpctl.muted is False

    def test_fix_is_never_implicit(self, wpctl):
        """Diagnosing must not change the user's audio stack."""
        import doctor

        wpctl.muted_by(True)
        doctor.check_microphone_mute()
        assert wpctl.muted is True
        assert wpctl.mute_calls() == []


class TestDoctorWiring:
    """A muted mic must fail the run and print the fix, not hide behind [✓]."""

    @pytest.fixture
    def stub_checks(self, monkeypatch):
        import doctor

        monkeypatch.setattr(doctor, "check_torch_acceleration", lambda: {"ok": True, "version": "2.0", "device": "cpu", "device_name": "CPU"})
        monkeypatch.setattr(doctor, "check_audio_devices", lambda: {"ok": True, "count": 1, "devices": [{"name": "Built-in", "is_default": True}]})
        monkeypatch.setattr(doctor, "check_hotkeys_and_permissions", lambda plat: {"ok": True, "details": [], "warnings": [], "errors": []})
        monkeypatch.setattr(doctor, "check_clipboard_and_typing", lambda plat: {"ok": True, "tools": {}})
        monkeypatch.setattr(doctor, "check_model_weights", lambda: {"ok": True, "cached": True, "path": "/m", "size_mb": 1.0})
        monkeypatch.setattr(doctor, "check_daemon", lambda: {"running": False, "pid": None, "socket": None})

    def test_muted_mic_fails_the_run_and_prints_the_fix(self, wpctl, stub_checks):
        import doctor

        wpctl.muted_by(True)
        buf = StringIO()
        report = doctor.run_doctor(stream=buf)
        assert report["ok"] is False
        assert report["microphone"]["ok"] is False
        assert "[✗] Microphone Mute" in buf.getvalue()
        assert "wpctl set-mute @DEFAULT_AUDIO_SOURCE@ 0" in buf.getvalue()
        assert "doctor --fix" in buf.getvalue()

    def test_healthy_mic_passes_the_run(self, wpctl, stub_checks):
        import doctor

        buf = StringIO()
        report = doctor.run_doctor(stream=buf)
        assert report["ok"] is True
        assert "[✓] Microphone Mute" in buf.getvalue()

    def test_fix_run_repairs_and_passes(self, wpctl, stub_checks):
        import doctor

        wpctl.muted_by(True)
        buf = StringIO()
        report = doctor.run_doctor(stream=buf, fix=True)
        assert report["ok"] is True
        assert report["microphone"]["fixed"] is True
        assert "Unmuted" in buf.getvalue()


# ---------------------------------------------------------------------------
# The app's own startup health check
# ---------------------------------------------------------------------------
class TestStartupHealthCheck:
    def test_muted_default_source_is_reported_at_startup(self, monkeypatch, wpctl):
        import t2
        from unittest.mock import MagicMock

        # Not a WSL host: keep the WSLg/PulseAudio probe out of this test.
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [{"name": "Blue Snowball", "max_input_channels": 2}]
        monkeypatch.setattr(t2, "sd", mock_sd)

        wpctl.muted_by(True)
        is_healthy, issues = t2.check_microphone_health()
        assert is_healthy is False
        assert any("MUTED" in issue for issue in issues)
        assert any("wpctl set-mute" in issue for issue in issues)

    def test_live_default_source_stays_quiet(self, monkeypatch, wpctl):
        import t2
        from unittest.mock import MagicMock

        monkeypatch.setattr(os.path, "exists", lambda p: False)
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [{"name": "Blue Snowball", "max_input_channels": 2}]
        monkeypatch.setattr(t2, "sd", mock_sd)

        is_healthy, issues = t2.check_microphone_health()
        assert is_healthy is True
        assert issues == []

    def test_broken_audio_state_probe_does_not_break_startup(self, monkeypatch, wpctl):
        """A diagnostic must never be able to stop the app from starting."""
        import t2
        from unittest.mock import MagicMock

        monkeypatch.setattr(os.path, "exists", lambda p: False)
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = [{"name": "Blue Snowball", "max_input_channels": 2}]
        monkeypatch.setattr(t2, "sd", mock_sd)

        wpctl.broken = "crash"
        is_healthy, issues = t2.check_microphone_health()
        assert is_healthy is True
        assert issues == []


# ---------------------------------------------------------------------------
# Inputs the device list is missing, and who is holding them
# ---------------------------------------------------------------------------
# PortAudio drops a device it cannot open while building its list, so an input
# another app is capturing from vanishes from the picker. Recovering it needs the
# two facts below: which ALSA cards can capture, and which client is holding one.
#
# A real `wpctl status` Streams section — one client line, then its streams.
# GNOME Settings meters the Snowball (an input stream, `<`), while Chrome's
# playback to a Bluetooth sink (`>`) is not a holder and must be ignored.
WPCTL_STATUS = """
Audio
 ├─ Devices:
 │      86. soundcore Q20i                      [bluez5]
 ├─ Sources:
 │  *  132. Blue Snowball Mono                  [vol: 0.32]
 └─ Streams:
        53. GNOME Settings
            103. input_MONO      < Blue Snowball:capture_MONO\t[active]
        84. Google Chrome
            140. output_FR       > soundcore Q20i:playback_FR\t[active]

Video
 └─ Devices:
        51. Integrated RGB Camera
"""


@pytest.fixture
def proc_asound(tmp_path, monkeypatch):
    """A fake ``/proc/asound``: two capture cards and one playback-only card."""
    import audio_state

    root = tmp_path / "asound"
    root.mkdir()
    (root / "cards").write_text(
        " 0 [Generic        ]: HDA-Intel - HD-Audio Generic\n"
        "                      HD-Audio Generic at 0xb04c8000 irq 113\n"
        " 4 [Snowball       ]: USB-Audio - Blue Snowball\n"
        "                      BLUE MICROPHONE Blue Snowball at usb-0000:c6:00.4-1.3.4.2, full speed\n"
        " 5 [A7             ]: USB-Audio - Arctis Nova 7\n"
        "                      SteelSeries Arctis Nova 7 at usb-0000:c6:00.4-1.3.4.1, full speed\n"
    )
    (root / "card0").mkdir()                    # HDMI only: no capture PCM
    (root / "card0" / "pcm3p").write_text("")
    for card in ("card4", "card5"):
        (root / card / "pcm0c").mkdir(parents=True)
    monkeypatch.setattr(audio_state, "_PROC_ASOUND", str(root))
    return root


# PortAudio names a card's devices "<card name>: <pcm> (hw:N,M)".
ALL_MICS = [
    "HD-Audio Generic: ALC257 Analog (hw:1,0)",
    "Blue Snowball: USB Audio (hw:4,0)",
    "Arctis Nova 7: USB Audio (hw:3,0)",
]


class TestUnlistedInputs:
    def test_capture_cards_are_read_from_proc_asound(self, proc_asound):
        import audio_state

        assert audio_state.alsa_capture_cards() == {
            "Blue Snowball": "Snowball",
            "Arctis Nova 7": "A7",
        }

    def test_a_playback_only_card_is_not_an_input(self, proc_asound):
        """Card 0 (HDMI) has no pcmNc, so it must never be reported as missing."""
        import audio_state

        assert "HD-Audio Generic" not in audio_state.alsa_capture_cards()

    def test_no_proc_asound_is_unsupported_not_an_error(self, tmp_path, monkeypatch):
        import audio_state

        monkeypatch.setattr(audio_state, "_PROC_ASOUND", str(tmp_path / "absent"))
        assert audio_state.alsa_capture_cards() == {}
        assert audio_state.missing_input_devices(ALL_MICS) == []

    def test_every_reported_device_is_accounted_for(self, proc_asound, wpctl):
        import audio_state

        assert audio_state.missing_input_devices(ALL_MICS) == []

    def test_an_unlisted_card_names_the_app_holding_it(self, proc_asound, wpctl):
        import audio_state

        wpctl.status = WPCTL_STATUS
        missing = audio_state.missing_input_devices(
            [n for n in ALL_MICS if "Snowball" not in n]
        )
        assert missing == [
            {"name": "Blue Snowball", "card_id": "Snowball", "holder": "GNOME Settings"}
        ]

    def test_playback_streams_are_not_read_as_holders(self, proc_asound, wpctl):
        """Chrome plays *into* the Bluetooth sink; that is not holding the mic."""
        import audio_state

        wpctl.status = WPCTL_STATUS
        holders = audio_state.active_source_holders()
        assert holders == {"Blue Snowball": "GNOME Settings"}

    def test_an_unlisted_card_without_a_holder_is_still_reported(self, proc_asound, wpctl):
        import audio_state

        wpctl.status = ""          # nothing is streaming
        missing = audio_state.missing_input_devices(["HD-Audio Generic: ALC257 Analog (hw:1,0)"])
        assert [m["name"] for m in missing] == ["Blue Snowball", "Arctis Nova 7"]
        assert all(m["holder"] is None for m in missing)

    def test_no_wpctl_means_no_holders_but_still_diagnoses(self, proc_asound, no_wpctl):
        """A host without PipeWire (or wpctl) loses the "who", not the "what"."""
        import audio_state

        assert audio_state.active_source_holders() == {}
        missing = audio_state.missing_input_devices([])
        assert [m["name"] for m in missing] == ["Blue Snowball", "Arctis Nova 7"]
        assert all(m["holder"] is None for m in missing)

    def test_a_broken_wpctl_degrades_to_no_holders(self, proc_asound, wpctl):
        import audio_state

        wpctl.broken = "crash"
        assert audio_state.active_source_holders() == {}


class TestDoctorBluetoothProfile:
    """Installs that ran a pre-1.2.1 build left autoswitch disabled forever.

    The app cannot tell whether the user or its former self disabled it, so doctor
    reports and suggests rather than failing — but it must not stay silent, because
    the symptom (a headset that never switches to hands-free) looks like missing
    hardware.
    """

    @pytest.fixture
    def wpctl_bt(self, monkeypatch):
        """Fake `wpctl settings` so the test never reads the host's real value."""
        import t2

        state = {"autoswitch": True}
        monkeypatch.setattr(t2, "get_wireplumber_bt_autoswitch", lambda: state["autoswitch"])
        return state

    def test_enabled_autoswitch_passes(self, wpctl_bt):
        import doctor

        info = doctor.check_bluetooth_profile_policy()
        assert info["checked"] is True
        assert info["value"] is True
        assert info["ok"] is True
        assert "enabled" in info["detail"]

    def test_disabled_autoswitch_is_advisory_not_a_failure(self, wpctl_bt):
        import doctor

        wpctl_bt["autoswitch"] = False
        info = doctor.check_bluetooth_profile_policy()

        # Advisory: disabling autoswitch can be deliberate.
        assert info["ok"] is True
        assert info["value"] is False
        assert "bluetooth.autoswitch-to-headset-profile" in info["detail"]
        assert "no microphone at all" in info["detail"]
        assert "autoswitch-to-headset-profile true" in info["fix_command"]

    def test_reports_unchecked_on_hosts_without_wpctl(self, monkeypatch):
        import doctor

        monkeypatch.setattr(doctor.shutil, "which", lambda _: None)
        info = doctor.check_bluetooth_profile_policy()
        assert info["checked"] is False
        assert info["ok"] is True

    def test_an_unreadable_setting_is_not_reported_as_disabled(self, wpctl_bt, monkeypatch):
        import t2
        import doctor

        monkeypatch.setattr(t2, "get_wireplumber_bt_autoswitch", lambda: None)
        info = doctor.check_bluetooth_profile_policy()
        assert info["checked"] is False
        assert info["value"] is None

    def test_a_disabled_policy_is_surfaced_in_the_report(self, wpctl_bt):
        import doctor
        from io import StringIO

        wpctl_bt["autoswitch"] = False
        buf = StringIO()
        report = doctor.run_doctor(stream=buf)

        assert report["bluetooth_profile"]["value"] is False
        # Advisory only: it must not flip the overall result.
        assert report["ok"] is True
        assert "Bluetooth Headset" in buf.getvalue()
        assert "autoswitch-to-headset-profile true" in buf.getvalue()
