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

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

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
    repo_root = Path(__file__).resolve().parent.parent
    mapping = post_processor.load_custom_dictionary_from_file(repo_root / "config" / "dictionary.yaml")
    assert mapping.get("github") == "GitHub"
    for variant in ("git hub", "git-hub", "get hub"):
        assert mapping.get(variant) == "GitHub", f"missing dictionary variant {variant!r}"
    assert "GitHub" in post_processor.clean_speech_transcription("push it to git hub", skip_slm=True)
