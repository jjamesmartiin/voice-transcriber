#!/usr/bin/env python3
"""
Autonomous Benchmark Harness for Voice Transcriber.
Calculates execution latency (ms), real-time factor (RTF), and verifies test suite health.
Features a hardware Mutex lock file (/tmp/vt_benchmark.lock) to prevent resource contention
and collisions when subagents benchmark concurrently.
"""

import os
import sys
import time
import json
import fcntl
import argparse
import numpy as np
import subprocess

# Ensure src/ is on PYTHONPATH
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

BENCHMARK_TEXT_SAMPLES = [
    "um so six seven zero six seven zero six nine nine six in the afternoon",
    "twenty five people came to the meeting on wednesday at five pm",
    "i think that into in to the room we should go right now for sure",
    "the cost was one hundred and fifty dollars plus tax for two items",
    "is this option a or option b please let me know by tomorrow morning",
    "quick-tap SLM on-demand polish runs a final rule-based clean pass",
    "capitalize words after prepositions like to Alice or on Wednesday",
    "three point one four one five nine is the value of pi approximately",
    "the first second third fourth fifth sixth seventh eighth ninth tenth",
    "wait a second i need one more minute before we start the presentation",
]

LOCK_FILE_PATH = "/tmp/vt_benchmark.lock"

class HardwareLock:
    """Ensures exclusive hardware access during benchmarking across processes/subagents."""
    def __enter__(self):
        self.lock_file = open(LOCK_FILE_PATH, "w")
        fcntl.flock(self.lock_file, fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        fcntl.flock(self.lock_file, fcntl.LOCK_UN)
        self.lock_file.close()

def run_pytest_suite() -> bool:
    """Executes pytest suite to guarantee zero regression on accuracy/correctness."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env = dict(os.environ)
    env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env.get("PYTHONPATH", "") if env.get("PYTHONPATH") else "")
    
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/"],
        cwd=repo_root, capture_output=True, text=True, env=env
    )
    return res.returncode == 0

def benchmark_post_processor() -> float:
    """Measures latency (ms per sample) of the post-processing pipeline."""
    import post_processor
    
    # Warmup
    for text in BENCHMARK_TEXT_SAMPLES[:2]:
        post_processor.clean_speech_transcription(text)
        
    start_time = time.perf_counter()
    iterations = 5
    for _ in range(iterations):
        for text in BENCHMARK_TEXT_SAMPLES:
            post_processor.clean_speech_transcription(text)
            
    total_time = time.perf_counter() - start_time
    avg_ms = (total_time / (iterations * len(BENCHMARK_TEXT_SAMPLES))) * 1000.0
    return round(avg_ms, 3)

def benchmark_buffer_processing() -> float:
    """Measures latency (ms) of buffer audio processing."""
    import t2
    
    sample_rate = 16000
    mock_frames = np.random.randn(int(sample_rate * 1.0)).astype(np.float32)
    
    # Warmup
    t2.process_audio_stream(mock_frames)
    
    latencies = []
    for _ in range(10):
        start = time.perf_counter()
        t2.process_audio_stream(mock_frames)
        latencies.append((time.perf_counter() - start) * 1000.0)
        
    return round(float(np.median(latencies)), 3)

def main():
    parser = argparse.ArgumentParser(description="Voice Transcriber Benchmark Harness")
    parser.add_argument("--agent-tag", type=str, default="Main-Agent", help="Tag identifying the agent running the test")
    args = parser.parse_args()

    # Acquire hardware mutex lock to prevent concurrent test collisions
    with HardwareLock():
        # 1. Verify correctness
        tests_passed = run_pytest_suite()
        if not tests_passed:
            print(json.dumps({
                "agent": args.agent_tag,
                "passed": False,
                "reason": "Pytest unit tests failed",
                "latency_ms": 999999.0,
                "post_processor_ms": 999999.0,
                "buffer_proc_ms": 999999.0
            }))
            sys.exit(1)
            
        # 2. Benchmark latency
        post_proc_ms = benchmark_post_processor()
        buffer_proc_ms = benchmark_buffer_processing()
        
        total_latency_ms = post_proc_ms + buffer_proc_ms
        
        output = {
            "agent": args.agent_tag,
            "passed": True,
            "latency_ms": round(total_latency_ms, 3),
            "post_processor_ms": post_proc_ms,
            "buffer_proc_ms": buffer_proc_ms,
            "samples_evaluated": len(BENCHMARK_TEXT_SAMPLES)
        }
        
        print(json.dumps(output))

if __name__ == "__main__":
    main()
