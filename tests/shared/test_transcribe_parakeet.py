#!/usr/bin/env python3
"""Model-free tests for the Parakeet TDT 0.6B v3 ASR backend.

The point of this file is to prove the *contract* the backend registry relies on
(declaration, import discipline, call shapes, word-piece merging, model
location) before the ~490 MB of weights are involved. Nothing here loads a
recognizer: the call-shape tests inject a fake one, mirroring how
``tests/shared/test_diarize.py`` exercises its backends against behaviour rather
than against a mock's assumptions.

One optional test does run the real graphs. It is skipped unless both
``sherpa_onnx`` and the weights are present, so a machine without them stays
green, and it is additionally opt-in via ``VT_TEST_PARAKEET=1`` so the shared
tier stays model-free by default.

Run the real one with::

    VT_TEST_PARAKEET=1 nix develop --command python -m pytest \\
        tests/shared/test_transcribe_parakeet.py -q
"""
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import transcribe2  # noqa: E402
import voice_transcriber.transcribe_parakeet as para  # noqa: E402


class TestRegistry:
    def test_parakeet_is_declared(self):
        assert transcribe2.BACKENDS.get("parakeet") == "voice_transcriber.transcribe_parakeet"

    def test_it_resolves_case_insensitively(self):
        assert transcribe2.resolve_backend_name("  PaRaKeEt ") == "parakeet"

    def test_the_default_is_still_cohere(self):
        """The bake-off verdict, not this module, decides a default change."""
        assert transcribe2.DEFAULT_BACKEND == "cohere"

    def test_it_loads_through_the_registry(self):
        # Restore the active backend so this test cannot leak selection state.
        saved = transcribe2._active
        try:
            module = transcribe2.get_backend("parakeet")
            assert callable(module.transcribe_audio)
            assert callable(module.preload_model)
        finally:
            transcribe2._active = saved


class TestImportDiscipline:
    def test_importing_the_module_does_not_import_sherpa(self):
        """A fresh interpreter must not pay for ``sherpa_onnx`` at import time."""
        code = (
            "import sys; import voice_transcriber.transcribe_parakeet; "
            "assert 'sherpa_onnx' not in sys.modules, 'sherpa_onnx imported eagerly'; "
            "print('ok')"
        )
        env = dict(os.environ, PYTHONPATH=str(SRC_DIR))
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "ok"

    def test_the_dependency_getter_is_lazy(self):
        para.reset()
        assert para._sherpa is None
        assert para._model is None


class TestModelLocation:
    def test_model_paths_are_named_and_under_one_directory(self):
        encoder, decoder, joiner, tokens = para.model_paths()
        names = [Path(p).name for p in (encoder, decoder, joiner, tokens)]
        assert names == [
            "encoder.int8.onnx",
            "decoder.int8.onnx",
            "joiner.int8.onnx",
            "tokens.txt",
        ]
        assert len({str(Path(p).parent) for p in (encoder, decoder, joiner, tokens)}) == 1

    def test_env_override_is_honoured(self, tmp_path, monkeypatch):
        monkeypatch.setenv(para.MODEL_DIR_ENV, str(tmp_path))
        assert Path(para.models_dir()) == tmp_path

    def test_available_requires_all_four_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv(para.MODEL_DIR_ENV, str(tmp_path))
        # No files: not available (assuming sherpa can be imported at all).
        if para._dependency_available():
            assert para.available() is False
        for name in para._GRAPH_FILES:
            (tmp_path / name).write_bytes(b"")
        if para._dependency_available():
            assert para.available() is False
        (tmp_path / para.TOKENS_FILE).write_text("")
        if para._dependency_available():
            assert para.available() is True

    def test_nested_extraction_directory_is_found(self, tmp_path):
        nested = tmp_path / para.MODEL_NAME
        nested.mkdir()
        for name in para._REQUIRED_FILES:
            (nested / name).write_bytes(b"")
        assert para._looks_like_model_dir(str(nested))


class TestWordPieceMerging:
    def test_words_are_reconstructed_from_space_prefixed_tokens(self):
        tokens = [" A", "sk", " not", " what", " you", "r", " co", "un", "tr", "y"]
        times = [0.0, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
        words = para._merge_word_pieces(tokens, times, fallback_end=1.5)
        assert [w["word"] for w in words] == ["Ask", "not", "what", "your", "country"]
        assert words[0]["start"] == 0.0
        assert words[0]["end"] == 0.4
        assert words[-1]["end"] == 1.5

    def test_sentencepiece_marker_is_also_a_word_start(self):
        tokens = ["\u2581hello", "\u2581world"]
        words = para._merge_word_pieces(tokens, [0.0, 0.5], fallback_end=1.0)
        assert [w["word"] for w in words] == ["hello", "world"]

    def test_durations_give_each_word_a_real_end(self):
        tokens = [" hello", " world"]
        timestamps = [1.0, 2.0]
        durations = [0.6, 0.5]
        words = para._merge_word_pieces(
            tokens, timestamps, fallback_end=99.0, durations=durations
        )
        assert words[0]["start"] == 1.0 and words[0]["end"] == pytest.approx(1.6)
        assert words[1]["start"] == 2.0 and words[1]["end"] == pytest.approx(2.5)

    def test_empty_tokens_yields_no_words(self):
        assert para._merge_word_pieces([], [], fallback_end=1.0) == []


class _FakeResult:
    def __init__(self, text):
        self.text = text
        self.tokens = [" hi"]
        self.timestamps = [0.0]
        self.durations = [0.1]


class _FakeStream:
    result = None
    accepted = None

    def accept_waveform(self, sample_rate, waveform):
        self.accepted = (sample_rate, len(waveform))


class _FakeRecognizer:
    def __init__(self, text, calls):
        self.text = text
        self.calls = calls

    def create_stream(self):
        return _FakeStream()

    def decode_stream(self, stream):
        self.calls.append("decode")
        stream.result = _FakeResult(self.text)


class TestCallShapes:
    def test_audio_data_is_transcribed(self, monkeypatch):
        calls = []
        monkeypatch.setattr(para, "get_model", lambda device="cpu": _FakeRecognizer("hello world", calls))
        text = para.transcribe_audio(audio_data=np.ones(16000, dtype=np.float32), sample_rate=16000)
        assert text == "hello world"
        assert calls == ["decode"]

    def test_audio_path_is_transcribed(self, tmp_path, monkeypatch):
        import soundfile as sf

        wav = tmp_path / "clip.wav"
        sf.write(str(wav), np.ones(8000, dtype=np.float32), 16000)
        calls = []
        monkeypatch.setattr(para, "get_model", lambda device="cpu": _FakeRecognizer("from file", calls))
        text = para.transcribe_audio(audio_path=str(wav))
        assert text == "from file"

    def test_no_audio_returns_empty(self):
        assert para.transcribe_audio() == ""

    def test_silence_never_reaches_the_model(self, monkeypatch):
        def _boom(device="cpu"):
            raise AssertionError("the model must not be loaded for pure silence")

        monkeypatch.setattr(para, "get_model", _boom)
        assert para.transcribe_audio(audio_data=np.zeros(16000, dtype=np.float32)) == ""

    def test_an_unknown_language_is_accepted_and_ignored(self, monkeypatch):
        monkeypatch.setattr(para, "get_model", lambda device="cpu": _FakeRecognizer("ok", []))
        assert para.transcribe_audio(audio_data=np.ones(16000, dtype=np.float32), language="xx") == "ok"


class TestRealModel:
    """Opt-in end-to-end test; skipped without weights or without the opt-in."""

    @pytest.fixture(autouse=True)
    def _require_weights(self, monkeypatch):
        if not para.available():
            pytest.skip("Parakeet weights or sherpa_onnx not available")
        if os.environ.get("VT_TEST_PARAKEET") != "1":
            pytest.skip("set VT_TEST_PARAKEET=1 to run the real-model test")

    def _sample(self):
        wav = Path(para.model_paths()[0]).parent / "test_wavs" / "en.wav"
        if not wav.is_file():
            pytest.skip("test_wavs/en.wav not present")
        return str(wav)

    def test_it_transcribes_and_exposes_word_timestamps(self):
        out = para.transcribe_with_timestamps(audio_path=self._sample())
        assert "country" in out["text"].lower()
        assert out["tokens"], "the model exposed no tokens"
        assert len(out["timestamps"]) == len(out["tokens"])
        assert out["words"], "no words could be reconstructed"
        starts = [w["start"] for w in out["words"]]
        assert starts == sorted(starts)
        # A word's end must not precede its start when both are known.
        for w in out["words"]:
            if w["start"] is not None and w["end"] is not None:
                assert w["end"] >= w["start"]
        para.unload_model()
