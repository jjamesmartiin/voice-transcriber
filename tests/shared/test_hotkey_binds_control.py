#!/usr/bin/env python3
"""Engine-level control-API surface for the push-to-talk binds.

The vocabulary itself is pinned in ``test_keybinds.py``; this file covers the
``hotkey`` verb end to end -- what an external program (or the settings modal's
wire commands, which call the same helpers) can actually do to a running engine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import control  # noqa: E402
import keybinds  # noqa: E402


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    main = pytest.importorskip("main")
    t2 = pytest.importorskip("t2")
    # Never touch the user's real config file (a Path: save_audio_config uses
    # .exists()/.suffix on it).
    monkeypatch.setattr(t2, "CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(t2, "HOTKEY_BINDS", keybinds.parse_binds(None))
    # The binds are pushed at the live hotkey manager; there is none here.
    engine = main.SimpleVoiceTranscriber.__new__(main.SimpleVoiceTranscriber)
    engine.tui = MagicMock()
    engine.tui.state = "READY"
    engine.recording = False
    engine.hotkey_system = None
    return engine


def _binds(engine):
    reply = engine.handle_control("hotkey", {})
    assert reply["ok"] is True
    return reply["hotkeys"]


def _dispatch(engine, value, cmd="hotkey"):
    """Round-trip through the socket layer: a bad request must never raise."""
    return control.ControlServer(engine).dispatch(json.dumps({"cmd": cmd, "value": value}))


class TestHotkeyVerb:
    def test_list_is_the_default_action(self, engine):
        assert _binds(engine) == ["alt+shift"]

    def test_add_appends_and_canonicalises(self, engine):
        engine.handle_control("hotkey", {"value": "add Control+Shift"})
        assert _binds(engine) == ["alt+shift", "ctrl+shift"]

    def test_add_accepts_a_second_bind_without_touching_the_first(self, engine):
        engine.handle_control("hotkey", {"value": "add f13"})
        engine.handle_control("hotkey", {"value": "add rightalt+shift"})
        assert _binds(engine) == ["alt+shift", "f13", "rightalt+shift"]

    def test_add_rejects_a_duplicate_alias(self, engine):
        reply = _dispatch(engine, "add alt+shift")
        assert reply["ok"] is False
        assert "alt+shift" in reply["error"]
        assert _binds(engine) == ["alt+shift"]

    def test_add_rejects_an_unknown_key(self, engine):
        reply = _dispatch(engine, "add hyper+shift")
        assert reply["ok"] is False
        assert "hyper" in reply["error"]
        assert _binds(engine) == ["alt+shift"]

    def test_add_without_a_chord_explains_itself(self, engine):
        reply = _dispatch(engine, "add")
        assert reply["ok"] is False
        assert "needs a chord" in reply["error"]

    def test_remove_drops_only_the_named_chord(self, engine):
        engine.handle_control("hotkey", {"value": "add f13"})
        engine.handle_control("hotkey", {"value": "remove alt+shift"})
        assert _binds(engine) == ["f13"]

    def test_remove_reports_an_unbound_chord(self, engine):
        reply = _dispatch(engine, "remove f13")
        assert reply["ok"] is False
        assert "not bound" in reply["error"]

    def test_the_last_bind_cannot_be_removed(self, engine):
        """Push-to-talk must stay reachable from the keyboard."""
        reply = _dispatch(engine, "remove alt+shift")
        assert reply["ok"] is False
        assert "at least one bind" in reply["error"]
        assert _binds(engine) == ["alt+shift"]

    def test_reset_restores_the_shipped_chord(self, engine):
        engine.handle_control("hotkey", {"value": "add f13"})
        engine.handle_control("hotkey", {"value": "remove alt+shift"})
        engine.handle_control("hotkey", {"value": "reset"})
        assert _binds(engine) == ["alt+shift"]

    def test_an_unknown_action_lists_the_valid_ones(self, engine):
        reply = _dispatch(engine, "frobnicate")
        assert reply["ok"] is False
        assert "list" in reply["error"] and "reset" in reply["error"]

    def test_aliases_reach_the_same_verb(self, engine):
        reply = engine.handle_control("binds", {"value": "add f13"})
        assert reply["ok"] is True
        assert reply["hotkeys"] == ["alt+shift", "f13"]

    @pytest.mark.parametrize("alias", ["hotkeys", "binds"])
    def test_every_alias_lists(self, engine, alias):
        assert engine.handle_control(alias, {})["hotkeys"] == ["alt+shift"]

    def test_the_change_survives_a_reload(self, engine, tmp_path):
        import t2

        engine.handle_control("hotkey", {"value": "add rightctrl+shift"})
        assert (tmp_path / "config.yaml").exists()
        t2.set_hotkey_binds("f13")
        t2.load_audio_config(str(tmp_path / "config.yaml"))
        assert [b.chord for b in t2.HOTKEY_BINDS] == ["alt+shift", "rightctrl+shift"]

    def test_the_frontend_is_told_about_the_new_list(self, engine):
        """The settings modal renders what the engine pushes at it."""
        engine.handle_control("hotkey", {"value": "add f13"})
        pushed = [
            call.kwargs.get("hotkeys")
            for call in engine.tui.set_config_state.call_args_list
            if call.kwargs.get("hotkeys")
        ]
        assert pushed[-1] == ["alt+shift", "f13"]

    def test_the_keys_action_lists_the_vocabulary(self, engine):
        """``hotkey keys`` is how a user (or a UI) discovers valid names."""
        reply = engine.handle_control("hotkey", {"value": "keys"})
        assert reply["ok"] is True
        assert [entry["name"] for entry in reply["keys"]] == list(
            keybinds.CANONICAL_NAMES
        )
        assert reply["hotkeys"] == ["alt+shift"]  # listing does not change binds
        assert any(entry["aliases"] for entry in reply["keys"])

    def test_the_keys_action_is_advertised(self):
        import control

        assert "keys" in control.VERBS["hotkey"]["choices"]

    def test_a_rebind_reaches_the_live_manager(self, engine):
        """A running engine must re-arm immediately, not at next launch.

        ``t2.set_hotkey_binds`` reaches a manager built by
        ``hotkeys.create_global_hotkeys`` through the shim; this engine builds
        its own straight from the HAL, so the push has to be explicit.
        """
        import t2

        engine.hotkey_system = MagicMock()
        engine.handle_control("hotkey", {"value": "add rightctrl+shift"})
        engine.hotkey_system.set_binds.assert_called_once()
        assert [b.chord for b in engine.hotkey_system.set_binds.call_args.args[0]] == [
            "alt+shift",
            "rightctrl+shift",
        ]

    def test_startup_hands_the_configured_binds_to_the_backend(self, engine, monkeypatch):
        """A rebind in the config file must apply without any further call."""
        import t2
        from main import hal as main_hal

        captured = {}
        backend = MagicMock()
        backend.devices = ["fake-device"]

        def fake_create(**kwargs):
            captured.update(kwargs)
            return backend

        monkeypatch.setattr(main_hal, "create_hotkey_manager", fake_create)
        monkeypatch.setattr(t2, "HOTKEY_BINDS", keybinds.parse_binds(["f13"]))
        engine.platform = "linux"
        engine.audio_cues = MagicMock()
        engine.clipboard_sink = MagicMock()

        assert engine.init_hotkeys() is True
        assert [b.chord for b in captured["binds"]] == ["f13"]
