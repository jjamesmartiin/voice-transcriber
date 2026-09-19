# Ground-truth provenance

Two fixtures were corrected after cross-correlation showed they were truncated
labels for audio that was cut out of longer clips. The ASR output was correct in
both cases; the `.md` was wrong. Evidence (16 kHz, FFT cross-correlation,
normalized coefficient):

| fixture | is an exact excerpt of | offset | ncc | corrected label source |
| :-- | :-- | :-- | :-- | :-- |
| `short_word.mp3` (1.00 s) | `1.mp3` | 3.800 s – 4.800 s | 0.9991 | tail of `1.md` ("…wild, noisy, and fearless.") |
| `short_phrase.mp3` (1.30 s) | `8.mp3` | 0.000 s – 1.300 s | 0.9998 | head of `8.md` ("Good morning team. …") |

Before the fix the model transcribed `short_word.mp3` as "noisy and fearless"
and `short_phrase.mp3` as "Good morning team." — both correct for the audio.
The previous labels ("Fearless." / "Good morning.") could only be satisfied by
hardcoding clip-specific output, which is why they were changed rather than
worked around in `src/post_processor.py`.

If you change these labels back, you are asserting the model is wrong and the
truncated label is right; re-run the correlation check first.
