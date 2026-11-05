#!/usr/bin/env python3
"""
Unit tests for LiveSpeakerTracker and micro-batched live speaker attribution.
Runs model-free by mocking or using synthetic embedding vectors.
"""

import numpy as np

from voice_transcriber.micro_batcher import StreamingMicroBatcher
from voice_transcriber.speaker_tracker import LiveSpeakerTracker


class DummyExtractor:
    def __init__(self, vector_fn):
        self.vector_fn = vector_fn
        self.call_count = 0

    def create_stream(self):
        return object()

    def accept_waveform(self, sample_rate, waveform):
        pass

    def input_finished(self):
        pass

    def is_ready(self, stream):
        return True

    def compute(self, stream):
        self.call_count += 1
        return self.vector_fn(self.call_count)


def test_speaker_tracker_graceful_missing_model():
    """Tracker must not crash when model file is missing or invalid."""
    tracker = LiveSpeakerTracker(model_path="/nonexistent/model.onnx")
    pcm = np.random.uniform(-0.1, 0.1, 16000).astype(np.float32)
    spk_id, label, sim = tracker.identify(pcm, sample_rate=16000)
    assert spk_id == 0
    assert label == "Speaker 1"
    assert sim == 0.0


def test_speaker_tracker_short_audio_fallback():
    """Short audio (< min_duration_s) falls back to active speaker."""
    tracker = LiveSpeakerTracker(min_duration_s=1.0)
    tracker.last_speaker_id = 2
    short_pcm = np.zeros(8000, dtype=np.float32)  # 0.5s at 16kHz
    spk_id, label, sim = tracker.identify(short_pcm, sample_rate=16000)
    assert spk_id == 2
    assert label == "Speaker 3"
    assert sim == 0.0


def test_speaker_tracker_clustering_workflow():
    """Tracks speaker transitions and matches recurring speakers using synthetic embeddings."""
    tracker = LiveSpeakerTracker(threshold=0.60, min_duration_s=0.1, min_new_cluster_duration_s=0.1)

    # Unit vectors for synthetic speakers
    # Dim 4:
    v_alice = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    v_alice_variation = np.array([0.96, 0.28, 0.0, 0.0], dtype=np.float32)  # cos sim ~ 0.96
    v_bob = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)  # orthogonal to Alice (cos sim 0.0)
    v_bob_variation = np.array([0.1, 0.99, 0.0, 0.0], dtype=np.float32)  # cos sim ~ 0.99 to Bob

    # Mock extractor output
    sequence = [v_alice, v_bob, v_alice_variation, v_bob_variation]
    curr_idx = [0]

    def mock_get_embedding(pcm, sr=16000):
        idx = curr_idx[0]
        curr_idx[0] += 1
        return sequence[idx]

    tracker.get_embedding = mock_get_embedding
    dummy_pcm = np.zeros(2000, dtype=np.float32)

    # 1. First turn: Alice
    id1, label1, sim1 = tracker.identify(dummy_pcm)
    assert id1 == 0
    assert label1 == "Speaker 1"

    # 2. Second turn: Bob
    id2, label2, sim2 = tracker.identify(dummy_pcm)
    assert id2 == 1
    assert label2 == "Speaker 2"
    assert sim2 < 0.60

    # 3. Third turn: Alice speaks again
    id3, label3, sim3 = tracker.identify(dummy_pcm)
    assert id3 == 0
    assert label3 == "Speaker 1"
    assert sim3 > 0.90

    # 4. Fourth turn: Bob speaks again
    id4, label4, sim4 = tracker.identify(dummy_pcm)
    assert id4 == 1
    assert label4 == "Speaker 2"
    assert sim4 > 0.90

    assert len(tracker.speakers) == 2


def test_speaker_tracker_speaker_names_getter():
    """Names getter maps speaker IDs onto user-defined names."""
    names = ["Alice", "Bob"]
    tracker = LiveSpeakerTracker(
        threshold=0.60,
        min_duration_s=0.1,
        min_new_cluster_duration_s=0.1,
        speaker_names_getter=lambda: names,
    )

    v1 = np.array([1.0, 0.0], dtype=np.float32)
    v2 = np.array([0.0, 1.0], dtype=np.float32)
    sequence = [v1, v2]
    curr_idx = [0]

    def mock_get_embedding(pcm, sr=16000):
        idx = curr_idx[0]
        curr_idx[0] += 1
        return sequence[idx]

    tracker.get_embedding = mock_get_embedding
    pcm = np.zeros(2000, dtype=np.float32)

    id1, label1, _ = tracker.identify(pcm)
    assert label1 == "Alice"
    id2, label2, _ = tracker.identify(pcm)
    assert label2 == "Bob"


def test_speaker_tracker_max_speakers_bound():
    """Clustering caps at max_speakers rather than creating unbounded clusters."""
    tracker = LiveSpeakerTracker(threshold=0.99, max_speakers=2, min_duration_s=0.1, min_new_cluster_duration_s=0.1)

    # Provide 3 orthogonal vectors
    sequence = [
        np.array([1.0, 0.0, 0.0], dtype=np.float32),
        np.array([0.0, 1.0, 0.0], dtype=np.float32),
        np.array([0.0, 0.0, 1.0], dtype=np.float32),
    ]
    curr_idx = [0]

    def mock_get_embedding(pcm, sr=16000):
        idx = curr_idx[0]
        curr_idx[0] += 1
        return sequence[idx]

    tracker.get_embedding = mock_get_embedding
    pcm = np.zeros(2000, dtype=np.float32)

    tracker.identify(pcm)  # speaker 0
    tracker.identify(pcm)  # speaker 1
    id3, _, _ = tracker.identify(pcm)  # 3rd vector: capped at max_speakers=2, assigned to best match
    assert id3 in (0, 1)
    assert len(tracker.speakers) == 2


def test_short_utterance_does_not_spawn_new_cluster():
    """Short utterance (<1.4s) does not spawn a new cluster even if dissimilar."""
    tracker = LiveSpeakerTracker(threshold=0.60, min_duration_s=0.1, min_new_cluster_duration_s=1.4)

    v1 = np.array([1.0, 0.0], dtype=np.float32)
    v2_dissimilar = np.array([0.0, 1.0], dtype=np.float32)
    seq = [v1, v2_dissimilar]
    curr = [0]

    tracker.get_embedding = lambda pcm, sr=16000: seq[curr.__setitem__(0, curr[0] + 1) or curr[0] - 1]

    # First turn: establishes Speaker 0
    tracker.identify(np.zeros(32000, dtype=np.float32))  # 2.0s
    assert len(tracker.speakers) == 1

    # Second turn: dissimilar vector, but only 0.8s (< 1.4s floor)
    id2, _, _ = tracker.identify(np.zeros(12800, dtype=np.float32))  # 0.8s
    # Must NOT spawn new cluster; assigns to closest existing
    assert id2 == 0
    assert len(tracker.speakers) == 1


def test_micro_batcher_speaker_tagging_integration():
    """StreamingMicroBatcher formats live text with speaker tags."""
    class MockTracker:
        def __init__(self):
            self.names = ["Alice", "Bob"]

        def format_label(self, spk_id):
            if 0 <= spk_id < len(self.names):
                return self.names[spk_id]
            return f"Speaker {spk_id + 1}"

    tracker = MockTracker()
    batcher = StreamingMicroBatcher(sample_rate=16000, speaker_tracker=tracker)

    # Turn 1: Alice chunk 0
    batcher._advance_live_text(0, "Good morning everyone.", False, False, spk_id=0, spk_label="Alice")
    assert "[Alice] Good morning everyone." in batcher._live_text

    # Turn 1 continued: Alice chunk 1
    batcher._advance_live_text(1, "Let's review the metrics.", False, False, spk_id=0, spk_label="Alice")
    assert "[Alice] Good morning everyone. Let's review the metrics." in batcher._live_text

    # Turn 2: Bob chunk 2
    batcher._advance_live_text(2, "Sounds good to me.", False, False, spk_id=1, spk_label="Bob")
    assert "[Bob] Sounds good to me." in batcher._live_text
