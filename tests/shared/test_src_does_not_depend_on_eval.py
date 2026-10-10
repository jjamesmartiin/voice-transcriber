#!/usr/bin/env python3
"""Offline-first guard (X1): the product must not depend on ``eval/``.

``eval/`` is a development-only harness: it needs the model weights and the
audio fixtures, and it is not shipped. A stray ``from eval.score import ...`` in
``src/`` would ship a product that cannot import on a clean machine, and nothing
enforced the rule before this test.

This is a *source-text / import-graph* check, not a hand-maintained list of
modules. It walks every ``.py`` under ``src/``, parses it with :mod:`ast`, and
rejects any reference to the top-level ``eval`` package in any form:

* ``import eval`` / ``import eval.foo`` / ``from eval import ...``;
* a dynamic import whose *literal* argument is ``eval`` or a dotted submodule
  (``importlib.import_module("eval.score")``, ``__import__("eval")``,
  ``importlib.util.find_spec("eval")``);
* any runtime string literal that is a path to the ``eval`` directory
  (``"eval"``, ``"../eval/x.py"``, ``os.path.join(root, "eval")``).

Docstrings are excluded on purpose: several modules explain in prose where their
numbers came from and mention ``eval/``; prose is not a dependency. A computed
string (``importlib.import_module("ev" + "al")``) is not statically decidable and
is out of scope for this check.

The checker is exercised against injected violations in
:class:`TestTheGuardItself`, so a green run is evidence the guard works rather
than evidence the walk found nothing.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

#: Split a string into path components on either separator.
_PATH_SPLIT = re.compile(r"[\\/]")


def _path_references_eval(value: str) -> bool:
    """True when ``value`` is (or contains) a path component named ``eval``."""
    return "eval" in (part for part in _PATH_SPLIT.split(value) if part)


def _is_eval_module(value: str) -> bool:
    """True for the dotted module name ``eval`` or a submodule of it."""
    return value == "eval" or value.startswith("eval.")


def _docstring_constants(tree: ast.AST) -> set[int]:
    """The ``id()``s of every docstring ``Constant`` in ``tree``.

    Docstrings are string literals too, and prose about ``eval/`` is not a
    dependency - ``src/voice_transcriber/transcribe2.py`` mentions it in its
    module docstring. Identified per definition, the same way the compiler does.
    """
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            found.add(id(first.value))
    return found


def find_eval_references(source: str, filename: str = "<string>") -> list[str]:
    """Return one message per ``eval/`` reference in ``source`` (``[]`` is clean)."""
    tree = ast.parse(source, filename=filename)
    docstrings = _docstring_constants(tree)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_eval_module(alias.name):
                    violations.append(
                        f"{filename}:{node.lineno}: import {alias.name}"
                    )
        elif isinstance(node, ast.ImportFrom):
            # ``level > 0`` is a relative import; ``from . import eval`` names a
            # submodule of *our* package, not the top-level ``eval``.
            if node.level == 0 and node.module and _is_eval_module(node.module):
                violations.append(
                    f"{filename}:{node.lineno}: from {node.module} import ..."
                )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            if _path_references_eval(node.value) or _is_eval_module(node.value):
                violations.append(
                    f"{filename}:{node.lineno}: references eval: {node.value!r}"
                )

    return violations


def _source_files() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


def test_src_has_no_eval_references():
    """The real source tree must be clean."""
    violations: list[str] = []
    for path in _source_files():
        relative = path.relative_to(REPO_ROOT)
        violations.extend(
            find_eval_references(path.read_text(encoding="utf-8"), str(relative))
        )
    assert violations == [], "src/ must not depend on eval/:\n" + "\n".join(violations)


def test_the_walk_sees_the_real_source_tree():
    """A guard that walked an empty tree would pass and prove nothing."""
    files = _source_files()
    assert len(files) > 20, files
    assert any(path.name == "main.py" for path in files)


class TestTheGuardItself:
    """Each form the rule forbids, plus the false positives it must avoid."""

    def test_a_plain_import_is_flagged(self):
        assert find_eval_references("import eval\n")

    def test_a_submodule_import_is_flagged(self):
        assert find_eval_references("import eval.score\n")

    def test_a_from_import_is_flagged(self):
        assert find_eval_references("from eval.score import score\n")

    def test_a_dynamic_import_by_name_is_flagged(self):
        source = 'import importlib\nimportlib.import_module("eval.score")\n'
        assert find_eval_references(source)

    def test_a_builtin_import_is_flagged(self):
        assert find_eval_references('__import__("eval")\n')

    def test_a_runtime_path_reference_is_flagged(self):
        assert find_eval_references('SPEC = "eval/score.py"\n')
        assert find_eval_references('ROOT = "../eval"\n')
        assert find_eval_references("import os\nos.path.join(root, 'eval')\n")

    def test_a_docstring_mention_is_not_a_dependency(self):
        source = '"""Measured against the 154-clip eval in `eval/`."""\nimport math\n'
        assert find_eval_references(source) == []

    def test_a_relative_submodule_named_eval_is_not_the_package(self):
        assert find_eval_references("from . import eval\n") == []

    def test_similar_names_are_not_false_positives(self):
        source = (
            "import evaluator\n"
            "from evaluation import metric\n"
            'name = "retrieval"\n'
            'other = "evaluate"\n'
        )
        assert find_eval_references(source) == []
