"""Live speaker tracking and online clustering for meeting mode.

Identifies speakers in real-time on short micro-batch audio chunks (~2-5s)
using sherpa-onnx 3D-Speaker voice embeddings on CPU.

Import discipline: importing this module never imports ``sherpa_onnx`` or
requires model weights to be present. The C++ runtime is loaded lazily on first
use, and missing weights or dependencies degrade gracefully to "no speaker
labels" rather than raising.
"""

from __future__ import annotations

import logging
import os
from typing import Callable

import numpy as np

logger = logging.getLogger(__name__)

#: Minimum audio duration (seconds) needed for a reliable speaker embedding.
#: Audio shorter than this inherits the active speaker's identity.
DEFAULT_MIN_DURATION_S = 0.75

#: Minimum audio duration required to establish a brand-new speaker cluster.
#: Short utterances (<1.4s) must match an existing speaker or inherit the active turn.
DEFAULT_MIN_NEW_CLUSTER_DURATION_S = 1.4

#: Cosine similarity threshold to match an incoming chunk to an existing speaker.
DEFAULT_SIMILARITY_THRESHOLD = 0.55

#: Continuity bonus added to the current speaker's score to prevent false
#: turn-splitting during natural pauses in conversational flow.
DEFAULT_CONTINUITY_BONUS = 0.06

#: Maximum exemplar embeddings kept per speaker cluster.
MAX_EXEMPLARS_PER_SPEAKER = 8


def get_embedding_model_path() -> str | None:
    """Resolve the 3D-Speaker embedding graph path, or ``None`` if absent."""
    try:
        from voice_transcriber.diarizers.sherpa_onnx import model_paths
        _, emb_path = model_paths()
        if emb_path and os.path.isfile(emb_path):
            return emb_path
    except Exception:
        logger.debug("Failed resolving diarization embedding model path", exc_info=True)
    return None


class LiveSpeakerTracker:
    """Online speaker clustering engine for streaming meeting transcription."""

    def __init__(
        self,
        model_path: str | None = None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        continuity_bonus: float = DEFAULT_CONTINUITY_BONUS,
        min_duration_s: float = DEFAULT_MIN_DURATION_S,
        min_new_cluster_duration_s: float = DEFAULT_MIN_NEW_CLUSTER_DURATION_S,
        max_speakers: int = 8,
        speaker_names_getter: Callable[[], list[str]] | None = None,
    ):
        self._model_path = model_path or get_embedding_model_path()
        self.threshold = threshold
        self.continuity_bonus = continuity_bonus
        self.min_duration_s = min_duration_s
        self.min_new_cluster_duration_s = min_new_cluster_duration_s
        self.max_speakers = max(1, max_speakers)
        self.speaker_names_getter = speaker_names_getter

        self._extractor = None
        self._extractor_checked = False
        # Each entry: {'id': int, 'exemplars': list[np.ndarray], 'count': int}
        self.speakers: list[dict] = []
        self.last_speaker_id: int | None = None

    @classmethod
    def available(cls) -> bool:
        """Return True if sherpa-onnx and the embedding weights are ready."""
        path = get_embedding_model_path()
        if not path:
            return False
        try:
            import sherpa_onnx  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure_extractor(self):
        if self._extractor_checked:
            return self._extractor
        self._extractor_checked = True
        if not self._model_path or not os.path.isfile(self._model_path):
            return None
        try:
            import sherpa_onnx

            config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=self._model_path,
                num_threads=2,
                provider="cpu",
            )
            if config.validate():
                self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        except Exception as exc:
            logger.debug("LiveSpeakerTracker extractor init failed: %s", exc)
            self._extractor = None
        return self._extractor

    def reset(self) -> None:
        """Clear cluster history."""
        self.speakers = []
        self.last_speaker_id = None

    def get_embedding(self, pcm: np.ndarray, sample_rate: int = 16000) -> np.ndarray | None:
        """Extract a unit-normalized speaker embedding vector from PCM audio."""
        extractor = self._ensure_extractor()
        if extractor is None:
            return None
        min_samples = int(self.min_duration_s * sample_rate)
        if len(pcm) < min_samples:
            return None

        try:
            if not isinstance(pcm, np.ndarray) or pcm.dtype != np.float32:
                pcm = np.asarray(pcm, dtype=np.float32).ravel()
            stream = extractor.create_stream()
            stream.accept_waveform(sample_rate=sample_rate, waveform=pcm)
            stream.input_finished()
            if not extractor.is_ready(stream):
                return None
            emb = np.array(extractor.compute(stream), dtype=np.float32)
            norm = float(np.linalg.norm(emb))
            if norm < 1e-6:
                return None
            return emb / norm
        except Exception as exc:
            logger.debug("LiveSpeakerTracker embedding extraction failed: %s", exc)
            return None

    def format_label(self, speaker_id: int) -> str:
        """Map a 0-based speaker ID onto a display name."""
        names = []
        if self.speaker_names_getter is not None:
            try:
                names = list(self.speaker_names_getter() or [])
            except Exception:
                pass
        if 0 <= speaker_id < len(names):
            val = str(names[speaker_id]).strip()
            if val:
                return val
        return f"Speaker {speaker_id + 1}"

    def identify(self, pcm: np.ndarray, sample_rate: int = 16000) -> tuple[int, str, float]:
        """Identify or assign a speaker ID for this audio chunk.

        Returns ``(speaker_id, speaker_label, similarity_score)``.
        """
        emb = self.get_embedding(pcm, sample_rate)
        if emb is None:
            # Fall back to active speaker or first speaker
            spk_id = self.last_speaker_id if self.last_speaker_id is not None else 0
            return spk_id, self.format_label(spk_id), 0.0

        dur_s = float(len(pcm)) / float(sample_rate)
        if not self.speakers:
            # First speaker observed
            self.speakers.append({"id": 0, "exemplars": [emb], "count": 1})
            self.last_speaker_id = 0
            return 0, self.format_label(0), 1.0

        scores: list[tuple[int, float, float]] = []
        for spk in self.speakers:
            sims = [float(np.dot(emb, ex)) for ex in spk["exemplars"]]
            top_sims = sorted(sims, reverse=True)[:3]
            raw_sim = float(np.mean(top_sims))
            # Continuity bonus for the active speaker
            effective_sim = (
                raw_sim + self.continuity_bonus
                if spk["id"] == self.last_speaker_id
                else raw_sim
            )
            scores.append((spk["id"], effective_sim, raw_sim))

        best_spk_id, best_effective, raw_sim = max(scores, key=lambda x: x[1])

        # A new cluster requires meeting the threshold AND enough duration (>=1.4s)
        # to ensure stable vocal characteristics rather than a transient burst.
        if raw_sim >= self.threshold or len(self.speakers) >= self.max_speakers:
            # Match existing speaker
            spk = next(s for s in self.speakers if s["id"] == best_spk_id)
            spk["count"] += 1
            if dur_s >= 1.2 and len(spk["exemplars"]) < MAX_EXEMPLARS_PER_SPEAKER:
                spk["exemplars"].append(emb)
            self.last_speaker_id = best_spk_id
            return best_spk_id, self.format_label(best_spk_id), raw_sim
        elif dur_s >= self.min_new_cluster_duration_s:
            # Register new speaker cluster from full speech chunk
            new_id = len(self.speakers)
            self.speakers.append({"id": new_id, "exemplars": [emb], "count": 1})
            self.last_speaker_id = new_id
            return new_id, self.format_label(new_id), raw_sim
        else:
            # Too short to establish a brand-new person: assign to closest existing speaker
            spk = next(s for s in self.speakers if s["id"] == best_spk_id)
            spk["count"] += 1
            self.last_speaker_id = best_spk_id
            return best_spk_id, self.format_label(best_spk_id), raw_sim
