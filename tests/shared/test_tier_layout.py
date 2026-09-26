#!/usr/bin/env python3
"""Layout guard: every test file must live in a tier directory.

Tiers: shared (all platforms), linux, windows, wsl (platform-specific),
e2e (model/audio, local only). This prevents the file-list drift that let a
cross-platform-safe test run on only one OS in CI.
"""
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parents[1]
TIERS = {"shared", "linux", "windows", "wsl", "e2e"}


def test_no_stray_test_files_at_tests_root():
    strays = sorted(p.name for p in TESTS_DIR.glob("test_*.py"))
    assert strays == [], (
        "Move these into a tier dir "
        f"({', '.join(sorted(TIERS))}): {strays}"
    )


def test_tier_dirs_exist_with_init():
    for tier in sorted(TIERS):
        tier_dir = TESTS_DIR / tier
        assert tier_dir.is_dir(), f"missing tier dir: tests/{tier}"
        assert (tier_dir / "__init__.py").exists(), f"missing tests/{tier}/__init__.py"
