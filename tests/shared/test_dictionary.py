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

#: A trie lookup is microseconds; this gate exists to catch an order-of-magnitude
#: regression (a dictionary rebuilt per call, a trie degraded to a linear scan),
#: not to certify a benchmark. It is loose on purpose: the shared CI runners are
#: noisy and a full 1000-call average was measured at 207 us there.
LATENCY_CEILING_MS = 0.5

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


def test_saved_values_round_trip_exactly(tmp_path):
    """A saved dictionary must read back as the values that went in.

    Regression: the file was written by hand with a short "needs quoting"
    character list. A value containing a newline produced a file that could not
    be parsed at all -- and since the save reads the file back for its contextual
    rules, the save itself raised, leaving the corrupt file on disk and breaking
    every later add/remove. Values YAML re-types were silently changed too
    ("007" -> "7", "yes" -> "True").
    """
    import dictionary

    saved = {
        "plain": "DeepSeek",
        "newline": "line1\nline2",
        "numeric": "007",
        "bool": "yes",
        "null": "null",
        "backslash": r"C:\new\path",
        "quote": 'he said "hi"',
        "tab": "a\tb",
        "emoji": "café 🎤",
        "colon": "a: b",
        "dash": "- lead",
        "del_control": "a\x7fb",
        "c1_control": "a\x85b",
    }
    path = tmp_path / "dict.yaml"

    dictionary.save_dictionary(saved, path)

    assert dictionary.load_dictionary(path) == saved

    # The save must leave a file its own reader accepts, so the next entry is
    # added rather than failing or starting from an empty mapping.
    assert dictionary.add_entry("another", "Another", path=path) is True
    reloaded = dictionary.load_dictionary(path)
    assert reloaded["another"] == "Another"
    assert reloaded["newline"] == "line1\nline2"


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

    assert avg_ms < LATENCY_CEILING_MS, f"Post-processor latency exceeded threshold: {avg_ms:.4f} ms"


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


@pytest.mark.parametrize("target", [r"\1", r"path\end", "trail\\", r"C:\new"])
def test_a_contextual_target_is_inserted_literally(target):
    """A backslash in a user-supplied replacement is text, not a template.

    Regression: ``target`` was passed to ``re.sub`` as a replacement *template*,
    where ``\\1`` is a back-reference and ``\\e`` is a bad escape. The first
    echoed the matched text back ("push the push the now"), and the second raised
    ``PatternError`` -- which the ASR caller catches by setting the transcript to
    an empty string, silently throwing the dictation away.
    """
    post_processor.set_contextual_rules([{
        "target": target,
        "spoken": ["walk end"],
        "triggers_before": ["push the"],
    }])
    try:
        assert post_processor.clean_speech_transcription(
            "push the walk end now", skip_slm=True
        ) == f"push the {target} now."
    finally:
        post_processor.set_contextual_rules(None)


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


def test_multi_tier_dictionary_merging(tmp_path, monkeypatch):
    import dictionary

    common_file = tmp_path / "config" / "dictionary.yaml"
    common_file.parent.mkdir(parents=True, exist_ok=True)
    common_file.write_text("""
dictionary:
  kubernetes: Kubernetes
  k8s: Kubernetes
  server one: Server-1
""")

    local_file = tmp_path / "config" / "dictionary.local.yaml"
    local_file.write_text("""
dictionary:
  home dash james m5: home-jamesm5
  server one: MyCustomServer
""")

    monkeypatch.setattr(dictionary, "get_common_dictionary_path", lambda: common_file)
    monkeypatch.setattr(dictionary, "get_local_dictionary_path", lambda create=False: local_file)

    merged_map, _, sources = dictionary.load_merged_dictionary()
    assert merged_map["kubernetes"] == "Kubernetes"
    assert sources["kubernetes"] == "common"
    assert merged_map["home dash james m5"] == "home-jamesm5"
    assert sources["home dash james m5"] == "local"
    # Local overrides common
    assert merged_map["server one"] == "MyCustomServer"
    assert sources["server one"] == "local"

    post_processor.set_custom_dictionary(merged_map)
    res = post_processor.clean_speech_transcription(
        "i deployed kubernetes on home dash james m5 and server one"
    )
    assert res == "I deployed Kubernetes on home-jamesm5 and MyCustomServer."


def test_packaged_install_applies_a_cli_added_dictionary(tmp_path, monkeypatch):
    """A read-only install must apply the per-user dictionary the CLI writes.

    Regression: on Nix / AppImage / PyInstaller there is no repository marker,
    so ``post_processor.load_merged_dictionaries`` fell back to a legacy
    ``<data-dir>/dictionary.yaml`` that ``dictionary.py add`` never writes.
    The result was a dictionary written to a user-editable path but silently
    ignored at runtime. It must now resolve the same
    ``<data-dir>/config/dictionary.local.yaml`` the CLI reports as active.
    """
    import dictionary
    from voice_transcriber import model_download

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(model_download, "find_repo_root", lambda: None)
    monkeypatch.chdir(tmp_path)

    # The CLI's own write path, exactly as a packaged user would invoke it.
    assert dictionary.add_entry("cube ctl", "kubectl", local=True)

    local_file = tmp_path / "vt" / "config" / "dictionary.local.yaml"
    assert local_file.exists()
    assert dictionary.get_local_dictionary_path() == local_file.resolve()

    merged_map, _ = post_processor.load_merged_dictionaries()
    assert merged_map.get("cube ctl") == "kubectl"
    assert "kubectl" in post_processor.clean_speech_transcription(
        "run cube ctl get pods"
    )


def test_a_dictionary_in_the_launch_directory_is_ignored(tmp_path, monkeypatch):
    """CWD is not a dictionary source.

    Regression: the candidate lists began with CWD-relative paths, so a
    ``config/dictionary.yaml`` dropped in the directory the app was launched from
    silently overrode the user's own substitutions - on a checkout it also
    shadowed the file the CLI writes. Every candidate is anchored now: the
    checkout root, or the per-user data dir.
    """
    import dictionary
    from voice_transcriber import model_download

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(model_download, "find_repo_root", lambda: None)

    cwd = tmp_path / "launch-here"
    (cwd / "config").mkdir(parents=True)
    (cwd / "config" / "dictionary.yaml").write_text(
        "dictionary:\n  server one: ATTACKER\n")
    monkeypatch.chdir(cwd)

    # The CWD file is the only one that exists, and it must contribute nothing.
    merged_map, _ = post_processor.load_merged_dictionaries()
    assert "server one" not in merged_map

    # The per-user file the CLI writes is the one that counts, and it wins.
    assert dictionary.add_entry("server one", "Mine", local=True)
    merged_map, _ = post_processor.load_merged_dictionaries()
    assert merged_map.get("server one") == "Mine"


def test_vram_and_gpu_model_repairs():
    """Verify common hardware dictionary repairs ERAM -> VRAM and 130.90 -> one 3090."""
    repo_root = Path(__file__).resolve().parents[2]
    post_processor.load_custom_dictionary_from_file(repo_root / "config" / "dictionary.yaml")

    input_text = (
        "We can research though there's other 3060s with like twelve gigabytes of "
        "ERAM so if we could pair like four of them that would be pretty sizable "
        "right that would be like better than 130.90 maybe"
    )
    out = post_processor.clean_speech_transcription(input_text, skip_slm=True)
    assert "VRAM" in out
    assert "one 3090" in out
    assert "130.90" not in out
    assert "ERAM" not in out


def test_dictionary_write_is_atomic_owner_only_and_leaves_no_temp(tmp_path):
    """A saved dictionary is owner-only, its directory is 0700, and the write is
    a temp+rename that leaves nothing behind.

    Regression: the save was a truncating ``write_text`` under the process umask
    (so world-readable, 0644) with no temp file, so a crash mid-write left the
    user's personal dictionary truncated or gone.
    """
    import stat as stat_mod

    import dictionary

    nested = tmp_path / "vt-data" / "config"
    path = nested / "dictionary.local.yaml"

    dictionary.save_dictionary({"pr": "PR"}, path)

    # File modes are a POSIX concept: CPython reports fixed 0o666/0o444 on Windows.
    if os.name != "nt":
        assert stat_mod.S_IMODE(path.stat().st_mode) == 0o600
        assert stat_mod.S_IMODE(nested.stat().st_mode) == 0o700
    assert list(nested.glob("*.tmp*")) == []


def test_save_contextual_rules_is_atomic_and_owner_only(tmp_path):
    """The contextual-rule writer shares the dictionary writer's guarantees."""
    import stat as stat_mod

    import dictionary

    path = tmp_path / "cfg" / "dictionary.yaml"
    dictionary.save_contextual_rules(
        [{"target": "Gitea", "spoken": ["get tea"]}], path
    )

    if os.name != "nt":
        assert stat_mod.S_IMODE(path.stat().st_mode) == 0o600
    assert list((tmp_path / "cfg").glob("*.tmp*")) == []
    assert dictionary.load_contextual_rules(path)[0]["target"] == "Gitea"


def test_a_corrupt_dictionary_is_backed_up_and_add_still_works(tmp_path):
    """One unparseable personal dictionary must not break every CLI add/remove.

    Regression: ``_load_raw_file`` only caught ``ImportError`` around
    ``yaml.safe_load``, so a ``YAMLError`` escaped and every later ``add`` /
    ``remove`` raised until the file was hand-repaired. The runtime path
    swallowed the same error, so the app looked healthy while the CLI was dead.
    """
    import dictionary

    path = tmp_path / "dictionary.local.yaml"
    corrupt = "dictionary:\n  broken: [unclosed\n"
    path.write_text(corrupt)

    assert dictionary.add_entry("cube ctl", "kubectl", path=path) is True

    backups = list(tmp_path.glob("dictionary.local.yaml.corrupt-*"))
    assert len(backups) == 1, "the unreadable file must be preserved for repair"
    assert backups[0].read_text() == corrupt
    assert dictionary.load_dictionary(path) == {"cube ctl": "kubectl"}


def test_a_corrupt_json_dictionary_is_also_recovered(tmp_path):
    """The JSON reader must recover the same way the YAML reader does.

    (Not round-tripped through ``save_dictionary`` here: the writer always emits
    ``_format_yaml`` regardless of extension, which is a separate pre-existing
    quirk. This pins that a bad JSON file is preserved and ``add`` still works.)
    """
    import dictionary

    path = tmp_path / "dictionary.local.json"
    corrupt = "{not json"
    path.write_text(corrupt)

    assert dictionary.load_dictionary(path) == {}

    backups = list(tmp_path.glob("dictionary.local.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == corrupt
    assert dictionary.add_entry("x", "y", path=path) is True






def test_a_json_path_round_trips_through_save_and_load(tmp_path):
    """The writer emits YAML whatever the extension, so a `.json` dictionary holds
    YAML on disk. The reader must accept that, or every save/read cycle moves the
    user's file aside as corrupt and the dictionary never loads.

    Regression pinned by the reader's YAML fallback; without it this fails and a
    `dictionary.local.json.corrupt-*` appears on the first load.
    """
    import dictionary

    path = tmp_path / "dictionary.local.json"
    assert dictionary.save_dictionary({"postgres": "PostgreSQL"}, path=path) == path
    assert dictionary.load_dictionary(path) == {"postgres": "PostgreSQL"}
    assert list(tmp_path.glob("dictionary.local.json.corrupt-*")) == []
    assert dictionary.add_entry("wi fi", "Wi-Fi", path=path) is True
    assert dictionary.load_dictionary(path)["wi fi"] == "Wi-Fi"
