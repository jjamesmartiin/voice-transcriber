"""The offline-first rule: the text cleaner must not drag in a network client.

The SLM/vLLM client was split out of ``post_processor.py`` (audit §3) so that
importing the cleaner no longer pulls an HTTP client into an otherwise
offline-first package. Both halves are checked in a subprocess, because by the
time any other test has run these modules may already be imported.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"


def _run_in_fresh_interpreter(code: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {str(SRC_DIR)!r}); {code}"],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# The import-free rule (audit §3: a model-free run must never pay for it)
# ---------------------------------------------------------------------------
def test_importing_the_cleaner_does_not_import_a_network_client():
    result = _run_in_fresh_interpreter(
        "import post_processor; "
        "leaked = [m for m in ('urllib.request', 'http.client') if m in sys.modules]; "
        "assert not leaked, f'network client leaked into import: {leaked}'; "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_the_slm_client_still_imports_its_network_client():
    """The other half: the client is the one place that may speak HTTP."""
    result = _run_in_fresh_interpreter(
        "import slm_client; "
        "assert 'urllib.request' in sys.modules, 'slm_client did not import urllib'; "
        "assert callable(slm_client.process_slm_llm_rewrite); "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_the_cleaner_still_reaches_the_slm_client():
    """The split must not have orphaned the polish pass: the cleaner still calls
    it, just lazily, from inside the function."""
    result = _run_in_fresh_interpreter(
        "import post_processor, slm_client; "
        "assert not hasattr(post_processor, 'process_slm_llm_rewrite'), "
        "'a shim re-export would defeat the split'; "
        "print('OK')"
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout
