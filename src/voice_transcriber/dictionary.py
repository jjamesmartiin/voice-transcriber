"""Voice Transcriber — Custom Dictionary Manager & CLI Tool.

Provides programmatic and command-line access to add, remove, list, and test
both exact word/phrase mappings and context-aware disambiguation rules across
two dictionary layers:
1. Common Dictionary (`config/dictionary.yaml`) — tracked in git, shared tech terms.
2. Local / Personal Dictionary (`config/dictionary.local.yaml`) — gitignored, private terms.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import console_text

COMMON_CANDIDATE_PATHS = [
    Path("config/dictionary.yaml"),
    Path("config/dictionary.yml"),
    Path("config/dictionary.json"),
    Path("dictionary.yaml"),
    Path("dictionary.json"),
]

LOCAL_CANDIDATE_PATHS = [
    Path("config/dictionary.local.yaml"),
    Path("config/dictionary.local.yml"),
    Path("config/dictionary.local.json"),
    Path("dictionary.local.yaml"),
    Path("dictionary.local.json"),
    Path("config/dictionary.user.yaml"),
    Path("config/dictionary.user.yml"),
    Path("config/dictionary.user.json"),
    Path("dictionary.user.yaml"),
    Path("dictionary.user.json"),
]


def find_repo_root() -> Optional[Path]:
    """Find the root repository directory if running from a git checkout."""
    d = Path(__file__).resolve().parent
    for _ in range(8):
        if (d / ".git").is_dir():
            return d
        parent = d.parent
        if parent == d:
            break
        d = parent
    return None


def get_common_dictionary_path() -> Path:
    """Return the path to the tracked common dictionary file."""
    repo_root = find_repo_root()
    for c in COMMON_CANDIDATE_PATHS:
        if c.exists():
            return c.resolve()

    if repo_root:
        for c in COMMON_CANDIDATE_PATHS:
            p = repo_root / c
            if p.exists():
                return p.resolve()

    base = repo_root if repo_root else Path.cwd()
    dest = base / "config" / "dictionary.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def get_local_dictionary_path(create: bool = False) -> Path:
    """Return the path to the gitignored personal/local dictionary file."""
    repo_root = find_repo_root()
    for c in LOCAL_CANDIDATE_PATHS:
        if c.exists():
            return c.resolve()

    if repo_root:
        for c in LOCAL_CANDIDATE_PATHS:
            p = repo_root / c
            if p.exists():
                return p.resolve()

    base = repo_root if repo_root else Path.cwd()
    dest = base / "config" / "dictionary.local.yaml"
    if create:
        dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def get_dictionary_path(target_path: Optional[str | Path] = None, local: bool = False) -> Path:
    """Return the active dictionary file path, creating parent dirs if needed."""
    if target_path:
        p = Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    if local:
        return get_local_dictionary_path(create=True)

    # If local dictionary exists, prefer it when no specific path was given,
    # otherwise default to common.
    local_p = get_local_dictionary_path()
    if local_p.exists():
        return local_p

    return get_common_dictionary_path()


def _load_raw_file(path: Optional[str | Path] = None) -> Dict[str, Any]:
    """Load the raw dictionary file as a parsed dictionary."""
    dict_path = get_dictionary_path(path)
    if not dict_path.exists():
        return {}

    content = dict_path.read_text(encoding="utf-8")
    if not content.strip():
        return {}

    data = None
    if dict_path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(content)
        except ImportError:
            pass

    if data is None:
        try:
            data = json.loads(content)
        except Exception:
            return {}

    return data if isinstance(data, dict) else {}


def load_dictionary(path: Optional[str | Path] = None) -> Dict[str, str]:
    """Load the flat dictionary mappings from a specific file."""
    raw = _load_raw_file(path)
    if "dictionary" in raw and isinstance(raw["dictionary"], dict):
        return {str(k): str(v) for k, v in raw["dictionary"].items()}
    return {str(k): str(v) for k, v in raw.items() if k != "contextual_rules" and isinstance(v, (str, int, float))}


def load_contextual_rules(path: Optional[str | Path] = None) -> List[Dict[str, Any]]:
    """Load the list of contextual disambiguation rules from a specific file."""
    raw = _load_raw_file(path)
    rules = raw.get("contextual_rules")
    if isinstance(rules, list):
        return [r for r in rules if isinstance(r, dict)]
    return []


def load_merged_dictionary() -> Tuple[Dict[str, str], List[Dict[str, Any]], Dict[str, str]]:
    """
    Load and merge common and local dictionaries.
    Returns (merged_mapping, merged_rules, key_source_map).
    """
    common_p = get_common_dictionary_path()
    local_p = get_local_dictionary_path()

    merged_map: Dict[str, str] = {}
    key_sources: Dict[str, str] = {}
    merged_rules: List[Dict[str, Any]] = []

    if common_p.exists():
        c_map = load_dictionary(common_p)
        merged_map.update(c_map)
        for k in c_map:
            key_sources[k] = "common"
        merged_rules.extend(load_contextual_rules(common_p))

    if local_p.exists():
        l_map = load_dictionary(local_p)
        merged_map.update(l_map)
        for k in l_map:
            key_sources[k] = "local"
        l_rules = load_contextual_rules(local_p)
        if l_rules:
            existing_targets = {r.get("target", "").lower(): i for i, r in enumerate(merged_rules)}
            for r in l_rules:
                t = r.get("target", "").lower()
                if t in existing_targets:
                    merged_rules[existing_targets[t]] = r
                else:
                    merged_rules.append(r)

    return merged_map, merged_rules, key_sources


def _format_yaml(mapping: Dict[str, str], contextual_rules: List[Dict[str, Any]], is_local: bool = False) -> str:
    """Format dictionary and contextual rules into clean, human-readable YAML."""
    header_title = "Personal / Local Custom Dictionary (Gitignored)" if is_local else "Custom Word & Technical Phrase Dictionary"
    lines = [
        f"# Voice Transcriber — {header_title}\n"
        "# Automatically compiled into high-speed regex engines in post_processor.py.\n"
        "# 1. 'dictionary': exact case-insensitive Trie replacement (~0.005 ms)\n"
        "# 2. 'contextual_rules': surrounding trigger/guard disambiguation rules\n\n"
        "dictionary:\n"
    ]

    for key in sorted(mapping.keys(), key=lambda s: s.lower()):
        val = mapping[key]
        clean_key = " ".join(str(key).strip().split()).lower()
        if any(c in clean_key for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "\\", ".")):
            key_repr = f'"{clean_key}"'
        else:
            key_repr = clean_key

        clean_val = str(val).strip()
        if any(c in clean_val for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "%", "@", "\\")) or clean_val.startswith(("-", ">", "<", "=")):
            val_repr = f'"{clean_val}"'
        else:
            val_repr = clean_val

        lines.append(f"  {key_repr}: {val_repr}\n")

    if contextual_rules:
        lines.append("\n# ---------------------------------------------------------------------------\n")
        lines.append("# Contextual Disambiguation Rules\n")
        lines.append("# ---------------------------------------------------------------------------\n")
        lines.append("contextual_rules:\n")
        for rule in contextual_rules:
            target = rule.get("target", "")
            lines.append(f"  - target: {target}\n")

            spoken = rule.get("spoken", [])
            if spoken:
                lines.append("    spoken:\n")
                for s in spoken:
                    lines.append(f'      - "{s}"\n')

            before = rule.get("triggers_before") or rule.get("before", [])
            if before:
                lines.append("    triggers_before:\n")
                for b in before:
                    lines.append(f'      - "{b}"\n')

            after = rule.get("triggers_after") or rule.get("after", [])
            if after:
                lines.append("    triggers_after:\n")
                for a in after:
                    lines.append(f'      - "{a}"\n')

            guards = rule.get("guards") or rule.get("protect", [])
            if guards:
                lines.append("    guards:\n")
                for g in guards:
                    lines.append(f'      - "{g}"\n')
            lines.append("\n")

    return "".join(lines)


def save_dictionary(mapping: Dict[str, str], path: Optional[str | Path] = None, local: Optional[bool] = None) -> Path:
    """Save the dictionary mapping to file, preserving any contextual rules."""
    if path:
        dict_path = Path(path)
    elif local is True:
        dict_path = get_local_dictionary_path(create=True)
    elif local is False:
        dict_path = get_common_dictionary_path()
    else:
        dict_path = get_dictionary_path()

    dict_path.parent.mkdir(parents=True, exist_ok=True)
    rules = load_contextual_rules(dict_path)
    is_local = "local" in dict_path.name or "user" in dict_path.name
    yaml_text = _format_yaml(mapping, rules, is_local=is_local)
    dict_path.write_text(yaml_text, encoding="utf-8")

    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        import post_processor
        post_processor.load_merged_dictionaries()
    except Exception:
        pass

    return dict_path


def save_contextual_rules(rules: List[Dict[str, Any]], path: Optional[str | Path] = None, local: Optional[bool] = None) -> Path:
    """Save contextual rules to file, preserving flat dictionary mappings."""
    if path:
        dict_path = Path(path)
    elif local is True:
        dict_path = get_local_dictionary_path(create=True)
    elif local is False:
        dict_path = get_common_dictionary_path()
    else:
        dict_path = get_dictionary_path()

    dict_path.parent.mkdir(parents=True, exist_ok=True)
    mapping = load_dictionary(dict_path)
    is_local = "local" in dict_path.name or "user" in dict_path.name
    yaml_text = _format_yaml(mapping, rules, is_local=is_local)
    dict_path.write_text(yaml_text, encoding="utf-8")

    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        import post_processor
        post_processor.load_merged_dictionaries()
    except Exception:
        pass

    return dict_path


def add_entry(phrase: str, replacement: str, path: Optional[str | Path] = None, local: Optional[bool] = None) -> bool:
    """Add or update an entry in the custom dictionary."""
    clean_key = " ".join(str(phrase).strip().split()).lower()
    clean_val = str(replacement).strip()
    if not clean_key or not clean_val:
        raise ValueError("Phrase and replacement cannot be empty.")

    target_path = Path(path) if path else (get_local_dictionary_path(create=True) if local is not False else get_common_dictionary_path())
    current = load_dictionary(target_path)
    is_new = clean_key not in current
    current[clean_key] = clean_val
    save_dictionary(current, target_path)
    return is_new


def remove_entry(phrase: str, path: Optional[str | Path] = None, local: Optional[bool] = None) -> bool:
    """Remove an entry from the custom dictionary."""
    clean_key = " ".join(str(phrase).strip().split()).lower()
    
    if path:
        target_path = Path(path)
        current = load_dictionary(target_path)
        if clean_key in current:
            del current[clean_key]
            save_dictionary(current, target_path)
            return True
        return False

    # If specific tier chosen:
    if local is True:
        target_path = get_local_dictionary_path()
        current = load_dictionary(target_path)
        if clean_key in current:
            del current[clean_key]
            save_dictionary(current, target_path)
            return True
        return False
    elif local is False:
        target_path = get_common_dictionary_path()
        current = load_dictionary(target_path)
        if clean_key in current:
            del current[clean_key]
            save_dictionary(current, target_path)
            return True
        return False

    # Check local first, then common
    local_p = get_local_dictionary_path()
    if local_p.exists():
        current = load_dictionary(local_p)
        if clean_key in current:
            del current[clean_key]
            save_dictionary(current, local_p)
            return True

    common_p = get_common_dictionary_path()
    if common_p.exists():
        current = load_dictionary(common_p)
        if clean_key in current:
            del current[clean_key]
            save_dictionary(current, common_p)
            return True

    return False


def add_contextual_rule(
    target: str,
    spoken: List[str] | str,
    triggers_before: Optional[List[str] | str] = None,
    triggers_after: Optional[List[str] | str] = None,
    guards: Optional[List[str] | str] = None,
    path: Optional[str | Path] = None,
    local: Optional[bool] = None,
) -> bool:
    """Add or update a contextual disambiguation rule."""
    def _to_list(v):
        if not v:
            return []
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return [str(x).strip() for x in v if str(x).strip()]

    rule_dict = {
        "target": str(target).strip(),
        "spoken": _to_list(spoken),
    }
    before = _to_list(triggers_before)
    if before:
        rule_dict["triggers_before"] = before
    after = _to_list(triggers_after)
    if after:
        rule_dict["triggers_after"] = after
    g = _to_list(guards)
    if g:
        rule_dict["guards"] = g

    target_path = Path(path) if path else (get_local_dictionary_path(create=True) if local is not False else get_common_dictionary_path())
    current_rules = load_contextual_rules(target_path)
    is_new = True
    for i, r in enumerate(current_rules):
        if r.get("target", "").lower() == str(target).strip().lower():
            current_rules[i] = rule_dict
            is_new = False
            break

    if is_new:
        current_rules.append(rule_dict)

    save_contextual_rules(current_rules, target_path)
    return is_new


def remove_contextual_rule(target: str, path: Optional[str | Path] = None, local: Optional[bool] = None) -> bool:
    """Remove a contextual rule by its target name."""
    clean_target = str(target).strip().lower()

    if path:
        target_path = Path(path)
        current_rules = load_contextual_rules(target_path)
        new_rules = [r for r in current_rules if r.get("target", "").lower() != clean_target]
        if len(new_rules) < len(current_rules):
            save_contextual_rules(new_rules, target_path)
            return True
        return False

    if local is True:
        target_path = get_local_dictionary_path()
        current_rules = load_contextual_rules(target_path)
        new_rules = [r for r in current_rules if r.get("target", "").lower() != clean_target]
        if len(new_rules) < len(current_rules):
            save_contextual_rules(new_rules, target_path)
            return True
        return False
    elif local is False:
        target_path = get_common_dictionary_path()
        current_rules = load_contextual_rules(target_path)
        new_rules = [r for r in current_rules if r.get("target", "").lower() != clean_target]
        if len(new_rules) < len(current_rules):
            save_contextual_rules(new_rules, target_path)
            return True
        return False

    local_p = get_local_dictionary_path()
    if local_p.exists():
        current_rules = load_contextual_rules(local_p)
        new_rules = [r for r in current_rules if r.get("target", "").lower() != clean_target]
        if len(new_rules) < len(current_rules):
            save_contextual_rules(new_rules, local_p)
            return True

    common_p = get_common_dictionary_path()
    if common_p.exists():
        current_rules = load_contextual_rules(common_p)
        new_rules = [r for r in current_rules if r.get("target", "").lower() != clean_target]
        if len(new_rules) < len(current_rules):
            save_contextual_rules(new_rules, common_p)
            return True

    return False


def test_phrase(text: str) -> str:
    """Run text through post_processor's dictionary replacer and return the result."""
    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        import post_processor
        post_processor.load_merged_dictionaries()
        return post_processor.clean_speech_transcription(text)
    except Exception as e:
        return f"(Error running post_processor: {e})"


def main(args=None):
    """CLI entry point for dictionary management."""
    console_text.harden_standard_streams()
    parser = argparse.ArgumentParser(
        prog="python -m src.dictionary",
        description="Manage Voice Transcriber custom word & phrase dictionaries (common and local).",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # add
    p_add = subparsers.add_parser("add", help="Add or update an exact dictionary entry")
    p_add.add_argument("phrase", help="Spoken phrase (e.g. 'deep seq', 'cube ctl', 'nixos')")
    p_add.add_argument("replacement", help="Desired output (e.g. 'Deepseek', 'kubectl', 'NixOS')")
    p_add.add_argument("--local", "-l", action="store_true", default=True, help="Save to personal/local dictionary (default, gitignored)")
    p_add.add_argument("--common", "-c", action="store_true", default=False, help="Save to common dictionary (tracked in git)")
    p_add.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # remove
    p_rm = subparsers.add_parser("remove", help="Remove an entry from the dictionary")
    p_rm.add_argument("phrase", help="Spoken phrase to remove")
    p_rm.add_argument("--local", "-l", action="store_true", default=False, help="Remove from local dictionary only")
    p_rm.add_argument("--common", "-c", action="store_true", default=False, help="Remove from common dictionary only")
    p_rm.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # list
    p_ls = subparsers.add_parser("list", help="List dictionary entries")
    p_ls.add_argument("filter", nargs="?", default=None, help="Optional text filter")
    p_ls.add_argument("--local", "-l", action="store_true", default=False, help="List only local/personal entries")
    p_ls.add_argument("--common", "-c", action="store_true", default=False, help="List only common entries")
    p_ls.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # add-contextual
    p_ac = subparsers.add_parser("add-contextual", help="Add or update a contextual disambiguation rule")
    p_ac.add_argument("--target", "-t", required=True, help="Target proper noun (e.g. 'Gitea')")
    p_ac.add_argument("--spoken", "-s", required=True, help="Comma-separated spoken phrases (e.g. 'get tea, git tea')")
    p_ac.add_argument("--before", "-b", default="", help="Comma-separated preceding trigger phrases (e.g. 'push to, clone from')")
    p_ac.add_argument("--after", "-a", default="", help="Comma-separated following trigger phrases (e.g. 'server, repo')")
    p_ac.add_argument("--guards", "-g", default="", help="Comma-separated everyday guard phrases that cancel replacement (e.g. 'cup of, drink, like to')")
    p_ac.add_argument("--local", "-l", action="store_true", default=True, help="Save to personal/local dictionary (default)")
    p_ac.add_argument("--common", "-c", action="store_true", default=False, help="Save to common dictionary")
    p_ac.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # remove-contextual
    p_rc = subparsers.add_parser("remove-contextual", help="Remove a contextual rule by target")
    p_rc.add_argument("target", help="Target name to remove")
    p_rc.add_argument("--local", "-l", action="store_true", default=False, help="Remove from local dictionary only")
    p_rc.add_argument("--common", "-c", action="store_true", default=False, help="Remove from common dictionary only")
    p_rc.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # list-contextual
    p_lc = subparsers.add_parser("list-contextual", help="List all contextual disambiguation rules")
    p_lc.add_argument("--local", "-l", action="store_true", default=False, help="List only local rules")
    p_lc.add_argument("--common", "-c", action="store_true", default=False, help="List only common rules")
    p_lc.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # test
    p_test = subparsers.add_parser("test", help="Test transcription text against the merged dictionary & contextual rules")
    p_test.add_argument("text", help="Sample text to test")

    # path
    subparsers.add_parser("path", help="Show the active dictionary file paths (common and local)")

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        sys.exit(1)

    if parsed.command == "path":
        common_p = get_common_dictionary_path()
        local_p = get_local_dictionary_path()
        print("Voice Transcriber Dictionary Paths:")
        print(f"  • Common Dictionary (tracked in git) : {common_p} {'[found]' if common_p.exists() else '[not found]'}")
        print(f"  • Local Dictionary  (gitignored)     : {local_p} {'[found]' if local_p.exists() else '[not created]'}")
        return

    if parsed.command == "add":
        use_local = not parsed.common if not parsed.file else None
        is_new = add_entry(parsed.phrase, parsed.replacement, path=parsed.file, local=use_local)
        action = "Added" if is_new else "Updated"
        tier = "common" if parsed.common else ("custom file" if parsed.file else "local (gitignored)")
        print(f"✓ {action} dictionary entry in {tier}: '{parsed.phrase}' -> '{parsed.replacement}'")

    elif parsed.command == "remove":
        use_local = True if parsed.local else (False if parsed.common else None)
        removed = remove_entry(parsed.phrase, path=parsed.file, local=use_local)
        if removed:
            print(f"✓ Removed dictionary entry: '{parsed.phrase}'")
        else:
            print(f"✕ Phrase not found in dictionary: '{parsed.phrase}'")
            sys.exit(1)

    elif parsed.command == "list":
        if parsed.file:
            entries = load_dictionary(parsed.file)
            sources = {k: "custom" for k in entries}
        elif parsed.local:
            entries = load_dictionary(get_local_dictionary_path())
            sources = {k: "local" for k in entries}
        elif parsed.common:
            entries = load_dictionary(get_common_dictionary_path())
            sources = {k: "common" for k in entries}
        else:
            entries, _, sources = load_merged_dictionary()

        if not entries:
            print("Dictionary is empty.")
            return

        filter_str = parsed.filter.lower() if parsed.filter else None
        matches = {
            k: v for k, v in entries.items()
            if not filter_str or filter_str in k.lower() or filter_str in v.lower()
        }

        print(f"Custom Dictionary ({len(matches)}/{len(entries)} entries):")
        print("-" * 65)
        for k in sorted(matches.keys(), key=lambda s: s.lower()):
            src_tag = f"[{sources.get(k, 'dict')}]"
            print(f"  {k:<26} -> {matches[k]:<20} {src_tag}")

    elif parsed.command == "add-contextual":
        use_local = not parsed.common if not parsed.file else None
        is_new = add_contextual_rule(
            target=parsed.target,
            spoken=parsed.spoken,
            triggers_before=parsed.before,
            triggers_after=parsed.after,
            guards=parsed.guards,
            path=parsed.file,
            local=use_local,
        )
        action = "Added" if is_new else "Updated"
        tier = "common" if parsed.common else ("custom file" if parsed.file else "local (gitignored)")
        print(f"✓ {action} contextual rule for '{parsed.target}' in {tier}")

    elif parsed.command == "remove-contextual":
        use_local = True if parsed.local else (False if parsed.common else None)
        removed = remove_contextual_rule(parsed.target, path=parsed.file, local=use_local)
        if removed:
            print(f"✓ Removed contextual rule for '{parsed.target}'")
        else:
            print(f"✕ Rule not found for target '{parsed.target}'")
            sys.exit(1)

    elif parsed.command == "list-contextual":
        if parsed.file:
            rules = load_contextual_rules(parsed.file)
        elif parsed.local:
            rules = load_contextual_rules(get_local_dictionary_path())
        elif parsed.common:
            rules = load_contextual_rules(get_common_dictionary_path())
        else:
            _, rules, _ = load_merged_dictionary()

        if not rules:
            print("No contextual rules configured.")
            return

        print(f"Contextual Disambiguation Rules ({len(rules)} rules):")
        print("=" * 60)
        for r in rules:
            target = r.get("target", "")
            spoken = ", ".join(r.get("spoken", []))
            before = ", ".join(r.get("triggers_before", []))
            after = ", ".join(r.get("triggers_after", []))
            guards = ", ".join(r.get("guards", []))
            print(f"• Target: {target}")
            print(f"  Spoken Collision : [{spoken}]")
            if before:
                print(f"  Triggers Before  : [{before}]")
            if after:
                print(f"  Triggers After   : [{after}]")
            if guards:
                print(f"  Everyday Guards  : [{guards}]")
            print()

    elif parsed.command == "test":
        result = test_phrase(parsed.text)
        print("Input: ")
        print(f"  {parsed.text}")
        print("Output (with dictionary & post-processor):")
        print(f"  {result}")


if __name__ == "__main__":
    main()
