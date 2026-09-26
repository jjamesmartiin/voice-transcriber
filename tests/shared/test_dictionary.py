#!/usr/bin/env python3
"""
Unit tests for high-speed Trie-compacted custom word & phrase dictionary replacer.
Tests exact matches, multi-word phrases, word boundary protection, case insensitivity,
file loading (YAML/JSON), and sub-millisecond execution latency.
"""

import os
import sys
import json
import time
import pytest
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../src")))

import post_processor
import t2


@pytest.fixture(autouse=True)
def reset_dict():
    post_processor.reset_custom_dictionary()
    yield
    post_processor.reset_custom_dictionary()


def test_empty_dictionary_noop():
    post_processor.set_custom_dictionary(None)
    assert post_processor.get_custom_dictionary() == {}
    
    text = "Testing clean speech transcription without dictionary."
    cleaned = post_processor.clean_speech_transcription(text)
    assert "Testing clean speech transcription without dictionary." in cleaned


def test_single_word_replacements():
    post_processor.set_custom_dictionary({
        "pr": "PR",
        "k8s": "Kubernetes",
        "github": "GitHub",
        "deepseq": "Deepseek",
        "deep seq": "Deepseek",
    })
    
    d = post_processor.get_custom_dictionary()
    assert d["pr"] == "PR"
    assert d["k8s"] == "Kubernetes"
    assert d["github"] == "GitHub"
    assert d["deepseq"] == "Deepseek"
    assert d["deep seq"] == "Deepseek"

    assert post_processor.clean_speech_transcription("i opened a pr on github") == "I opened a PR on GitHub."
    assert post_processor.clean_speech_transcription("Check the Pr on Github") == "Check the PR on GitHub."
    assert post_processor.clean_speech_transcription("Deploy to K8S") == "Deploy to Kubernetes."
    assert post_processor.clean_speech_transcription("I am testing deepseq models") == "I am testing Deepseek models."
    assert post_processor.clean_speech_transcription("we use deep seq for reasoning") == "we use Deepseek for reasoning."


def test_multi_word_phrases():
    post_processor.set_custom_dictionary({
        "pull request": "PR",
        "pull request review": "PR review",
        "v l l m": "vLLM",
        "vs code": "VS Code",
        "smiley face": "😊",
    })

    res = post_processor.clean_speech_transcription("i submitted a pull request for review")
    assert res == "I submitted a PR for review."

    res = post_processor.clean_speech_transcription("please do a pull request review today")
    assert res == "please do a PR review today."

    res = post_processor.clean_speech_transcription("we are serving on v l l m right now")
    assert res == "we are serving on vLLM right now."

    res = post_processor.clean_speech_transcription("send him a smiley face")
    assert res == "send him a 😊."


def test_word_boundary_protection():
    post_processor.set_custom_dictionary({
        "cat": "feline",
        "in": "inside",
        "to": "toward",
    })

    input_text = "The cat looked at the catalog and tried to catch a fish"
    cleaned = post_processor.clean_speech_transcription(input_text)
    assert "feline" in cleaned
    assert "catalog" in cleaned
    assert "catch" in cleaned


def test_load_from_yaml_file(tmp_path):
    yaml_file = tmp_path / "dict.yaml"
    yaml_content = """
dictionary:
  "pull request": "PR"
  "open ai": "OpenAI"
  "nixos": "NixOS"
"""
    yaml_file.write_text(yaml_content)

    loaded = post_processor.load_custom_dictionary_from_file(yaml_file)
    assert loaded["pull request"] == "PR"
    assert loaded["open ai"] == "OpenAI"
    assert loaded["nixos"] == "NixOS"

    res = post_processor.clean_speech_transcription("we run open ai models on nixos")
    assert res == "we run OpenAI models on NixOS."


def test_load_from_json_file(tmp_path):
    json_file = tmp_path / "dict.json"
    data = {
        "postgres": "PostgreSQL",
        "wi fi": "Wi-Fi"
    }
    json_file.write_text(json.dumps(data))

    loaded = post_processor.load_custom_dictionary_from_file(json_file)
    assert loaded["postgres"] == "PostgreSQL"
    assert loaded["wi fi"] == "Wi-Fi"

    res = post_processor.clean_speech_transcription("connect to postgres over wi fi")
    assert res == "connect to PostgreSQL over Wi-Fi."


def test_t2_config_sync(tmp_path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_content = """
model_backend: cohere
is_muted: true
dictionary:
  "docker container": "container"
  "pr": "PR"
"""
    config_file.write_text(config_content)
    monkeypatch.setattr(t2, "get_config_file", lambda: config_file)

    t2.load_audio_config()
    current = t2.get_dictionary()
    assert current.get("docker container") == "container"
    assert current.get("pr") == "PR"

    res = post_processor.clean_speech_transcription("check the docker container for pr")
    assert res == "check the container for PR."


def test_dictionary_execution_latency():
    test_dict = {f"sample word {i}": f"TARGET_{i}" for i in range(100)}
    test_dict["pull request"] = "PR"
    test_dict["vllm"] = "vLLM"
    test_dict["github"] = "GitHub"

    post_processor.set_custom_dictionary(test_dict)

    sample_sentence = "i opened a pull request on github and tested vllm service"
    
    for _ in range(10):
        post_processor.clean_speech_transcription(sample_sentence)

    iterations = 1000
    t0 = time.perf_counter()
    for _ in range(iterations):
        post_processor.clean_speech_transcription(sample_sentence)
    total_ms = (time.perf_counter() - t0) * 1000
    avg_ms = total_ms / iterations

    assert avg_ms < 0.10, f"Post-processor latency exceeded threshold: {avg_ms:.4f} ms"


def test_shipped_dictionary_maps_github_variants():
    """Regression: the shipped dictionary had GitLab but not GitHub, so dictated
    "git hub"/"get hub" fell through un-repaired."""
    repo_root = Path(__file__).resolve().parents[2]
    mapping = post_processor.load_custom_dictionary_from_file(repo_root / "config" / "dictionary.yaml")
    assert mapping.get("github") == "GitHub"
    for variant in ("git hub", "git-hub", "get hub"):
        assert mapping.get(variant) == "GitHub", f"missing dictionary variant {variant!r}"
    assert "GitHub" in post_processor.clean_speech_transcription("push it to git hub", skip_slm=True)


def test_gitea_variants_are_safe_and_get_tea_is_untouched():
    """Only unambiguous variants may map to Gitea. 'get tea' is ordinary English
    (a drink) and must survive untouched — a context-free dictionary cannot
    disambiguate it, so it must not have an entry."""
    repo_root = Path(__file__).resolve().parents[2]
    mapping = post_processor.load_custom_dictionary_from_file(repo_root / "config" / "dictionary.yaml")
    for variant in ("gitea", "git ea", "git tea"):
        assert mapping.get(variant) == "Gitea", f"missing dictionary variant {variant!r}"

    drink = post_processor.clean_speech_transcription("let's get tea", skip_slm=True)
    assert "gitea" not in drink.lower()
    assert "get tea" in drink.lower()


def test_dictionary_module_cli_and_helpers(tmp_path):
    import dictionary
    custom_yaml = tmp_path / "custom_dict.yaml"

    # 1. Add new entry
    is_new = dictionary.add_entry("cube ctl", "kubectl", path=custom_yaml)
    assert is_new is True

    # 2. Load dictionary
    loaded = dictionary.load_dictionary(path=custom_yaml)
    assert loaded.get("cube ctl") == "kubectl"

    # 3. Update existing entry
    is_new_update = dictionary.add_entry("cube ctl", "Kubectl", path=custom_yaml)
    assert is_new_update is False
    loaded2 = dictionary.load_dictionary(path=custom_yaml)
    assert loaded2.get("cube ctl") == "Kubectl"

    # 4. Remove entry
    removed = dictionary.remove_entry("cube ctl", path=custom_yaml)
    assert removed is True
    assert "cube ctl" not in dictionary.load_dictionary(path=custom_yaml)

    # 5. Remove non-existent entry returns False
    assert dictionary.remove_entry("non_existent_phrase", path=custom_yaml) is False


def test_contextual_rules_developer_vs_social_disambiguation():
    """Verify that contextual rules disambiguate homophones in developer contexts
    without corrupting everyday English."""
    repo_root = Path(__file__).resolve().parents[2]
    post_processor.load_custom_dictionary_from_file(repo_root / "config" / "dictionary.yaml")

    # Developer context -> converts to Gitea / GitHub / UART
    dev_1 = post_processor.clean_speech_transcription("push to get tea", skip_slm=True)
    assert "Gitea" in dev_1

    dev_2 = post_processor.clean_speech_transcription("Let's push to get home and get tea", skip_slm=True)
    assert "GitHub" in dev_2
    assert "Gitea" in dev_2

    dev_3 = post_processor.clean_speech_transcription("check the get tea server status", skip_slm=True)
    assert "Gitea server" in dev_3

    dev_4 = post_processor.clean_speech_transcription("connect to device over you art port", skip_slm=True)
    assert "UART port" in dev_4

    # Everyday social / beverage / domestic context -> preserved untouched
    social_1 = post_processor.clean_speech_transcription("hey would you like to get tea with me", skip_slm=True)
    assert "Gitea" not in social_1
    assert "get tea" in social_1.lower()

    social_2 = post_processor.clean_speech_transcription("let's grab some iced get tea", skip_slm=True)
    assert "Gitea" not in social_2

    social_3 = post_processor.clean_speech_transcription("I need to get home before dinner", skip_slm=True)
    assert "GitHub" not in social_3
    assert "get home" in social_3.lower()

    social_4 = post_processor.clean_speech_transcription("are you an art student at the gallery", skip_slm=True)
    assert "UART" not in social_4


def test_contextual_rules_cli_and_roundtrip(tmp_path):
    import dictionary
    custom_yaml = tmp_path / "test_contextual.yaml"

    # Add contextual rule
    is_new = dictionary.add_contextual_rule(
        target="Kubectl",
        spoken=["cube control", "cube ctl"],
        triggers_before=["run", "apply"],
        triggers_after=["get", "describe"],
        guards=["remote control", "game control"],
        path=custom_yaml,
    )
    assert is_new is True

    # Load and verify
    rules = dictionary.load_contextual_rules(path=custom_yaml)
    assert len(rules) == 1
    assert rules[0]["target"] == "Kubectl"
    assert "cube control" in rules[0]["spoken"]
    assert "run" in rules[0]["triggers_before"]
    assert "remote control" in rules[0]["guards"]

    # Remove contextual rule
    assert dictionary.remove_contextual_rule("Kubectl", path=custom_yaml) is True
    assert len(dictionary.load_contextual_rules(path=custom_yaml)) == 0


