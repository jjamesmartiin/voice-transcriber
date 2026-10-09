#!/usr/bin/env python3
"""
Unit tests for the real sherpa-onnx diarization backend (milestone D3).

Everything here is model-free except the single smoke test guarded by
``_weights_present()``: the point is that the backend's contract surface -
``available()``, the lazy dependency import, argument mapping (``num_speakers``
-> ``num_clusters``), threshold forwarding, progress adaptation and the
"missing weights degrade to unavailable" rule - is pinned without loading the
~47 MB of ONNX graphs (``docs/plan-diarization.md`` sec 9).

The fake ``sherpa_onnx`` module used here mirrors the real 1.12.25 API recorded
in ``docs/diarization-benchmark.md``; in particular the pyannote segmentation
config takes only ``model`` (no ``window_shift_ratio``).
"""
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import diarize
from voice_transcriber.diarizers import sherpa_onnx as so


@pytest.fixture(autouse=True)
def _clean_state():
    diarize.reset_backend_cache()
    so.reset()
    yield
    diarize.reset_backend_cache()
    so.reset()


# ---------------------------------------------------------------------------
# A fake sherpa_onnx module, matching the real constructor signatures
# ---------------------------------------------------------------------------
def make_fake_sherpa(*, result=(), sample_rate=16000, valid=True, on_process=None):
    """Return ``(module, records)`` implementing just enough of the API.

    ``records`` captures every mapping decision so tests can assert on it.
    """
    records: dict = {"engine_builds": 0}

    class _Pyannote:
        def __init__(self, model=None):
            records["pyannote_model"] = model

    class _Segmentation:
        def __init__(self, pyannote=None, **kwargs):
            records["pyannote"] = pyannote

    class _Embedding:
        def __init__(self, model=None, **kwargs):
            records["embedding_model"] = model

    class _Clustering:
        def __init__(self, num_clusters=-1, threshold=0.5):
            records["num_clusters"] = num_clusters
            records["threshold"] = threshold

    class _Config:
        def __init__(self, segmentation=None, embedding=None, clustering=None,
                     min_duration_on=0.3, min_duration_off=0.5):
            records["min_duration_on"] = min_duration_on
            records["min_duration_off"] = min_duration_off
            self.segmentation = segmentation
            self.embedding = embedding
            self.clustering = clustering

        def validate(self):
            return valid

    class _Result(list):
        def sort_by_start_time(self):
            return _Result(sorted(self, key=lambda item: item.start))

    class _Engine:
        def __init__(self):
            records["engine_builds"] += 1
            self.sample_rate = sample_rate

        def process(self, samples, callback=None):
            records["samples"] = samples
            if callback is not None and on_process is not None:
                on_process(callback)
            return _Result(result)

    module = types.SimpleNamespace(
        OfflineSpeakerDiarizationConfig=_Config,
        OfflineSpeakerSegmentationModelConfig=_Segmentation,
        OfflineSpeakerSegmentationPyannoteModelConfig=_Pyannote,
        SpeakerEmbeddingExtractorConfig=_Embedding,
        FastClusteringConfig=_Clustering,
        OfflineSpeakerDiarization=lambda config: _Engine(),
    )
    return module, records


def _item(start, end, speaker):
    return types.SimpleNamespace(start=start, end=end, speaker=speaker)


def _install(monkeypatch, fake, tmp_path, *, files_exist=True):
    """Wire the backend to a fake dependency and a temporary model directory."""
    segmentation = tmp_path / "seg.onnx"
    embedding = tmp_path / "emb.onnx"
    if files_exist:
        segmentation.write_bytes(b"x")
        embedding.write_bytes(b"x")
    monkeypatch.setattr(so, "model_paths", lambda: (str(segmentation), str(embedding)))
    monkeypatch.setattr(so, "_get_sherpa", lambda: fake)
    monkeypatch.setattr(so, "_dependency_available", lambda: True)


# ---------------------------------------------------------------------------
# available() - cheap, and about files not models
# ---------------------------------------------------------------------------
def test_available_is_false_without_the_dependency(monkeypatch):
    monkeypatch.setattr(so, "_dependency_available", lambda: False)
    assert so.available() is False


def test_available_is_false_when_a_model_file_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(so, "_dependency_available", lambda: True)
    monkeypatch.setattr(
        so, "model_paths",
        lambda: (str(tmp_path / "missing_seg.onnx"), str(tmp_path / "missing_emb.onnx")),
    )
    assert so.available() is False


def test_available_is_true_when_both_files_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(so, "_dependency_available", lambda: True)
    seg, emb = tmp_path / "seg.onnx", tmp_path / "emb.onnx"
    seg.write_bytes(b"x")
    emb.write_bytes(b"x")
    monkeypatch.setattr(so, "model_paths", lambda: (str(seg), str(emb)))
    assert so.available() is True


def test_available_does_not_load_the_engine(monkeypatch, tmp_path):
    """It is called before every run; it must not touch the ONNX graphs."""
    monkeypatch.setattr(so, "_dependency_available", lambda: True)
    seg, emb = tmp_path / "seg.onnx", tmp_path / "emb.onnx"
    seg.write_bytes(b"x")
    emb.write_bytes(b"x")
    monkeypatch.setattr(so, "model_paths", lambda: (str(seg), str(emb)))

    def explode():
        raise AssertionError("available() imported/loaded sherpa_onnx")

    monkeypatch.setattr(so, "_get_sherpa", explode)
    assert so.available() is True


# ---------------------------------------------------------------------------
# The import-free rule
# ---------------------------------------------------------------------------
def test_importing_the_backend_does_not_import_sherpa_onnx():
    code = (
        "import sys; sys.path.insert(0, %r); "
        "from voice_transcriber.diarizers import sherpa_onnx; "
        "assert 'sherpa_onnx' not in sys.modules, 'sherpa_onnx leaked at import'; "
        "print('OK')" % str(SRC_DIR)
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Argument mapping: num_speakers -> num_clusters, thresholds forwarded
# ---------------------------------------------------------------------------
def test_num_speakers_none_maps_to_auto_clustering(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path)
    so.diarize([0.0] * 16000 * 2, 16000, num_speakers=None)
    assert records["num_clusters"] == so.AUTO_NUM_CLUSTERS == -1


def test_known_speaker_count_is_forwarded(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path)
    so.diarize([0.0] * 16000 * 2, 16000, num_speakers=3)
    assert records["num_clusters"] == 3


def test_threshold_and_merge_windows_are_forwarded(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path)
    so.diarize(
        [0.0] * 16000 * 2, 16000,
        threshold=0.2, min_duration_on=0.4, min_duration_off=0.7,
    )
    assert records["threshold"] == 0.2
    assert records["min_duration_on"] == 0.4
    assert records["min_duration_off"] == 0.7


def test_default_threshold_and_windows_are_the_shared_constants():
    """The defaults must match diarize.py's; two sets of numbers is the bug the
    plan forbids (sec 5.1)."""
    assert so.DEFAULT_THRESHOLD == diarize.DEFAULT_THRESHOLD == 0.5
    assert so.MIN_DURATION_ON_S == diarize.MIN_DURATION_ON_S == 0.3
    assert so.MIN_DURATION_OFF_S == diarize.MIN_DURATION_OFF_S == 0.5


def test_both_model_paths_reach_the_library(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path)
    so.diarize([0.0] * 16000 * 2, 16000)
    assert records["pyannote_model"].endswith("seg.onnx")
    assert records["embedding_model"].endswith("emb.onnx")


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------
def test_results_become_sorted_turns(monkeypatch, tmp_path):
    fake, _ = make_fake_sherpa(result=[_item(1.0, 2.0, 0), _item(0.0, 1.0, 1)])
    _install(monkeypatch, fake, tmp_path)
    turns = so.diarize([0.0] * 16000 * 2, 16000)
    assert [t.start for t in turns] == [0.0, 1.0]
    assert [t.speaker for t in turns] == [1, 0]
    assert all(isinstance(t, diarize.Turn) for t in turns)


# ---------------------------------------------------------------------------
# Progress: handed to the library, adapted only for its int return
# ---------------------------------------------------------------------------
def test_progress_callback_receives_the_library_events(monkeypatch, tmp_path):
    seen = []

    def on_process(callback):
        assert callback(1, 4) == 0  # library requires int 0 to continue
        assert callback(2, 4) == 0

    fake, _ = make_fake_sherpa(on_process=on_process)
    _install(monkeypatch, fake, tmp_path)
    so.diarize(
        [0.0] * 16000 * 2, 16000,
        progress=lambda done, total: seen.append((done, total)),
    )
    assert seen == [(1, 4), (2, 4)]


def test_no_progress_means_no_callback(monkeypatch, tmp_path):
    seen = []

    def on_process(callback):  # pragma: no cover - must not be called
        seen.append("called")

    fake, _ = make_fake_sherpa(on_process=on_process)
    _install(monkeypatch, fake, tmp_path)
    so.diarize([0.0] * 16000 * 2, 16000, progress=None)
    assert seen == []


# ---------------------------------------------------------------------------
# Audio preparation: engine sample rate is authoritative, resample when needed
# ---------------------------------------------------------------------------
def test_same_rate_audio_is_passed_through_as_float32():
    out = so._prepare_audio([0.0] * 100, 16000, 16000)
    assert out.dtype == np.float32
    assert out.shape == (100,)


def test_audio_is_resampled_to_the_engine_rate():
    out = so._prepare_audio([0.0] * 8000, 8000, 16000)
    assert out.dtype == np.float32
    assert out.shape[0] == 16000


def test_multichannel_audio_is_downmixed():
    out = so._prepare_audio(np.zeros((100, 2), dtype=np.float32), 16000, 16000)
    assert out.shape == (100,)


def test_engine_sample_rate_is_used_not_a_hardcoded_16000(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa(sample_rate=8000)
    _install(monkeypatch, fake, tmp_path)
    so.diarize([0.0] * 16000, 16000)  # 1 s at 16 kHz -> 0.5 s at 8 kHz
    assert records["samples"].shape[0] == 8000


# ---------------------------------------------------------------------------
# warm(): preload once, best-effort
# ---------------------------------------------------------------------------
def test_warm_builds_the_engine_once(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path)
    so.warm()
    so.warm()
    assert records["engine_builds"] == 1
    assert len(so._engines) == 1


def test_warm_is_silent_when_the_weights_are_missing(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path, files_exist=False)
    so.warm()  # must not raise
    assert records["engine_builds"] == 0


def test_diarize_reuses_a_warmed_engine(monkeypatch, tmp_path):
    fake, records = make_fake_sherpa()
    _install(monkeypatch, fake, tmp_path)
    so.warm()
    so.diarize([0.0] * 16000 * 2, 16000)
    assert records["engine_builds"] == 1


def test_a_rejected_config_raises(monkeypatch, tmp_path):
    fake, _ = make_fake_sherpa(valid=False)
    _install(monkeypatch, fake, tmp_path)
    with pytest.raises(RuntimeError, match="rejected"):
        so.diarize([0.0] * 16000 * 2, 16000)


# ---------------------------------------------------------------------------
# Missing weights degrade through the fail-safe, they do not raise
# ---------------------------------------------------------------------------
def test_missing_model_files_degrade_to_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(so, "_dependency_available", lambda: True)
    monkeypatch.setattr(
        so, "model_paths",
        lambda: (str(tmp_path / "nope_seg.onnx"), str(tmp_path / "nope_emb.onnx")),
    )
    turns = diarize.diarize([0.0] * 16000 * 3, 16000, backend="sherpa-onnx")
    assert turns == []


def test_models_dir_honours_the_override(monkeypatch, tmp_path):
    monkeypatch.setenv(so.MODEL_DIR_ENV, str(tmp_path))
    assert so.models_dir() == str(tmp_path)


# ---------------------------------------------------------------------------
# Real weights: skipped unless the ~47 MB of graphs are installed
# ---------------------------------------------------------------------------
def _weights_present() -> bool:
    try:
        return so.available()
    except Exception:
        return False


@pytest.mark.skipif(not _weights_present(), reason="diarization weights not installed")
def test_real_backend_runs_on_the_installed_weights(monkeypatch):
    """Exercises the real ONNX graphs end to end.

    A synthetic (non-speech) signal is intentional: the assertion is that the
    real API path loads, processes and returns well-formed turns - or none -
    rather than that a particular signal is a particular speaker.
    """
    sample_rate = 16000
    rng = np.random.default_rng(0)
    audio = (0.05 * rng.standard_normal(sample_rate * 3)).astype(np.float32)
    turns = so.diarize(audio, sample_rate, num_speakers=2)
    assert isinstance(turns, list)
    for turn in turns:
        assert isinstance(turn, diarize.Turn)
        assert 0.0 <= turn.start < turn.end <= 3.0 + 1e-3
        assert turn.speaker >= 0
