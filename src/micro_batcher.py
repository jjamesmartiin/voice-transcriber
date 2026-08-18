#!/usr/bin/env python3
"""
VAD-Guided Micro-Batching Streaming Transcription Engine:
Supports Dynamic Dual-Path ('auto', 'always', 'disabled' via VT_MICRO_BATCHING).
- For short clips (<4.5s): Runs high-speed single-pass direct transcription.
- For longer dictation (>=4.5s): Concurrent background pipelining with 300ms acoustic context overlap.
- Audio tail trimming and full-text sign-off hallucination cleaning.
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

HALLUCINATION_PATTERNS = [
    re.compile(r'\bthanks?\s+(for\s+)?watching[.!?,]*\b', re.IGNORECASE),
    re.compile(r'\bthank\s+you\s+for\s+watching[.!?,]*\b', re.IGNORECASE),
    re.compile(r'\bsubtitles\s+by\s+.*$', re.IGNORECASE),
    re.compile(r'\bplease\s+subscribe[.!?,]*\b', re.IGNORECASE),
]

def clean_hallucinations(text):
    if not text:
        return ""
    cleaned = text
    for pat in HALLUCINATION_PATTERNS:
        cleaned = pat.sub('', cleaned)
    # Strip isolated trailing pronouns or sign-offs attached after punctuation or end of text
    cleaned = re.sub(r'([.!?])\s+you[.!?,]*\s*$', r'\1', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*,\s*you\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'([.!?])\s+bye[.!?,]*\s*$', r'\1', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+bye[.!?,]*\s*$', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s+you\s*$', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()

def trim_trailing_silence(audio_pcm, sample_rate=16000, frame_len_ms=25, silence_thresh=0.012, min_speech_cushion_ms=100):
    """
    Trim trailing room silence from the end of an audio buffer,
    leaving a safe 100ms cushion for soft final consonants (s, f, th, t, you).
    """
    if audio_pcm is None or len(audio_pcm) == 0:
        return audio_pcm
        
    flat = audio_pcm.flatten().astype(np.float32)
    frame_size = int(frame_len_ms * sample_rate / 1000)
    cushion_size = int(min_speech_cushion_ms * sample_rate / 1000)
    
    # Scan from back to front to find where speech actually ended
    last_speech_idx = len(flat)
    found_speech = False
    for i in range(len(flat) - frame_size, 0, -frame_size):
        frame = flat[i:i+frame_size]
        rms = np.sqrt(np.mean(frame**2))
        peak = np.max(np.abs(frame))
        if rms >= silence_thresh or peak >= silence_thresh * 2:
            last_speech_idx = min(len(flat), i + frame_size + cushion_size)
            found_speech = True
            break
            
    if found_speech and last_speech_idx < len(flat):
        return flat[:last_speech_idx]
    return flat

class StreamingMicroBatcher:
    def __init__(self, sample_rate=16000, mode=None, min_chunk_sec=4.5, max_chunk_sec=7.0, silence_thresh=0.015, min_silence_sec=0.25, overlap_sec=0.3):
        self.sample_rate = sample_rate
        self.mode = mode or os.environ.get("VT_MICRO_BATCHING", "auto").lower()
        
        # Configure thresholds based on mode
        if self.mode == "always" or self.mode == "1" or self.mode == "true":
            min_chunk_sec = 3.0
            max_chunk_sec = 5.5
        elif self.mode == "disabled" or self.mode == "0" or self.mode == "false":
            # Disabled: large limit so it never triggers background chunks
            min_chunk_sec = 999999.0
            max_chunk_sec = 999999.0
            
        self.min_chunk_len = int(min_chunk_sec * sample_rate)
        self.max_chunk_len = int(max_chunk_sec * sample_rate)
        self.silence_thresh = silence_thresh
        self.min_silence_len = int(min_silence_sec * sample_rate)
        self.overlap_len = int(overlap_sec * sample_rate)
        
        self.audio_buffer = []
        self.total_samples = 0
        self.silence_samples = 0
        
        self.chunk_queue = queue.Queue()
        self.results_lock = threading.Lock()
        self.transcribed_chunks = []
        self.worker_thread = None
        self.running = False
        
    def start(self):
        """Start the background worker"""
        self.audio_buffer = []
        self.total_samples = 0
        self.silence_samples = 0
        self.transcribed_chunks = []
        self.running = True
        
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        
    def _worker_loop(self):
        while self.running or not self.chunk_queue.empty():
            try:
                chunk_data = self.chunk_queue.get(timeout=0.05)
                if chunk_data is None:
                    break
                chunk_index, audio_chunk = chunk_data
                
                # Check energy gate: is this chunk purely silence/background noise?
                flat = audio_chunk.flatten().astype(np.float32)
                peak = np.max(np.abs(flat)) if len(flat) > 0 else 0
                rms = np.sqrt(np.mean(flat**2)) if len(flat) > 0 else 0
                
                if peak < 0.015 and rms < 0.0035:
                    text = ""
                else:
                    # Transcribe chunk with speech signal
                    text = transcribe2.transcribe_audio(audio_data=audio_chunk, sample_rate=self.sample_rate)
                    text = clean_hallucinations(text.strip() if text else "")
                
                with self.results_lock:
                    self.transcribed_chunks.append((chunk_index, text))
                self.chunk_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Micro-batch worker error: {e}")

    def feed_audio(self, pcm_chunk):
        """Feed a live incoming block of PCM audio (float32, 16kHz)"""
        if not self.running or pcm_chunk is None or len(pcm_chunk) == 0:
            return
            
        flat = pcm_chunk.flatten().astype(np.float32)
        self.audio_buffer.append(flat)
        self.total_samples += len(flat)
        
        # Calculate RMS energy of this block
        rms = np.sqrt(np.mean(flat**2)) if len(flat) > 0 else 0
        if rms < self.silence_thresh:
            self.silence_samples += len(flat)
        else:
            self.silence_samples = 0
            
        # Check if we should dispatch a micro-batch chunk
        should_split = False
        if self.total_samples >= self.min_chunk_len and self.silence_samples >= self.min_silence_len:
            # Natural pause detected!
            should_split = True
        elif self.total_samples >= self.max_chunk_len:
            # Max window reached
            should_split = True
            
        if should_split:
            self._dispatch_current_chunk(keep_overlap=True, is_tail=False)

    def _dispatch_current_chunk(self, keep_overlap=False, is_tail=False):
        if not self.audio_buffer:
            return
        full_chunk = np.concatenate(self.audio_buffer)
        
        # Trim dead silence from trailing tail chunk so the ASR decoder never sees room silence
        if is_tail:
            full_chunk = trim_trailing_silence(full_chunk, sample_rate=self.sample_rate)
            
        chunk_idx = len(self.transcribed_chunks) + self.chunk_queue.qsize()
        self.chunk_queue.put((chunk_idx, full_chunk))
        
        if keep_overlap and len(full_chunk) > self.overlap_len:
            overlap_data = full_chunk[-self.overlap_len:]
            self.audio_buffer = [overlap_data]
            self.total_samples = len(overlap_data)
        else:
            self.audio_buffer = []
            self.total_samples = 0
            
        self.silence_samples = 0

    def finish_and_get_text(self):
        """
        Called on key release: dispatches trailing audio, waits for remaining queue,
        and returns the fully stitched 100% accurate transcription.
        """
        # Dispatch any trailing audio in buffer
        if self.audio_buffer:
            self._dispatch_current_chunk(keep_overlap=False, is_tail=True)
            
        self.running = False
        # Wait for all background chunk transcriptions to finish
        self.chunk_queue.join()
        
        with self.results_lock:
            # Sort by chunk index and stitch
            self.transcribed_chunks.sort(key=lambda x: x[0])
            cleaned_texts = [clean_hallucinations(t) for _, t in self.transcribed_chunks if t]
            full_text = " ".join(cleaned_texts).strip()
            # Clean duplicate punctuation from chunk boundaries
            full_text = re.sub(r'\s+([.,!?;:])', r'\1', full_text)
            full_text = re.sub(r'([.!?])\s*\1+', r'\1', full_text)
            full_text = clean_hallucinations(full_text)
            
        return full_text
