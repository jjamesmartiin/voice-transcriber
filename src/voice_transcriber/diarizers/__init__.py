"""Diarization backends.

Each module here implements the contract documented in
:mod:`voice_transcriber.diarize`: ``available()``, ``diarize()`` and an
optional ``warm()``. Nothing in this package may be imported eagerly - the
runtime it depends on (``sherpa_onnx``, and the models behind it) must stay out
of the app's import path until the feature is actually used.
"""
