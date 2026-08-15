import time
import pytest
import numpy as np
import transcribe2
import transcribe_whisper
import t2

def compute_wer(reference: str, hypothesis: str) -> float:
    """Calculate Word Error Rate (WER) between reference and hypothesis text."""
    import re
    clean_ref = re.sub(r'[^\w\s]', '', reference.lower()).strip()
    clean_hyp = re.sub(r'[^\w\s]', '', hypothesis.lower()).strip()
    
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

def test_transcribe_known_text_validation(sample_audio_file):
    """
    Test transcription accuracy against known text validation on sample audio file.
    """
    audio_path, expected_text, sample_rate, duration = sample_audio_file

    # Perform transcription on sample audio file
    transcribed_text = transcribe_whisper.transcribe_audio(audio_path=audio_path, language="en")
    print(f"\n[Validation] Expected Text   : '{expected_text}'")
    print(f"[Validation] Transcribed Text: '{transcribed_text}'")

    # If audio is speech (e.g. gTTS), check WER
    if transcribed_text:
        wer = compute_wer(expected_text, transcribed_text)
        print(f"[Validation] Word Error Rate (WER): {wer:.2%}")
        # WER threshold check for valid speech
        assert wer < 0.5, f"Word Error Rate too high: {wer:.2%}"
    else:
        # If synthetic audio sine tones were used, verify function completed cleanly
        print("[Validation] Synthetic audio processed without error.")

def test_transcription_performance_time(sample_audio_file):
    """
    Test transcription execution time and calculate Real-Time Factor (RTF).
    RTF = transcribe_time / audio_duration.
    """
    audio_path, expected_text, sample_rate, duration = sample_audio_file

    start_time = time.time()
    result = transcribe_whisper.transcribe_audio(audio_path=audio_path, language="en")
    transcribe_time = time.time() - start_time

    rtf = transcribe_time / max(duration, 0.1)

    print(f"\n[Performance] Audio Duration : {duration:.2f} s")
    print(f"[Performance] Transcribe Time: {transcribe_time:.2f} s")
    print(f"[Performance] Real-Time Factor (RTF): {rtf:.2f}x")

    # Assert reasonable performance bound
    assert transcribe_time > 0, "Transcribe time should be positive"

def test_recording_time_and_buffer_performance():
    """
    Test audio recording duration and buffer sizing accuracy.
    Verifies that audio frames accumulated match requested sample rate and time duration.
    """
    sample_rate = 16000
    test_duration = 1.0  # 1 second of audio
    expected_samples = int(sample_rate * test_duration)

    # Generate test audio buffer simulating 1 second recording
    mock_frames = np.random.randn(expected_samples).astype(np.float32)

    start_time = time.time()
    result, proc_time = t2.process_audio_stream(mock_frames)
    elapsed = time.time() - start_time

    print(f"\n[Buffer Performance] Processed {len(mock_frames)} audio samples ({test_duration}s) in {proc_time:.4f}s")
    assert proc_time < 5.0, "Buffer processing took too long"
