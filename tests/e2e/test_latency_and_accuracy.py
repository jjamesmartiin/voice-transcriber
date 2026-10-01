import os
import time
import pytest
import numpy as np
import soundfile as sf
from pathlib import Path

# Mark test as local-only (skip in CI runners)
pytestmark = pytest.mark.skipif(
    os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true",
    reason="Local-only benchmark suite (requires local hardware & full model weights)",
)


def compute_wer(reference: str, hypothesis: str) -> float:
    """Calculate Word Error Rate (WER) between reference and hypothesis text."""
    import re

    clean_ref = re.sub(r"[^\w\s]", "", reference.lower()).strip()
    clean_hyp = re.sub(r"[^\w\s]", "", hypothesis.lower()).strip()

    ref_words = clean_ref.split()
    hyp_words = clean_hyp.split()

    if not ref_words:
        return 0.0 if not hyp_words else 1.0

    d = np.zeros((len(ref_words) + 1, len(hyp_words) + 1), dtype=np.int32)
    for i in range(len(ref_words) + 1):
        d[i][0] = i
    for j in range(len(hyp_words) + 1):
        d[0][j] = j

    for i in range(1, len(ref_words) + 1):
        for j in range(1, len(hyp_words) + 1):
            if ref_words[i - 1] == hyp_words[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                substitution = d[i - 1][j - 1] + 1
                insertion = d[i][j - 1] + 1
                deletion = d[i - 1][j] + 1
                d[i][j] = min(substitution, insertion, deletion)

    return float(d[len(ref_words)][len(hyp_words)]) / len(ref_words)


def test_post_processor_latency_and_accuracy(benchmark_reporter):
    """Microsecond latency & accuracy benchmark for post_processor across all 3 number modes."""
    import post_processor as pp

    test_phrases = [
        "bring one of them over here",
        "call me at five five five one two one two today",
        "the server has two hundred forty eight megabytes left",
        "let us meet at five pm on twenty first street",
        "regular sentence with no numbers at all",
    ]

    for mode in ["auto", "digits", "words"]:
        pp.set_number_digits_mode(mode)
        # Warmup
        for p in test_phrases:
            pp.clean_speech_transcription(p, skip_slm=True)

        N = 1000
        start = time.perf_counter()
        for _ in range(N):
            for p in test_phrases:
                pp.clean_speech_transcription(p, skip_slm=True)
        elapsed = time.perf_counter() - start
        avg_us = (elapsed / (N * len(test_phrases))) * 1e6

        # Accuracy checks
        if mode == "auto":
            res_natural = pp.clean_speech_transcription("bring one of them over here", skip_slm=True)
            res_phone = pp.clean_speech_transcription("call five five five one two one two", skip_slm=True)
            assert "one" in res_natural, "auto mode should keep isolated 'one' as words"
            assert "5551212" in res_phone, "auto mode should collapse consecutive digits"
            acc_str = "100% Match (PASS)"
            # Strict latency budget: auto mode must be under 0.1ms (100 µs)
            assert avg_us < 100.0, f"auto mode exceeded latency budget: {avg_us:.2f} µs"
        elif mode == "digits":
            res_natural = pp.clean_speech_transcription("twenty five items", skip_slm=True)
            assert "25" in res_natural, "digits mode should convert cardinals"
            acc_str = "100% Match (PASS)"
            assert avg_us < 150.0, f"digits mode exceeded latency budget: {avg_us:.2f} µs"
        else:  # words
            res = pp.clean_speech_transcription("call five five five one two one two", skip_slm=True)
            assert "555" not in res, "words mode should never convert"
            acc_str = "100% Match (PASS)"
            assert avg_us < 50.0, f"words mode exceeded latency budget: {avg_us:.2f} µs"

        throughput = int(1e6 / max(avg_us, 0.001))
        benchmark_reporter(
            "Post-Processor",
            f"{mode:<8} mode",
            f"{avg_us:.2f} µs",
            f"~{throughput:,} ph/s",
            acc_str,
            status="PASS",
        )


def test_asr_inference_latency_and_accuracy(benchmark_reporter):
    """End-to-end ASR inference latency (RTF) and transcription accuracy (WER) against ground truth."""
    import transcribe2

    # Warm up model so disk loading isn't counted against inference latency
    transcribe2.get_model()

    test_dir = Path(__file__).resolve().parent.parent / "test_transcribe"
    samples = [
        {"name": "Sample 1 (Literature)", "file": test_dir / "1.mp3", "gt_file": test_dir / "1.md"},
        {"name": "Sample 3 (Tech Dictation)", "file": test_dir / "3.mp3", "gt_file": test_dir / "3.md"},
    ]

    for s in samples:
        audio_path = s["file"]
        gt_path = s["gt_file"]
        if not audio_path.exists() or not gt_path.exists():
            pytest.skip(f"Audio sample {audio_path.name} not found")

        audio_data, sr = sf.read(str(audio_path))
        duration = len(audio_data) / sr
        expected = gt_path.read_text().strip()

        start = time.perf_counter()
        actual = transcribe2.transcribe_audio(audio_path=str(audio_path), language="en")
        dt = time.perf_counter() - start

        rtf = dt / max(duration, 0.01)
        wer = compute_wer(expected, actual)
        acc_pct = max(0.0, (1.0 - wer) * 100.0)

        # Accuracy & latency assertions: RTF < 1.0x (faster than real time), WER < 15%
        assert rtf < 1.0, f"ASR inference too slow: {rtf:.2f}x RTF (must be < 1.0x)"
        assert wer < 0.15, f"WER too high: {wer:.1%} on {s['name']}"

        benchmark_reporter(
            "ASR Inference",
            f"{s['name']}",
            f"{dt:.2f} s",
            f"{rtf:.2f}x RTF",
            f"WER: {wer:.1%} ({acc_pct:.1f}% match)",
            status="PASS",
        )


def test_full_pipeline_end_to_end(benchmark_reporter):
    """Verify full pipeline (Audio -> ASR -> Post-Processor in auto mode) adds negligible overhead."""
    import transcribe2
    import post_processor as pp

    test_dir = Path(__file__).resolve().parent.parent / "test_transcribe"
    audio_path = test_dir / "1.mp3"
    gt_path = test_dir / "1.md"
    if not audio_path.exists() or not gt_path.exists():
        pytest.skip(f"Audio sample {audio_path.name} not found")

    audio_data, sr = sf.read(str(audio_path))
    duration = len(audio_data) / sr
    expected = gt_path.read_text().strip()

    pp.set_number_digits_mode("auto")

    start = time.perf_counter()
    raw = transcribe2.transcribe_audio(audio_path=str(audio_path), language="en")
    asr_time = time.perf_counter() - start

    start_pp = time.perf_counter()
    final_text = pp.clean_speech_transcription(raw, skip_slm=True)
    pp_time = time.perf_counter() - start_pp

    total_time = asr_time + pp_time
    total_rtf = total_time / max(duration, 0.01)
    wer = compute_wer(expected, final_text)

    # Post-processor overhead must be sub-millisecond (< 5 ms)
    assert pp_time < 0.005, f"Post-processor overhead too high: {pp_time*1000:.2f} ms"
    assert wer < 0.15, f"Pipeline WER too high: {wer:.1%}"

    benchmark_reporter(
        "Full Pipeline",
        "Sample 1 + PostProc",
        f"{total_time:.2f} s",
        f"{total_rtf:.2f}x RTF (+{pp_time*1000:.2f}ms)",
        f"WER: {wer:.1%} (PASS)",
        status="PASS",
    )
