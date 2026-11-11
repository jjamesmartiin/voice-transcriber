"""Suite hygiene: the conventions that make this suite trustworthy.

Each rule here was earned by a real finding, not invented:

* an unnamed skip is indistinguishable from a broken test that someone silenced;
* a test function whose name is reused in the same scope is **never collected** —
  the second definition wins in the namespace, so the first is dead code that
  looks like coverage. (Found in `test_audio_state.py`, where the same name
  appeared in two *classes*; both ran, but the failure output could not say
  which. Different classes are fine; the same scope is not.)
* a `test_*.py` outside a tier directory is collected by **no** tier, so it is
  either dead weight or a script pretending to be a test (see
  `tests/benchmark_synthetic_e2e.py`, which is deliberately a script).

These are structural checks on the test tree itself. They are cheap (an AST pass
over ~96 files), they run on every OS in the shared tier, and they cannot fail
because of the host.
"""

import ast
import pathlib

TESTS_DIR = pathlib.Path(__file__).resolve().parent.parent
TIERS = {"shared", "linux", "windows", "wsl", "macos", "e2e"}


def _modules():
    return sorted(p for p in TESTS_DIR.rglob("*.py") if p.name != "__init__.py")


def _skip_markers(tree):
    """Every `skip` / `skipif` / `xfail` call in a module, however it is spelled."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name.endswith(("mark.skip", "mark.skipif", "mark.xfail")):
            yield node


def _has_a_reason(call, name):
    """Whether a skip marker says who it is for.

    ``pytest.skip("POSIX only")`` carries its reason positionally; a
    ``skipif``/``xfail`` marker has a *condition* in that position, so it must
    name its reason by keyword. Accepting the position for those made this check
    pass on ``@pytest.mark.skipif(True)`` — the empty-skip case it exists to
    catch.
    """
    if "reason" in {kw.arg for kw in call.keywords}:
        return True
    return name.endswith("pytest.skip") and bool(call.args) and (
        not isinstance(call.args[0], ast.Constant) or str(call.args[0].value).strip()
    )


def test_every_skip_and_xfail_says_who_it_is_for():
    """`reason=` is what tells the next person whether a skip is a gap or a fact.

    A skip is the one way a test can be green without testing anything, and the
    WSL PowerShell bridge is the standing example: CI has no `powershell.exe`, so
    those tests are pinned structurally and say so.
    """
    problems = []
    for path in _modules():
        tree = ast.parse(path.read_text())
        for call in _skip_markers(tree):
            name = ast.unparse(call.func)
            if not _has_a_reason(call, name):
                problems.append(f"{path.relative_to(TESTS_DIR)}:{call.lineno}: "
                                f"{ast.unparse(call)[:60]}")
    assert not problems, "unnamed skip/xfail markers:\n  " + "\n  ".join(problems)


def test_no_test_function_shadows_another_in_the_same_scope():
    """A redefined test name is never collected, so it is coverage that does not run."""
    problems = []
    for path in _modules():
        tree = ast.parse(path.read_text())

        def check(body, scope, path=path):
            seen = {}
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name.startswith("test"):
                        if node.name in seen:
                            problems.append(
                                f"{path.relative_to(TESTS_DIR)}:{node.lineno}: "
                                f"{scope}{node.name} (first at line {seen[node.name]})")
                        seen[node.name] = node.lineno
            return seen

        check(tree.body, "")
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                check(node.body, f"{node.name}.")

    assert not problems, "shadowed test definitions:\n  " + "\n  ".join(problems)


def test_every_test_file_belongs_to_a_tier():
    """No tier collects `tests/*.py`, so a test file there silently never runs."""
    stray = [
        str(p.relative_to(TESTS_DIR))
        for p in TESTS_DIR.glob("test_*.py")
        if p.is_file()
    ]
    assert not stray, (
        "these test files live outside every tier and are collected by nothing: "
        + ", ".join(stray)
    )


def test_the_tier_directories_are_the_ones_the_runner_uses():
    """`tools/vt_dev.py` names the tiers; a new one has to be added there too."""
    import sys

    sys.path.insert(0, str(TESTS_DIR.parent / "tools"))
    import vt_dev

    declared = {t for t in vt_dev._VALID_TIERS} & TIERS
    assert declared == TIERS, (
        f"tiers on disk: {sorted(TIERS)}; runner knows: {sorted(declared)}"
    )
