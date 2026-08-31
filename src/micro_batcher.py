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

from post_processor import clean_speech_transcription, process_slm_llm_stream_concat

def clean_hallucinations(text, skip_slm=False):
    return clean_speech_transcription(text, skip_slm=skip_slm)

def deduplicate_text_overlap(prev_text: str, new_text: str) -> str:
    """
    Deduplicates overlapping word sequences between consecutive micro-batch transcripts.
    e.g. ('speech recognition have evolved', 'have evolved significantly over') -> 'speech recognition have evolved significantly over'
    """
    if not prev_text:
        return new_text or ''
    if not new_text:
        return prev_text or ''

    prev_words = prev_text.strip().split()
    new_words = new_text.strip().split()

    if not prev_words:
        return ' '.join(new_words)
    if not new_words:
        return ' '.join(prev_words)

    max_overlap = min(8, len(prev_words), len(new_words))
    
    # Pre-normalize candidate overlap slices once
    prev_suffix = [w.lower().strip(',.!?') for w in prev_words[-max_overlap:]]
    new_prefix = [w.lower().strip(',.!?') for w in new_words[:max_overlap]]

    best_overlap_len = 0
    for overlap_len in range(max_overlap, 0, -1):
        if prev_suffix[-overlap_len:] == new_prefix[:overlap_len]:
            best_overlap_len = overlap_len
            break

    if best_overlap_len > 0:
        deduped_new = ' '.join(new_words[best_overlap_len:])
        return (prev_text.strip() + ' ' + deduped_new).strip() if deduped_new else prev_text.strip()
    
    return prev_text.strip() + ' ' + new_text.strip()

def trim_trailing_silence(audio_pcm, sample_rate=16000, frame_len_ms=25, silence_thresh=0.004, min_speech_cushion_ms=250):
    """
    Trim trailing room silence from the end of an audio buffer using Dynamic VAD Energy Decay.
    Preserves quiet unvoiced trailing consonants (t, d, p, k, s, board, on) via dynamic decay thresholding
    and zero-crossing rate turbulence analysis, with a safe 250ms speech cushion.
    Optimized with NumPy SIMD vectorization over contiguous frame views.
    """
    if audio_pcm is None or len(audio_pcm) == 0:
        return audio_pcm
        
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
    mean_sq = np.sum(frames_2d * frames_2d, axis=1) / frame_size
    
    # 2. Peak calculation
    peak = np.maximum(np.max(frames_2d, axis=1), -np.min(frames_2d, axis=1))
    
    # 3. Zero-Crossing Rate (ZCR) for quiet unvoiced consonants
    signs = np.signbit(frames_2d)
    crossings = signs[:, 1:] ^ signs[:, :-1]
    zcr = np.mean(crossings, axis=1)
    
    # Match high energy speech OR peak OR quiet unvoiced trailing consonants with ZCR turbulence
    speech_mask = (mean_sq >= silence_thresh_sq) | (peak >= peak_thresh) | ((mean_sq >= decayed_thresh_sq) & (zcr >= 0.15))
    
    speech_indices = np.nonzero(speech_mask)[0]
    if len(speech_indices) > 0:
        last_frame_j = speech_indices[-1]
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
        self.running = True
        
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        
    def _worker_loop(self):
        while self.running or not self.chunk_queue.empty():
            chunk_data = None
            try:
                try:
                    chunk_data = self.chunk_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                    
                if chunk_data is None:
                    break
                    
                chunk_index, audio_chunk = chunk_data
                
                # Check energy gate: is this chunk purely silence/background noise?
                flat = np.asarray(audio_chunk, dtype=np.float32).ravel()
                n = len(flat)
                if n == 0:
                    text = ''
                else:
                    peak = max(float(np.max(flat)), -float(np.min(flat)))
                    mean_sq = float(np.dot(flat, flat)) / n
                    if peak < 0.015 and mean_sq < (0.0035 * 0.0035):
                        text = ''
                    else:
                        # Transcribe chunk with speech signal
                        text = transcribe2.transcribe_audio(audio_data=flat, sample_rate=self.sample_rate)
                        text = clean_hallucinations(text.strip() if text else '', skip_slm=True)
                
                with self.results_lock:
                    self.transcribed_chunks.append((chunk_index, text))
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
        if not self.running or pcm_chunk is None or len(pcm_chunk) == 0:
            return
            
        flat = np.asarray(pcm_chunk, dtype=np.float32).ravel()
        n_samples = len(flat)
        if n_samples == 0:
            return

        self.audio_buffer.append(flat)
        self.total_samples += n_samples
        
        # Calculate RMS energy of this block using SIMD dot product
        sum_sq = float(np.dot(flat, flat))
        mean_sq = sum_sq / n_samples
        
        if hasattr(self, 'tui') and self.tui:
            rms = np.sqrt(mean_sq)
            self.tui.update_vu_level(float(rms))
            
        if mean_sq < self.silence_thresh_sq:
            self.silence_samples += n_samples
        else:
            self.silence_samples = 0
            
        # Check if we should dispatch a micro-batch chunk
        if (self.total_samples >= self.min_chunk_len and self.silence_samples >= self.min_silence_len) or (self.total_samples >= self.max_chunk_len):
            self._dispatch_current_chunk(keep_overlap=True, is_tail=False)

    def _dispatch_current_chunk(self, keep_overlap=False, is_tail=False):
        if not self.audio_buffer:
            return
            
        if len(self.audio_buffer) == 1:
            full_chunk = self.audio_buffer[0]
        else:
            full_chunk = np.concatenate(self.audio_buffer)
        
        # Trim dead silence from trailing tail chunk so the ASR decoder never sees room silence
        if is_tail:
            full_chunk = trim_trailing_silence(full_chunk, sample_rate=self.sample_rate)
            
        chunk_idx = len(self.transcribed_chunks) + self.chunk_queue.qsize()
        self.chunk_queue.put((chunk_idx, full_chunk))
        
        if keep_overlap and len(full_chunk) > self.overlap_len:
            overlap_data = full_chunk[-self.overlap_len:].copy()
            self.audio_buffer = [overlap_data]
            self.total_samples = len(overlap_data)
        else:
            self.audio_buffer = []
            self.total_samples = 0
            
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
            self.worker_thread.join(timeout=15.0)
            
        with self.results_lock:
            # Sort by chunk index and stitch full transcript with overlap deduplication
            self.transcribed_chunks.sort(key=lambda x: x[0])
            cleaned_texts = [clean_hallucinations(t, skip_slm=True) for _, t in self.transcribed_chunks if t]
            
            raw_full = ''
            for t in cleaned_texts:
                raw_full = deduplicate_text_overlap(raw_full, t)
                
            raw_full = re.sub(r"\s+([.,!?;:])", r"\1", raw_full)
            raw_full = re.sub(r"([.!?])\s*\1+", r"\1", raw_full)
            
            # Execute final cleaning pass (skip_slm=True for instant ASR, skip_slm=False for vLLM SLM)
            full_text = clean_hallucinations(raw_full, skip_slm=skip_slm)
            
        return full_text
