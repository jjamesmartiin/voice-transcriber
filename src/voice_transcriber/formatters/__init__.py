"""Formatter backends.

Each module here implements the same three-function contract and nothing else::

    available() -> bool
    warm() -> None
    format_text(text, *, style, structure, context, timeout_s) -> str

A backend is selected by name through :data:`voice_transcriber.formatter.BACKENDS`.
Nothing in this package is imported at app startup, so a machine that never
enables the formatter never pays for a model runtime.
"""
