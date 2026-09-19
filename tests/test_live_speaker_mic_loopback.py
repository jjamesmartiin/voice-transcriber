#!/usr/bin/env python3
"""
Live Speaker-to-Microphone Acoustic Loopback Integration Test Suite.

Plays reference audio clips through the system speakers while simultaneously recording 
from the system microphone, transcribing the captured audio in real time, and evaluating
accuracy and end-to-end latency.

Supports:
  - 30-second continuous speech test (sample_id='long_30s')
  - Short single-word / phrase pickup tests (sample_id='short_word', 'short_phrase')
  - Standard samples (sample_id='1' through '8')
  - Full suite runner (sample_id='all')
"""

import os
import sys
import time
import glob
import re
import threading
import numpy as np
import soundfile as sf
import sounddevice as sd

src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, src_dir)

from main import SimpleVoiceTranscriber
import t2


from difflib import SequenceMatcher


def list_audio_devices():
    """Print every input/output device with its index."""
    print("Available audio devices:")
    for i, d in enumerate(sd.query_devices()):
        print(f"  [{i:2d}] in={d['max_input_channels']:3d} out={d['max_output_channels']:3d}  {d['name']}")


def resolve_device(spec, kind):
    """Resolve a device spec (None/'default' | index | name substring) to an index.

    ``kind`` is 'input' or 'output'; the device must have channels for it.
    Returns None to mean "use the system default".
    """
    if spec is None or str(spec).strip() == "":
        return None
    s = str(spec).strip()
    if s.lower() in ("default", "none"):
        return None
    if s.isdigit():
        idx = int(s)
        d = sd.query_devices(idx)
        channels = d['max_input_channels'] if kind == 'input' else d['max_output_channels']
        if channels <= 0:
            raise SystemExit(f"Device [{idx}] '{d['name']}' has no {kind} channels")
        return idx
    matches = []
    for i, d in enumerate(sd.query_devices()):
        channels = d['max_input_channels'] if kind == 'input' else d['max_output_channels']
        if channels > 0 and s.lower() in d['name'].lower():
            matches.append(i)
    if not matches:
        raise SystemExit(f"No {kind} device matching {s!r}; use --list to see devices")
    return matches[0]


def device_rate(device):
    """Native sample rate for a device, or None for the system default.

    Hardware (ALSA hw:X) devices only accept their native rate, so playback
    must be resampled to it; the pipewire/default devices accept 16 kHz.
    """
    if device is None:
        return None
    try:
        return int(sd.query_devices(device)['default_samplerate'])
    except Exception:
        return None


def resample_to(data, src_rate, dst_rate):
    """Resample a float32 mono buffer with a polyphase filter."""
    if not dst_rate or dst_rate == src_rate:
        return data
    import scipy.signal
    return scipy.signal.resample_poly(data, dst_rate, src_rate).astype(np.float32)

def _words_match_fuzzy(w1, w2, threshold=0.80):
    if w1 == w2:
        return True
    if len(w1) > 2 and len(w2) > 2 and w1.rstrip('s') == w2.rstrip('s'):
        return True
    return SequenceMatcher(None, w1, w2).ratio() >= threshold

def score_transcription(expected, actual):
    if not actual:
        return 0.0, "FAIL"
    
    normal_expected = re.sub(r'[^\w\s]', '', expected.lower()).split()
    normal_actual = re.sub(r'[^\w\s]', '', actual.lower()).split()
    
    if not normal_expected:
        return 0.0, "FAIL"
        
    matched_count = 0
    actual_pool = list(normal_actual)
    
    for ew in normal_expected:
        match_idx = None
        for i, aw in enumerate(actual_pool):
            if _words_match_fuzzy(ew, aw, threshold=0.80):
                match_idx = i
                break
        if match_idx is not None:
            matched_count += 1
            actual_pool.pop(match_idx)
            
    match_ratio = matched_count / len(normal_expected)
    
    if match_ratio >= 0.80:
        return match_ratio, "PASS"
    elif match_ratio >= 0.50:
        return match_ratio, "PARTIAL"
    else:
        return match_ratio, "FAIL"


def run_single_test(sample_id, transcriber, out_device=None, in_device=None):
    test_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_transcribe")
    audio_path = os.path.join(test_dir, f"{sample_id}.mp3")
    md_path = os.path.join(test_dir, f"{sample_id}.md")
    
    if not os.path.exists(audio_path) or not os.path.exists(md_path):
        print(f"Error: Sample file '{sample_id}' not found in {test_dir}")
        return None

    with open(md_path, 'r') as f:
        expected_text = f.read().strip()

    # Load playback audio
    data, sr = sf.read(audio_path, dtype='float32')
    duration_sec = len(data) / float(sr)

    print("\n" + "-" * 80)
    print(f"🎙️  TESTING SAMPLE: {sample_id} ({duration_sec:.2f}s audio)")
    print(f"EXPECTED: \"{expected_text}\"")
    print("-" * 80)

    # Pin the microphone for this run so the app's stream does not fall back to
    # the session default (which may be a headset, not the room mic).
    t2.INPUT_DEVICE_INDEX = in_device
    t2.PRIMARY_DEVICE_NAME = None
    t2.SECONDARY_DEVICE_NAME = None
    t2.OVERRIDE_MODE = 'auto'

    transcriber.start_recording()
    time.sleep(0.05)  # Fast pre-playback start

    # Play the reference clip out the chosen speaker, independent of the default
    # output device. Hardware devices need their native rate.
    out_rate = device_rate(out_device) or sr
    sd.play(resample_to(data, sr, out_rate), out_rate, device=out_device)
    sd.wait()
    time.sleep(0.35)  # Post-playback room acoustic propagation cushion

    t0_process = time.time()
    transcriber.stop_recording(copy_to_clipboard=True)
    transcriber.process_recording()
    processing_time = time.time() - t0_process

    actual_text = getattr(transcriber, 'last_transcription', '').strip()
    score, status = score_transcription(expected_text, actual_text)

    print(f"CAPTURED: \"{actual_text}\"")
    print(f"RESULT  : {score * 100:.1f}% [{status}] in {processing_time:.2f}s")
    
    return {
        "sample_id": sample_id,
        "duration": duration_sec,
        "expected": expected_text,
        "actual": actual_text,
        "score": score,
        "status": status,
        "latency": processing_time
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Live speaker-to-mic acoustic loopback suite.")
    parser.add_argument("sample", nargs="?", default="long_30s",
                        help="sample id or 'all' (default: long_30s)")
    parser.add_argument("--out", dest="out",
                        default=os.environ.get("VT_LOOPBACK_OUT") or os.environ.get("VT_LOOPBACK_OUTPUT"),
                        help="speaker/output device: index or name substring (default: system default)")
    parser.add_argument("--in", dest="inp",
                        default=os.environ.get("VT_LOOPBACK_IN") or os.environ.get("VT_LOOPBACK_INPUT"),
                        help="microphone/input device: index or name substring (default: system default)")
    parser.add_argument("--list", action="store_true", help="list audio devices and exit")
    args = parser.parse_args()

    if args.list:
        list_audio_devices()
        return

    sample_arg = args.sample
    out_device = resolve_device(args.out, 'output')
    in_device = resolve_device(args.inp, 'input')

    print("\n" + "=" * 80)
    print("🎙️  LIVE SPEAKER-TO-MIC ACOUSTIC LOOPBACK SUITE")
    print("=" * 80)
    print(f"output device: {out_device if out_device is not None else 'system default'}")
    print(f"input device : {in_device if in_device is not None else 'system default'}")

    # Pre-initialize single transcriber instance
    transcriber = SimpleVoiceTranscriber()

    if sample_arg == "all":
        samples_to_run = ["short_word", "short_phrase", "1", "2", "3", "long_30s"]
    else:
        samples_to_run = [sample_arg]

    results = []
    for sid in samples_to_run:
        res = run_single_test(sid, transcriber, out_device=out_device, in_device=in_device)
        if res:
            results.append(res)

    if hasattr(transcriber, 'notification'):
        transcriber.notification.hide()

    print("\n" + "=" * 80)
    print("SUMMARY RESULTS TABLE")
    print("=" * 80)
    print(f"{'Sample ID':<15} | {'Duration':<10} | {'Score':<8} | {'Status':<8} | {'Pipeline Latency'}")
    print("-" * 80)
    for r in results:
        print(f"{r['sample_id']:<15} | {r['duration']:>6.2f}s    | {r['score']*100:>5.1f}%   | {r['status']:<8} | {r['latency']:>6.2f}s")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
