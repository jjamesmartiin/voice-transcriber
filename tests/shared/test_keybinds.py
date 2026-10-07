#!/usr/bin/env python3
"""The push-to-talk bind vocabulary, and the config it round-trips through.

``voice_transcriber.keybinds`` is the single place that knows what ``alt+shift``,
``rightctrl+shift`` or ``f13`` mean on each OS. These tests pin that vocabulary,
because three separate backends (evdev, pynput, and the WSL PowerShell bridge)
resolve the *same* names through the *same* tables -- if the tables drift, a
user who rebinds gets three different keyboards.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import control  # noqa: E402
import keybinds  # noqa: E402
import t2  # noqa: E402
from voice_transcriber import keybinds as pkg_keybinds  # noqa: E402


class TestVocabulary:
    """Names, aliases and the config shape."""

    def test_missing_config_means_the_shipped_chord(self):
        binds = keybinds.parse_binds(None)
        assert [b.chord for b in binds] == ["alt+shift"]
        assert binds[0].action == "dictate"

    def test_empty_list_also_falls_back(self):
        """A hand-edited ``hotkeys: []`` must not leave the app unbindable."""
        assert [b.chord for b in keybinds.parse_binds([])] == ["alt+shift"]

    def test_aliases_resolve_to_one_canonical_spelling(self):
        assert keybinds.parse_chord("Control+Shift") == ("ctrl", "shift")
        assert keybinds.parse_chord("control+shift") == keybinds.parse_chord("ctrl+shift")
        assert keybinds.parse_chord("super") == ("meta",)
        assert keybinds.parse_chord("Option") == ("alt",)
        assert keybinds.parse_chord("escape") == ("esc",)

    def test_sides_are_distinct_names(self):
        assert keybinds.parse_chord("rightctrl+shift") == ("rightctrl", "shift")
        assert keybinds.parse_chord("ralt") == ("rightalt",)

    def test_config_entries_accept_strings_lists_and_mappings(self):
        assert keybinds.parse_binds("f13")[0].keys == ("f13",)
        assert keybinds.parse_binds([["ctrl", "shift"]])[0].keys == ("ctrl", "shift")
        binds = keybinds.parse_binds([{"keys": ["rightctrl", "shift"]}])
        assert binds[0].chord == "rightctrl+shift"
        assert binds[0].to_config() == {
            "keys": ["rightctrl", "shift"],
            "action": "dictate",
        }

    @pytest.mark.parametrize(
        "spec", ["", "   ", "hyper+shift", "alt+alt", "dictate"]
    )
    def test_unusable_chords_raise_with_a_user_facing_message(self, spec):
        with pytest.raises(keybinds.KeybindError):
            keybinds.parse_chord(spec)

    def test_unknown_action_is_rejected(self):
        from voice_transcriber import keybinds as kb

        with pytest.raises(kb.KeybindError, match="unknown action"):
            kb.parse_bind({"keys": ["alt", "shift"], "action": "whisper"})

    def test_both_import_paths_reach_the_same_file(self):
        assert Path(keybinds.__file__).resolve() == Path(pkg_keybinds.__file__).resolve()

    def test_a_bind_from_another_import_path_round_trips(self):
        """Class identity must not be load-bearing.

        ``voice_transcriber/main.py`` prepends its own directory to ``sys.path``,
        so a later bare ``import keybinds`` loads the same file a *second* time
        (``hal`` already has this property). A ``Bind`` built by one copy has to
        survive the other copy's ``parse_binds``, or every dict/list config
        round-trip starts raising ``'Bind' object is not iterable``.
        """
        foreign = pkg_keybinds.Bind(("alt", "shift"), "dictate")
        parsed = keybinds.parse_binds([foreign])
        assert parsed[0].keys == ("alt", "shift")
        assert parsed[0].to_config() == {
            "keys": ["alt", "shift"],
            "action": "dictate",
        }
        assert keybinds.parse_binds(foreign)[0].chord == "alt+shift"


class TestPlatformTables:
    """Every name must resolve on every platform, to the *same* meaning."""

    def test_every_name_has_both_platform_codes(self):
        names = set(keybinds.CANONICAL_NAMES)
        assert set(keybinds.EVDEV_CODES) == names
        assert set(keybinds.VK_CODES) == names

    def test_bare_modifiers_mean_either_side(self):
        assert keybinds.EVDEV_CODES["alt"] == (56, 100)  # LEFTALT, RIGHTALT
        assert keybinds.EVDEV_CODES["shift"] == (42, 54)
        assert set(keybinds.VK_CODES["alt"]) == {0xA4, 0xA5}
        assert keybinds.EVDEV_CODES["leftalt"] == (56,)
        assert keybinds.EVDEV_CODES["rightalt"] == (100,)

    def test_a_few_well_known_codes(self):
        assert keybinds.EVDEV_CODES["f13"] == (183,)
        assert keybinds.VK_CODES["f13"] == (0x7C,)
        assert keybinds.EVDEV_CODES["space"] == (57,)
        assert keybinds.VK_CODES["space"] == (0x20,)
        assert keybinds.EVDEV_CODES["esc"] == (1,)
        assert keybinds.EVDEV_CODES["q"] == (16,)

    def test_vk_groups_never_flatten_a_bare_modifier(self):
        """Grouping is what stops ``alt`` requiring both sides to be down."""
        assert keybinds.vk_groups(keybinds.parse_bind("alt+shift")) == (
            (0xA4, 0xA5),
            (0xA0, 0xA1),
        )
        assert keybinds.encode_vk_binds(keybinds.parse_binds(None)) == "164,165;160,161"
        assert keybinds.DEFAULT_VK_BINDS == "164,165;160,161"

    def test_multi_bind_encoding_keeps_all_three_delimiters_distinct(self):
        binds = keybinds.parse_binds(["alt+shift", "f13"])
        assert keybinds.encode_vk_binds(binds) == "164,165;160,161|124"


class TestBindMatching:
    """The semantics every backend must reproduce."""

    def test_all_keys_of_a_chord_are_required(self):
        bind = keybinds.parse_bind("alt+shift")
        assert keybinds.bind_hit(bind, ["alt"]) is False
        assert keybinds.bind_hit(bind, ["alt", "shift"]) is True

    def test_bare_modifier_accepts_either_side(self):
        bind = keybinds.parse_bind("alt+shift")
        assert keybinds.bind_hit(bind, ["leftalt", "rightshift"]) is True
        assert keybinds.bind_hit(bind, ["rightalt", "leftshift"]) is True

    def test_a_sided_name_rejects_the_other_side(self):
        """The reported bug, in one line: Right-Ctrl must not read as Alt."""
        bind = keybinds.parse_bind("rightalt")
        assert keybinds.bind_hit(bind, ["rightctrl"]) is False
        assert keybinds.bind_hit(bind, ["leftalt"]) is False

    def test_extra_held_keys_do_not_break_a_chord(self):
        bind = keybinds.parse_bind("ctrl+shift")
        assert keybinds.bind_hit(bind, ["leftctrl", "rightshift", "f13"]) is True


class TestKeyTable:
    """``KEYS`` is the one place a key is declared; everything else is derived.

    These tests are the reason a new key is a one-line change: if any of the
    per-platform or per-ui tables is ever restated by hand instead of derived,
    the equality checks below fail.
    """

    def test_every_row_is_complete_and_unambiguous(self):
        keybinds.validate_keys()  # AssertionError on a half-added key

    def test_the_tables_are_derived_from_the_declaration(self):
        assert keybinds.CANONICAL_NAMES == tuple(key.name for key in keybinds.KEYS)
        assert keybinds.EVDEV_CODES == {k.name: k.evdev for k in keybinds.KEYS}
        assert keybinds.VK_CODES == {k.name: k.vk for k in keybinds.KEYS}
        assert keybinds.ALIASES == {
            alias: key.name for key in keybinds.KEYS for alias in key.aliases
        }
        assert keybinds.PYNPUT_ATTRS == {k.name: k.pynput for k in keybinds.KEYS}
        # The reverse map is first-declaration-wins, so a shared attribute keeps
        # the sided name (sided rows are declared before the bare ones).
        expected_reverse: dict[str, str] = {}
        for key in keybinds.KEYS:
            for attr in key.pynput:
                expected_reverse.setdefault(attr, key.name)
        assert keybinds.PYNPUT_ATTR_TO_NAME == expected_reverse
        assert keybinds.MODIFIER_NAMES == tuple(
            key.name for key in keybinds.KEYS if key.group == "modifiers"
        )

    def test_shared_pynput_attributes_resolve_to_the_sided_name(self):
        assert keybinds.PYNPUT_ATTR_TO_NAME["ctrl_l"] == "leftctrl"
        assert keybinds.PYNPUT_ATTR_TO_NAME["alt_gr"] == "rightalt"
        assert keybinds.PYNPUT_ATTR_TO_NAME["shift_r"] == "rightshift"
        assert keybinds.PYNPUT_ATTR_TO_NAME["cmd"] == "meta"

    def test_bare_modifiers_still_offer_both_sides_forward(self):
        assert set(keybinds.PYNPUT_ATTRS["ctrl"]) == {"ctrl_l", "ctrl_r", "ctrl"}
        assert "alt_gr" in keybinds.PYNPUT_ATTRS["rightalt"]
        assert "alt" in keybinds.PYNPUT_ATTRS["alt"]

    def test_every_declared_key_is_reachable_by_name_and_alias(self):
        for key in keybinds.KEYS:
            assert keybinds.parse_chord(key.name) == (key.name,)
            for alias in key.aliases:
                assert keybinds.parse_chord(alias) == (key.name,)

    def test_a_row_alone_is_enough_for_every_backend(self):
        """No key may depend on a second table to be bindable."""
        for key in keybinds.KEYS:
            assert key.evdev, f"{key.name}: no evdev code"
            assert key.vk, f"{key.name}: no VK code"
            if key.group not in ("letters", "digits"):
                # Letters/digits resolve through their character instead.
                assert any(
                    keybinds.PYNPUT_ATTR_TO_NAME.get(attr) == key.name
                    for attr in key.pynput
                ), f"{key.name}: no pynput attribute"

    def test_the_catalogue_is_the_declaration(self):
        catalogue = keybinds.catalogue()
        assert [entry["name"] for entry in catalogue] == list(keybinds.CANONICAL_NAMES)
        assert all(entry["group"] in keybinds.KEY_GROUPS for entry in catalogue)
        by_name = {entry["name"]: entry for entry in catalogue}
        assert "option" in by_name["alt"]["aliases"]
        assert by_name["rightctrl"]["group"] == "modifiers"

    def test_a_press_resolves_to_the_sided_name_first(self):
        class FakeKey:
            def __init__(self, name):
                self.name = name

        fake_keys = types.SimpleNamespace(
            ctrl_l=FakeKey("ctrl_l"), ctrl=FakeKey("ctrl")
        )
        assert keybinds.canonical_pynput_name(fake_keys.ctrl_l, fake_keys) == "leftctrl"
        assert keybinds.canonical_pynput_name(fake_keys.ctrl, fake_keys) == "ctrl"

    def test_alt_gr_counts_as_the_right_hand_alt(self):
        assert keybinds.PYNPUT_ATTR_TO_NAME["alt_gr"] == "rightalt"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """Point t2's config persistence at a throwaway file, never the user's."""
    path = tmp_path / "audio_device_config.json"
    monkeypatch.setattr(t2, "CONFIG_FILE", path)
    return path


class TestConfigIntegration:
    """t2 loads, persists and applies the binds."""

    def test_shipped_default_matches_the_default_chord(self):
        assert keybinds.parse_binds(t2.DEFAULT_SETTINGS["HOTKEY_BINDS"]) == [
            keybinds.Bind(("alt", "shift"), "dictate")
        ]

    def test_module_default_is_the_shipped_chord(self):
        assert [b.chord for b in t2.HOTKEY_BINDS] == ["alt+shift"]

    def test_invalid_config_falls_back_and_says_so(self, caplog):
        with caplog.at_level("WARNING"):
            binds = t2._load_hotkey_binds("hyper+shift")
        assert [b.chord for b in binds] == ["alt+shift"]
        assert "invalid hotkeys config" in caplog.text
        # The fallback must name the chord that is actually in force.
        assert "alt+shift" in caplog.text

    def test_setter_normalises_and_reaches_the_live_manager(self, monkeypatch):
        import hotkeys as hotkeys_shim

        applied = []
        monkeypatch.setattr(
            hotkeys_shim, "set_global_binds", lambda binds: applied.append(binds) or True
        )
        t2.set_hotkey_binds("Control+Shift")
        assert [b.chord for b in t2.HOTKEY_BINDS] == ["ctrl+shift"]
        assert [b.chord for b in applied[-1]] == ["ctrl+shift"]
        t2.HOTKEY_BINDS = keybinds.parse_binds(None)  # restore for other tests

    def test_setter_rejects_an_unusable_chord_without_changing_anything(self):
        before = list(t2.HOTKEY_BINDS)
        # t2 imports the spec as ``voice_transcriber.keybinds``; asserting on that
        # copy's exception class is what keeps this honest, since the same file
        # can also be loaded under the bare name ``keybinds``.
        with pytest.raises(pkg_keybinds.KeybindError):
            t2.set_hotkey_binds("hyper+shift")
        assert t2.HOTKEY_BINDS == before

    def test_round_trip_through_the_config_file(self, cfg):
        t2.set_hotkey_binds(["alt+shift", "rightctrl+shift"])
        t2.save_audio_config()
        assert "hotkeys" in cfg.read_text()

        t2.set_hotkey_binds("f13")  # clobber the global, then reload from disk
        t2.load_audio_config(str(cfg))
        assert [b.chord for b in t2.HOTKEY_BINDS] == ["alt+shift", "rightctrl+shift"]
        t2.HOTKEY_BINDS = keybinds.parse_binds(None)  # restore for other tests


class TestControlSurface:
    """The binds are reachable programmatically, as the docs claim."""

    def test_catalogue_advertises_the_verb_and_its_aliases(self):
        entry = control.VERBS["hotkey"]
        assert set(entry["aliases"]) == {"hotkeys", "binds"}
        assert entry["returns"] == ["hotkeys", "keys"]
        assert any(choice.startswith("add") for choice in entry["choices"])

    @pytest.mark.parametrize("alias", ["hotkey", "hotkeys", "binds"])
    def test_alias_normalises_to_the_canonical_verb(self, alias):
        assert control.normalize_verb(alias) == "hotkey"
