"""Voice Transcriber — Custom Dictionary Manager & CLI Tool.

Provides programmatic and command-line access to add, remove, list, and test
custom word and technical phrase replacements in ``config/dictionary.yaml``.
Phrases are compiled into a high-speed Trie regex in ``post_processor.py``
running in ~0.005 ms.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Dict, Optional

# Standard search paths for the dictionary file (ordered by preference)
CANDIDATE_PATHS = [
    Path("config/dictionary.yaml"),
    Path("config/dictionary.yml"),
    Path("config/dictionary.json"),
    Path("dictionary.yaml"),
    Path("dictionary.json"),
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


def get_dictionary_path(target_path: Optional[str | Path] = None) -> Path:
    """Return the active dictionary file path, creating parent dirs if needed."""
    if target_path:
        p = Path(target_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    repo_root = find_repo_root()

    # Check candidates relative to current working dir first
    for c in CANDIDATE_PATHS:
        if c.exists():
            return c.resolve()

    # Check relative to repo root
    if repo_root:
        for c in CANDIDATE_PATHS:
            p = repo_root / c
            if p.exists():
                return p.resolve()

    # Default to config/dictionary.yaml under repo root (or cwd)
    base = repo_root if repo_root else Path.cwd()
    dest = base / "config" / "dictionary.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    return dest


def load_dictionary(path: Optional[str | Path] = None) -> Dict[str, str]:
    """Load the dictionary mapping from file."""
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

    if isinstance(data, dict):
        if "dictionary" in data and isinstance(data["dictionary"], dict):
            return {str(k): str(v) for k, v in data["dictionary"].items()}
        return {str(k): str(v) for k, v in data.items() if not isinstance(v, (dict, list))}
    return {}


def save_dictionary(mapping: Dict[str, str], path: Optional[str | Path] = None) -> Path:
    """Save the dictionary mapping to file in clean, human-readable YAML format."""
    dict_path = get_dictionary_path(path)
    dict_path.parent.mkdir(parents=True, exist_ok=True)

    header = (
        "# Voice Transcriber — Custom Word & Technical Phrase Dictionary\n"
        "# Automatically compiled into a ~0.005 ms high-speed Trie regex in post_processor.py.\n"
        "# Matches case-insensitively and replaces with exact target casing/formatting.\n\n"
        "dictionary:\n"
    )

    lines = [header]
    for key in sorted(mapping.keys(), key=lambda s: s.lower()):
        val = mapping[key]
        clean_key = " ".join(str(key).strip().split()).lower()
        # Escape string if it contains colons or special chars
        if any(c in clean_key for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "\\")):
            key_repr = f'"{clean_key}"'
        else:
            key_repr = clean_key

        clean_val = str(val).strip()
        if any(c in clean_val for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "?", "|", "%", "@", "\\")) or clean_val.startswith(("-", ">", "<", "=")):
            val_repr = f'"{clean_val}"'
        else:
            val_repr = clean_val

        lines.append(f"  {key_repr}: {val_repr}\n")

    dict_path.write_text("".join(lines), encoding="utf-8")

    # Update active post_processor dictionary if currently loaded
    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        import post_processor
        post_processor.set_custom_dictionary(mapping)
    except Exception:
        pass

    return dict_path


def add_entry(phrase: str, replacement: str, path: Optional[str | Path] = None) -> bool:
    """Add or update an entry in the custom dictionary."""
    clean_key = " ".join(str(phrase).strip().split()).lower()
    clean_val = str(replacement).strip()
    if not clean_key or not clean_val:
        raise ValueError("Phrase and replacement cannot be empty.")

    current = load_dictionary(path)
    is_new = clean_key not in current
    current[clean_key] = clean_val
    save_dictionary(current, path)
    return is_new


def remove_entry(phrase: str, path: Optional[str | Path] = None) -> bool:
    """Remove an entry from the custom dictionary."""
    clean_key = " ".join(str(phrase).strip().split()).lower()
    current = load_dictionary(path)
    if clean_key in current:
        del current[clean_key]
        save_dictionary(current, path)
        return True
    return False


def test_phrase(text: str) -> str:
    """Run text through post_processor's dictionary replacer and return the result."""
    try:
        src_dir = Path(__file__).resolve().parent
        if str(src_dir) not in sys.path:
            sys.path.insert(0, str(src_dir))
        import post_processor
        # Ensure latest dictionary is active
        post_processor.set_custom_dictionary(load_dictionary())
        return post_processor.clean_speech_transcription(text)
    except Exception as e:
        return f"(Error running post_processor: {e})"


def main(args=None):
    """CLI entry point for dictionary management."""
    parser = argparse.ArgumentParser(
        prog="python -m src.dictionary",
        description="Manage the Voice Transcriber custom word & phrase dictionary.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # add
    p_add = subparsers.add_parser("add", help="Add or update a dictionary entry")
    p_add.add_argument("phrase", help="Spoken phrase (e.g. 'deep seq', 'cube ctl', 'nixos')")
    p_add.add_argument("replacement", help="Desired output (e.g. 'Deepseek', 'kubectl', 'NixOS')")
    p_add.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # remove
    p_rm = subparsers.add_parser("remove", help="Remove an entry from the dictionary")
    p_rm.add_argument("phrase", help="Spoken phrase to remove")
    p_rm.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # list
    p_ls = subparsers.add_parser("list", help="List all dictionary entries")
    p_ls.add_argument("filter", nargs="?", default=None, help="Optional text filter")
    p_ls.add_argument("--file", "-f", default=None, help="Custom dictionary file path")

    # test
    p_test = subparsers.add_parser("test", help="Test transcription text against the dictionary")
    p_test.add_argument("text", help="Sample text to test")

    # path
    subparsers.add_parser("path", help="Show the active dictionary file path")

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        sys.exit(1)

    if parsed.command == "path":
        print(get_dictionary_path())
        return

    if parsed.command == "add":
        is_new = add_entry(parsed.phrase, parsed.replacement, parsed.file)
        action = "Added" if is_new else "Updated"
        print(f"✓ {action} dictionary entry: '{parsed.phrase}' -> '{parsed.replacement}'")
        dict_path = get_dictionary_path(parsed.file)
        print(f"  Saved in {dict_path}")

    elif parsed.command == "remove":
        removed = remove_entry(parsed.phrase, parsed.file)
        if removed:
            print(f"✓ Removed dictionary entry: '{parsed.phrase}'")
        else:
            print(f"✕ Phrase not found in dictionary: '{parsed.phrase}'")
            sys.exit(1)

    elif parsed.command == "list":
        entries = load_dictionary(parsed.file)
        if not entries:
            print("Dictionary is empty.")
            return

        filter_str = parsed.filter.lower() if parsed.filter else None
        matches = {
            k: v for k, v in entries.items()
            if not filter_str or filter_str in k.lower() or filter_str in v.lower()
        }

        print(f"Custom Dictionary ({len(matches)}/{len(entries)} entries):")
        print("-" * 50)
        for k in sorted(matches.keys(), key=lambda s: s.lower()):
            print(f"  {k:<24} -> {matches[k]}")

    elif parsed.command == "test":
        result = test_phrase(parsed.text)
        print("Input: ")
        print(f"  {parsed.text}")
        print("Output (with dictionary & post-processor):")
        print(f"  {result}")


if __name__ == "__main__":
    main()
