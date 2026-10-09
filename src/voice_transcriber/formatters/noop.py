"""Identity backend - returns the transcript unchanged.

Two jobs, both of which matter more than they look:

* It lets the whole formatter path be exercised with **no model at all**, so the
  plumbing, the guardrails and the settings can be proven before any weights
  exist (milestone C1).
* It is the honest "off" that the fallback path lands on. When a real backend is
  missing, broken or too slow, the caller wants the cleaned text back - which is
  precisely what this returns.

It is also a useful manual check: setting ``formatter_model: noop`` proves the
app's wiring end-to-end without a 462 MiB download.
"""

from __future__ import annotations


def available() -> bool:
    """Always available - that is the point of it."""
    return True


def warm() -> None:
    """Nothing to load."""
    return None


def format_text(
    text: str,
    *,
    style: str = "semi-formal",
    structure: str = "prose",
    context: str = "general",
    timeout_s: float = 3.0,
) -> str:
    """Return ``text`` unchanged, ignoring every axis."""
    return text
