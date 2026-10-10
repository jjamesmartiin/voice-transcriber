"""Shared helper for the Rust parity guards.

The guards pin literals in the *non-test* half of a Rust source file, because
the Rust test modules repeat those same literals: matching the whole file would
let the implementation drift while the guard stayed green. Splitting on the
``#[cfg(test)]`` marker only scopes the search if the marker is actually there;
if it is renamed or moved, the split returns the whole file and the guard passes
for the wrong reason. This helper asserts the marker exists so that rename fails
the guard loudly.
"""

from pathlib import Path

_MARKER = "\n#[cfg(test)]"


def rust_implementation(path) -> str:
    """The implementation (pre-``#[cfg(test)]``) half of a Rust source file."""
    source = Path(path).read_text(encoding="utf-8")
    assert _MARKER in source, (
        f"{path} has no {_MARKER.strip()!r} marker; the parity guard cannot "
        "scope itself to the implementation half and would match the Rust tests"
    )
    return source.split(_MARKER, 1)[0]
