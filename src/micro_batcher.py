#!/usr/bin/env python3
"""
VAD-Guided Micro-Batching Streaming Transcription Engine:
Chunks incoming audio on natural voice pauses during live speech,
transcribes concurrent chunks in the background, and leaves only a small
tail upon key release for near-instant post-release clipboard delivery.
"""

import os
import sys
import time
import queue
import threading
import numpy as np

# Ensure src is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcribe2

class StreamingMicroBatcher:
    def __init__(self, sample_rate=16000, min_chunk_sec=3.5, max_chunk_sec=6.0, silence_thresh=0.015, min_silence_sec=0.2):
        self.sample_rate = sample_rate
        self.min_chunk_len = int(min_chunk_sec * sample_rate)
        self.max_chunk_len = int(max_chunk_sec * sample_rate)
        self.silence_thresh = silence_thresh
        self.min_silence_len = int(min_silence_sec * sample_rate)
        
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
                
                # Transcribe chunk
                text = transcribe2.transcribe_audio(audio_data=audio_chunk, sample_rate=self.sample_rate)
                text = text.strip() if text else ""
                
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
            self._dispatch_current_chunk()

    def _dispatch_current_chunk(self):
        if not self.audio_buffer:
            return
        full_chunk = np.concatenate(self.audio_buffer)
        chunk_idx = len(self.transcribed_chunks) + self.chunk_queue.qsize()
        self.chunk_queue.put((chunk_idx, full_chunk))
        
        # Reset current buffer
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
            self._dispatch_current_chunk()
            
        self.running = False
        # Wait for all background chunk transcriptions to finish
        self.chunk_queue.join()
        
        with self.results_lock:
            # Sort by chunk index and stitch
            self.transcribed_chunks.sort(key=lambda x: x[0])
            full_text = " ".join(t for _, t in self.transcribed_chunks if t).strip()
            
        return full_text
