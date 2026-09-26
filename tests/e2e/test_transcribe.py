#!/usr/bin/env python3
"""
Test transcription accuracy against expected results.
Run: python tests/e2e/test_transcribe.py

The ASR backend (Cohere) runs in a subprocess so the torch runtime is isolated
from the test process.
"""
import os
import sys
import re
import glob
import json
import subprocess

test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tests", "test_transcribe")
src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src")
sys.path.insert(0, src_dir)

import numpy as np
import soundfile as sf

BACKENDS = [
    ("Cohere", "cohere"),
]

RESULT_MARKER = "__VT_RESULTS__"


def load_audio(audio_path):
    audio, sr = sf.read(audio_path)
    if sr != 16000:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
    return audio.astype(np.float32)


def transcribe(backend_module, audio):
    """Use transcribe2 like the app does"""
    import transcribe2
    return transcribe2.transcribe_audio(audio_data=audio)


def score_transcription(expected, actual):
    if not actual:
        return 0, "FAIL"

    normal_expected = re.sub(r'[^\w\s]', '', expected.lower()).split()
    normal_actual = re.sub(r'[^\w\s]', '', actual.lower()).split()

    expected_words = set(normal_expected)
    actual_words = set(normal_actual)
    overlap = expected_words & actual_words

    if not normal_expected:
        return 0, "FAIL"

    match_ratio = len(overlap) / len(expected_words)

    if expected.lower().strip() == actual.lower().strip():
        return match_ratio, "PASS"
    elif match_ratio >= 0.7:
        return match_ratio, "PASS"
    elif match_ratio >= 0.4:
        return match_ratio, "PARTIAL"
    else:
        return match_ratio, "FAIL"


def _run_backend(backend_id, backend_name):
    """
    Run all transcriptions for a single backend in THIS process.
    Returns a list of result dicts. Never runs a second ASR backend in the same process.
    """
    import time
    import transcribe2

    transcribe2._backend = None

    test_files = sorted(glob.glob(os.path.join(test_dir, "*.mp3")))
    results = []
    for test_file in test_files:
        test_num = os.path.basename(test_file).replace(".mp3", "")
        md_file = test_file.replace(".mp3", ".md")
        if not os.path.exists(md_file):
            continue
        with open(md_file, "r") as f:
            expected = f.read().strip()

        print(f"Test {test_num}: {expected[:60]}...", flush=True)
        audio = load_audio(test_file)

        start_load = time.time()
        backend = transcribe2.get_backend()
        load_time = time.time() - start_load

        start_transcribe = time.time()
        result = transcribe2.transcribe_audio(audio_data=audio)
        transcribe_time = time.time() - start_transcribe

        score, status = score_transcription(expected, result)
        results.append({
            "test_num": test_num,
            "backend_name": backend_name,
            "score": score,
            "status": status,
            "result": result,
            "load_time": load_time,
            "transcribe_time": transcribe_time,
        })
    return results


def _spawn_backend_subprocess(backend_id, backend_name):
    """Run one backend in a fresh subprocess to isolate ASR library FPU state."""
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_dir + (os.pathsep + existing if existing else "")
    proc = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--backend-json", backend_id],
        capture_output=True, text=True, env=env, timeout=900,
    )
    if proc.returncode != 0:
        # Backend process crashed (e.g. library SIGFPE/SIGSEGV) — report as failures
        stderr_tail = (proc.stderr or "")[-500:]
        print(f"[backend {backend_id} exited with code {proc.returncode}]\n{stderr_tail}", flush=True)
        test_files = sorted(glob.glob(os.path.join(test_dir, "*.mp3")))
        return [{
            "test_num": os.path.basename(t).replace(".mp3", ""),
            "backend_name": backend_name,
            "score": 0.0,
            "status": "FAIL",
            "result": f"(backend process crashed, exit {proc.returncode})",
            "load_time": 0.0,
            "transcribe_time": 0.0,
        } for t in test_files]

    for line in proc.stdout.splitlines():
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER):])
    print(f"[backend {backend_id} produced no results]", flush=True)
    return []


def run_all_tests():
    import time

    test_files = sorted(glob.glob(os.path.join(test_dir, "*.mp3")))

    if not test_files:
        print(f"No test files found in {test_dir}")
        return 1

    print("VT Transcription Test")
    print("=" * 100)

    all_results = []
    for backend_name, backend_id in BACKENDS:
        print(f"\n=== Backend: {backend_name} (isolated subprocess) ===", flush=True)
        all_results.extend(_spawn_backend_subprocess(backend_id, backend_name))

    print(f"\n{'='*100}")
    print("RESULTS TABLE")
    print("=" * 100)
    print(f"{'Test':<6} {'Model':<10} {'Load':<10} {'Transcribe':<12} {'Total':<10} {'Score':<8} {'Status':<8} {'Output'}")
    print(f"{'-'*6} {'-'*10} {'-'*10} {'-'*12} {'-'*10} {'-'*8} {'-'*8} {'-'*40}")

    for r in all_results:
        result_str = r["result"] if r["result"] else "None"
        total_t = r["load_time"] + r["transcribe_time"]
        print(f"{r['test_num']:<6} {r['backend_name']:<10} {r['load_time']:>9.2f}s {r['transcribe_time']:>11.2f}s {total_t:>9.2f}s {r['score']*100:>7.1f}% {r['status']:<8} {result_str}")

    print("=" * 80)

    fail_count = sum(1 for r in all_results if r["status"] == "FAIL")
    pass_count = sum(1 for r in all_results if r["status"] == "PASS")
    skip_count = sum(1 for r in all_results if r["status"] == "SKIP")
    total_count = len(all_results)

    print(f"\nTotal: {pass_count} passed, {skip_count} skipped, {fail_count} failed ({total_count} total)")
    return fail_count == 0


def test_transcription_accuracy():
    """Pytest entrypoint for automated CI and flake testing"""
    assert run_all_tests() is True


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--backend-json":
        backend_id = sys.argv[2]
        backend_name = dict(BACKENDS).get(backend_id, backend_id)
        results = _run_backend(backend_id, backend_name)
        print(RESULT_MARKER + json.dumps(results))
        return 0

    success = run_all_tests()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
