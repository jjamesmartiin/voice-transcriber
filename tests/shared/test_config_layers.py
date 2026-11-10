#!/usr/bin/env python3
"""The config file's inline dictionary is a layer, not a replacement.

``apply_settings_from_config(dictionary=...)`` used to call
``set_custom_dictionary``, which marks the dictionaries as loaded -- so the
first-use loader returned immediately and the file layers were never read. The
shipped ``config/example-config/config.yaml.example`` has a ``dictionary:``
block and the README tells new users to copy it, so following the setup steps
dropped the shipped 166-entry dictionary and whatever ``dictionary add`` wrote
(measured: 167 entries became 1).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def clean_dictionaries(tmp_path, monkeypatch):
    """No real dictionaries: the repo's, the user's, and the CLI's are all stubbed."""
    import dictionary
    import post_processor as pp

    empty = tmp_path / "missing.yaml"
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(dictionary, "get_common_dictionary_path", lambda: empty)
    monkeypatch.setattr(dictionary, "get_local_dictionary_path", lambda: empty)
    monkeypatch.setattr(pp, "_INLINE_DICTIONARY", {})
    pp.reset_custom_dictionary()
    return pp, dictionary, tmp_path


def test_an_inline_dictionary_does_not_disable_the_file_layers(clean_dictionaries):
    pp, dictionary, tmp_path = clean_dictionaries
    local = tmp_path / "dictionary.local.yaml"
    local.write_text("dictionary:\n  'my landlord': 'Michael'\n", encoding="utf-8")
    dictionary.get_local_dictionary_path = lambda: local

    pp.apply_settings_from_config(dictionary={"pull request": "PR"})
    merged = pp.get_custom_dictionary()

    assert merged.get("pull request") == "PR", "the inline layer vanished"
    assert merged.get("my landlord") == "Michael", "the personal file layer vanished"


def test_the_inline_layer_wins_over_a_shipped_file(clean_dictionaries):
    """Precedence is common < inline < personal, as the loader documents."""
    pp, dictionary, tmp_path = clean_dictionaries
    common = tmp_path / "dictionary.yaml"
    common.write_text("dictionary:\n  'k8s': 'Kubernetes'\n  'pr': 'pull request'\n",
                      encoding="utf-8")
    dictionary.get_common_dictionary_path = lambda: common

    pp.apply_settings_from_config(dictionary={"pr": "PR"})
    merged = pp.get_custom_dictionary()

    assert merged.get("k8s") == "Kubernetes"   # shipped entry survives
    assert merged.get("pr") == "PR"            # inline overrides it
