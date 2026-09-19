"""
Streaming Micro-Batcher Engine
Splits live audio streams into real-time micro-batches based on natural pauses
and speech energy, transcribing chunks concurrently in the background.
"""

import os
import sys
import time
import queue
import threading
import re
import numpy as np

# Ensure src is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcribe2

from post_processor import clean_speech_transcription, process_slm_llm_stream_concat, STUTTER_PROTECTED_WORDS

def clean_hallucinations(text, skip_slm=False, is_intermediate=False):
    return clean_speech_transcription(text, skip_slm=skip_slm, is_intermediate=is_intermediate)

def _common_prefix_len(a: str, b: str) -> int:
    i = 0
    m = min(len(a), len(b))
    while i < m and a[i] == b[i]:
        i += 1
    return i

def deduplicate_text_overlap(prev_text: str, new_text: str, has_speech_overlap: bool = True) -> str:
    """
    Deduplicates overlapping word sequences between consecutive micro-batch transcripts.
    Preserves genuine repeated words (e.g. 'two two three', 'really really') while
    collapsing acoustic overlaps and healing cut-word truncations ('hospitable' + 'hospitably' -> 'hospitably').
    """
    if not prev_text:
        return new_text or ''
    if not new_text:
        return prev_text or ''

    # If there was no acoustic speech overlap between chunks (e.g. cut cleanly in silence
    # or at an energy valley), genuine spoken repetitions must be preserved untouched.
    if not has_speech_overlap:
        return prev_text.strip() + ' ' + new_text.strip()

    prev_words = prev_text.strip().split()
    new_words = new_text.strip().split()

    if not prev_words:
        return ' '.join(new_words)
    if not new_words:
        return ' '.join(prev_words)

    max_overlap = min(6, len(prev_words), len(new_words))
    clean_prev = [w.lower().strip(',.!?";:()[]{}') for w in prev_words]
    clean_new = [w.lower().strip(',.!?";:()[]{}') for w in new_words]

    # 1. Multi-word exact overlap (k >= 2): genuine acoustic duplicate across chunk boundary
    for overlap_len in range(max_overlap, 1, -1):
        if clean_prev[-overlap_len:] == clean_new[:overlap_len]:
            deduped_new = ' '.join(new_words[overlap_len:])
            return (prev_text.strip() + ' ' + deduped_new).strip() if deduped_new else prev_text.strip()

    # 1b. Multi-word overlap with trailing stem truncation (e.g. 'the gate' vs 'the gateway')
    for overlap_len in range(max_overlap, 1, -1):
        if clean_prev[-overlap_len:-1] == clean_new[:overlap_len-1]:
            w_p = clean_prev[-1]
            w_n = clean_new[overlap_len-1]
            min_l = min(len(w_p), len(w_n))
            pref = _common_prefix_len(w_p, w_n)
            if pref >= 4 and pref >= min_l - 2:
                better_word = new_words[overlap_len-1] if len(w_n) >= len(w_p) else prev_words[-1]
                prefix_part = ' '.join(prev_words[:-1])
                suffix_part = ' '.join(new_words[overlap_len:])
                return f"{prefix_part} {better_word} {suffix_part}".strip()

    # 2. Single-word exact overlap (k == 1):
    # Only deduplicate if NOT a protected number/intensifier (e.g. 'two', 'three', 'no', 'really')
    # to avoid deleting intentional spoken repetitions.
    if clean_prev[-1] == clean_new[0]:
        cand = clean_prev[-1]
        if cand not in STUTTER_PROTECTED_WORDS:
            deduped_new = ' '.join(new_words[1:])
            return (prev_text.strip() + ' ' + deduped_new).strip() if deduped_new else prev_text.strip()

    # 3. Truncation / boundary stem match (e.g. 'hospitable' vs 'hospitably')
    w_prev = clean_prev[-1]
    w_new = clean_new[0]
    min_l = min(len(w_prev), len(w_new))
    if min_l >= 4:
        pref = _common_prefix_len(w_prev, w_new)
        if pref >= 4 and pref >= min_l - 2:
            better_word = new_words[0] if len(w_new) >= len(w_prev) else prev_words[-1]
            prefix_part = ' '.join(prev_words[:-1])
            suffix_part = ' '.join(new_words[1:])
            return f"{prefix_part} {better_word} {suffix_part}".strip()

        # Shared root morpheme suffix >= 5 chars ('autoregressive' / 'progressive' -> 'gressive')
        common_suf = 0
        for c1, c2 in zip(reversed(w_prev), reversed(w_new)):
            if c1 == c2:
                common_suf += 1
            else:
                break
        if common_suf >= 5 and (common_suf / min_l) >= 0.60:
            better_word = prev_words[-1] if len(w_prev) >= len(w_new) else new_words[0]
            prefix_part = ' '.join(prev_words[:-1])
            suffix_part = ' '.join(new_words[1:])
            return f"{prefix_part} {better_word} {suffix_part}".strip()

    return prev_text.strip() + ' ' + new_text.strip()

def find_energy_trough_cut(audio_pcm, sample_rate=16000, search_window_sec=1.5, frame_ms=25) -> int:
    """Find the frame with lowest RMS energy in the search window to cut during a natural speech dip."""
    n = len(audio_pcm)
    search_samples = int(search_window_sec * sample_rate)
    if n <= search_samples:
        return n
    frame_size = int(frame_ms * sample_rate / 1000)
    if frame_size <= 0:
        return n
    start_idx = n - search_samples
    window = audio_pcm[start_idx:]
    num_frames = len(window) // frame_size
    if num_frames <= 1:
        return n
    frames_2d = window[:num_frames * frame_size].reshape(num_frames, frame_size)
    mean_sq = np.einsum('ij,ij->i', frames_2d, frames_2d) / frame_size
    min_j = int(np.argmin(mean_sq))
    return start_idx + (min_j * frame_size) + (frame_size // 2)

def trim_trailing_silence(audio_pcm, sample_rate=16000, frame_len_ms=25, silence_thresh=0.004, min_speech_cushion_ms=250):
    """
    Trim trailing room silence from the end of an audio buffer using Dynamic VAD Energy Decay.
    Preserves quiet unvoiced trailing consonants (t, d, p, k, s, board, on) via dynamic decay thresholding
    and zero-crossing rate turbulence analysis, with a safe 250ms speech cushion.
    Optimized with NumPy SIMD vectorization over contiguous frame views.
    """
    if audio_pcm is None or len(audio_pcm) == 0:
        return audio_pcm
        
    if isinstance(audio_pcm, np.ndarray) and audio_pcm.dtype == np.float32 and audio_pcm.ndim == 1:
        flat = audio_pcm
    else:
        flat = np.asarray(audio_pcm, dtype=np.float32).ravel()
    n = len(flat)
    frame_size = int(frame_len_ms * sample_rate / 1000)
    if frame_size <= 0 or n < frame_size:
        return flat
        
    cushion_size = int(min_speech_cushion_ms * sample_rate / 1000)
    decayed_thresh_sq = (silence_thresh * 0.35) ** 2
    silence_thresh_sq = silence_thresh * silence_thresh
    peak_thresh = silence_thresh * 2.0
    
    # Vectorized 2D frame evaluation
    num_frames = (n - 1) // frame_size
    if num_frames <= 0:
        return flat
        
    slice_len = num_frames * frame_size
    # frames_2d shape: (num_frames, frame_size), covering contiguous samples [n - slice_len : n]
    frames_2d = flat[n - slice_len : n].reshape(num_frames, frame_size)
    
    # 1. RMS Energy calculation (mean of squares using vector SIMD)
    mean_sq = np.einsum('ij,ij->i', frames_2d, frames_2d) / frame_size
    
    # 2. Peak calculation (fast per-frame max/min)
    peak = np.maximum(np.max(frames_2d, axis=1), -np.min(frames_2d, axis=1))
    
    # Fast path: detect frames with definite speech energy (high RMS or peak)
    high_energy = (mean_sq >= silence_thresh_sq) | (peak >= peak_thresh)
    high_indices = np.nonzero(high_energy)[0]
    last_frame_j = high_indices[-1] if len(high_indices) > 0 else -1
    
    # Check trailing candidate frames after last_frame_j for quiet unvoiced consonants via ZCR
    if last_frame_j < num_frames - 1:
        start_idx = last_frame_j + 1
        cand_mask = (~high_energy[start_idx:]) & (mean_sq[start_idx:] >= decayed_thresh_sq)
        cand_indices = np.nonzero(cand_mask)[0]
        if len(cand_indices) > 0:
            for rel_idx in reversed(cand_indices):
                j = start_idx + rel_idx
                f = frames_2d[j]
                signs = np.signbit(f)
                zcr = np.mean(signs[1:] ^ signs[:-1])
                if zcr >= 0.15:
                    last_frame_j = j
                    break

    if last_frame_j >= 0:
        i = n - (num_frames - last_frame_j) * frame_size
        last_speech_idx = min(n, i + frame_size + cushion_size)
        if last_speech_idx < n:
            return flat[:last_speech_idx]
            
    return flat

class StreamingMicroBatcher:
    def __init__(self, sample_rate=16000, mode=None, min_chunk_sec=4.5, max_chunk_sec=7.0, silence_thresh=0.015, min_silence_sec=0.25, overlap_sec=0.3, tui=None):
        self.sample_rate = sample_rate
        self.mode = mode or os.environ.get('VT_MICRO_BATCHING', 'auto').lower()
        self.tui = tui
        
        # Configure thresholds based on mode
        if self.mode == 'always' or self.mode == '1' or self.mode == 'true':
            min_chunk_sec = 3.0
            max_chunk_sec = 5.5
        elif self.mode == 'disabled' or self.mode == '0' or self.mode == 'false':
            # Disabled: large limit so it never triggers background chunks
            min_chunk_sec = 999999.0
            max_chunk_sec = 999999.0
            
        self.min_chunk_len = int(min_chunk_sec * sample_rate)
        self.max_chunk_len = int(max_chunk_sec * sample_rate)
        self.silence_thresh = silence_thresh
        self.silence_thresh_sq = silence_thresh * silence_thresh
        self.min_silence_len = int(min_silence_sec * sample_rate)
        self.overlap_len = int(overlap_sec * sample_rate)
        
        self.audio_buffer = []
        self.total_samples = 0
        self.silence_samples = 0
        self.next_chunk_idx = 0
        
        self.chunk_queue = queue.Queue()
        self.results_lock = threading.Lock()
        self.transcribed_chunks = []
        self.accumulated_text = ''
        self.worker_thread = None
        self.running = False
        
    def start(self):
        """Start the background worker"""
        self.audio_buffer = []
        self.total_samples = 0
        self.accumulated_text = ''
        self.silence_samples = 0
        self.transcribed_chunks = []
        self.next_chunk_idx = 0
        self.running = True
        
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        
    def _worker_loop(self):
        while self.running or not self.chunk_queue.empty():
            chunk_data = None
            try:
                try:
                    chunk_data = self.chunk_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                    
                if chunk_data is None:
                    break
                    
                if len(chunk_data) == 5:
                    chunk_index, audio_chunk, is_tail, is_forced, has_overlap = chunk_data
                elif len(chunk_data) == 3:
                    chunk_index, audio_chunk, has_overlap = chunk_data
                    is_tail, is_forced = False, False
                else:
                    chunk_index, audio_chunk = chunk_data[:2]
                    is_tail, is_forced, has_overlap = False, False, True
                
                # Check energy gate: is this chunk purely silence/background noise?
                if isinstance(audio_chunk, np.ndarray) and audio_chunk.dtype == np.float32 and audio_chunk.ndim == 1:
                    flat = audio_chunk
                else:
                    flat = np.asarray(audio_chunk, dtype=np.float32).ravel()
                n = len(flat)
                if n == 0:
                    text = ''
                else:
                    is_inter = not is_tail
                    # Fast speech energy detection: check peak first
                    peak = max(float(np.max(flat)), -float(np.min(flat)))
                    if peak >= 0.015:
                        # Definite speech energy: transcribe chunk directly
                        text = transcribe2.transcribe_audio(audio_data=flat, sample_rate=self.sample_rate)
                        text = clean_hallucinations(text.strip() if text else '', skip_slm=True, is_intermediate=is_inter)
                    else:
                        # Low peak: check RMS energy to confirm silence
                        mean_sq = float(np.dot(flat, flat)) / n
                        if mean_sq < (0.0035 * 0.0035):
                            text = ''
                        else:
                            text = transcribe2.transcribe_audio(audio_data=flat, sample_rate=self.sample_rate)
                            text = clean_hallucinations(text.strip() if text else '', skip_slm=True, is_intermediate=is_inter)
                    if is_inter and is_forced and text:
                        text = text.rstrip('. \t\r\n')
                
                with self.results_lock:
                    self.transcribed_chunks.append((chunk_index, text, has_overlap))
            except Exception as e:
                print(f'Micro-batch worker error: {e}')
            finally:
                if chunk_data is not None:
                    try:
                        self.chunk_queue.task_done()
                    except:
                        pass

    def feed_audio(self, pcm_chunk):
        """Feed a live incoming block of PCM audio (float32, 16kHz)"""
        if not self.running or pcm_chunk is None:
            return
            
        if isinstance(pcm_chunk, np.ndarray) and pcm_chunk.dtype == np.float32 and pcm_chunk.ndim == 1:
            flat = pcm_chunk
        else:
            flat = np.asarray(pcm_chunk, dtype=np.float32).ravel()
        n_samples = len(flat)
        if n_samples == 0:
            return

        self.audio_buffer.append(flat)
        self.total_samples += n_samples
        
        # Calculate RMS energy of this block using SIMD dot product
        sum_sq = float(np.dot(flat, flat))
        mean_sq = sum_sq / n_samples
        
        if self.tui:
            rms = np.sqrt(mean_sq)
            self.tui.update_vu_level(float(rms))
            
        if mean_sq < self.silence_thresh_sq:
            self.silence_samples += n_samples
        else:
            self.silence_samples = 0
            
        # Check if we should dispatch a micro-batch chunk
        if self.total_samples >= self.min_chunk_len and self.silence_samples >= self.min_silence_len:
            # The cut already sits in a detected pause. Re-adding the fixed overlap
            # would reach back past the pause into the previous word (overlap_sec 0.3
            # > min_silence_sec 0.25), making the next chunk start mid-word; the ASR
            # then hallucinates the word tail (e.g. "autoregressive" -> "Progressive").
            # Keep only the detected silence, which is clean lead-in context.
            self._dispatch_current_chunk(keep_overlap=True, is_tail=False,
                                         overlap_len=self.silence_samples)
        elif self.total_samples >= self.max_chunk_len:
            self._dispatch_current_chunk(keep_overlap=False, is_tail=False, is_forced=True)

    def _dispatch_current_chunk(self, keep_overlap=False, is_tail=False, overlap_len=None, is_forced=False):
        if not self.audio_buffer:
            return
            
        if len(self.audio_buffer) == 1:
            full_chunk = self.audio_buffer[0]
        else:
            full_chunk = np.concatenate(self.audio_buffer)
        
        # Trim dead silence from trailing tail chunk so the ASR decoder never sees room silence
        if is_tail:
            full_chunk = trim_trailing_silence(full_chunk, sample_rate=self.sample_rate)
            # If previous chunks have already been transcribed and the remaining tail after
            # silence trimming is sub-utterance length (< 0.55s), it is trailing room breath
            # / mic-release residue. Skip dispatching to prevent trailing phantom hallucinations.
            if self.next_chunk_idx > 0 and len(full_chunk) < int(0.55 * self.sample_rate):
                self.audio_buffer = []
                self.total_samples = 0
                return
            chunk_idx = self.next_chunk_idx
            self.next_chunk_idx += 1
            self.chunk_queue.put((chunk_idx, full_chunk, True, False, False))
            self.audio_buffer = []
            self.total_samples = 0
            self.silence_samples = 0
            return
        elif is_forced:
            cut_idx = find_energy_trough_cut(full_chunk, sample_rate=self.sample_rate, search_window_sec=1.5)
            if cut_idx < len(full_chunk):
                rem = full_chunk[cut_idx:].copy()
                full_chunk = full_chunk[:cut_idx]
                self.audio_buffer = [rem]
                self.total_samples = len(rem)
                self.silence_samples = 0
                chunk_idx = self.next_chunk_idx
                self.next_chunk_idx += 1
                self.chunk_queue.put((chunk_idx, full_chunk, False, True, False))
                return
            
        chunk_idx = self.next_chunk_idx
        self.next_chunk_idx += 1
        
        if keep_overlap:
            eff_overlap = self.overlap_len if overlap_len is None else min(self.overlap_len, overlap_len)
        else:
            eff_overlap = 0
        if keep_overlap and eff_overlap > 0 and len(full_chunk) > eff_overlap:
            overlap_data = full_chunk[-eff_overlap:].copy()
            self.audio_buffer = [overlap_data]
            self.total_samples = len(overlap_data)
            self._last_overlap_samples = len(overlap_data)
            has_speech_overlap = (overlap_len is None)
        else:
            self.audio_buffer = []
            self.total_samples = 0
            self._last_overlap_samples = 0
            has_speech_overlap = False
            
        self.chunk_queue.put((chunk_idx, full_chunk, False, False, has_speech_overlap))
        self.silence_samples = 0

    def finish_and_get_text(self, skip_slm: bool = True) -> str:
        """
        Stop background worker loop, join worker thread, stitch all audio micro-chunks
        chronologically, and execute final cleaning pass.
        """
        # Dispatch any trailing audio in buffer
        if self.audio_buffer:
            self._dispatch_current_chunk(keep_overlap=False, is_tail=True)
            
        self.running = False
        
        # Put sentinel None to unblock worker loop
        self.chunk_queue.put(None)
        
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=25.0)
            
        with self.results_lock:
            # Sort by chunk index and stitch full transcript with overlap deduplication
            self.transcribed_chunks.sort(key=lambda x: x[0])
            
            raw_full = ''
            for item in self.transcribed_chunks:
                if len(item) == 3:
                    _, t, has_overlap = item
                else:
                    _, t = item[:2]
                    has_overlap = True
                if not t:
                    continue
                raw_full = deduplicate_text_overlap(raw_full, t, has_speech_overlap=has_overlap)
                
            raw_full = re.sub(r"\s+([.,!?;:])", r"\1", raw_full)
            raw_full = re.sub(r"([.!?])\s*\1+", r"\1", raw_full)
            
            # Execute final cleaning pass (skip_slm=True for instant ASR, skip_slm=False for vLLM SLM)
            full_text = clean_hallucinations(raw_full, skip_slm=skip_slm, is_intermediate=False)
            
        return full_text
