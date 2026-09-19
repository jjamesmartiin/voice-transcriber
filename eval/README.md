# Voice Transcriber accuracy evaluation

A real, reproducible **WER/CER accuracy set** for the English ASR pipeline,
replacing the meaningless 11-clean-clip smoke test.

- **`build_eval.py`** — builds `data/` (mono 16 kHz float32 WAVs) + `manifest.jsonl`.
- **`score.py`** — runs the **real** pipeline over the manifest and reports WER/CER,
  exact-match rate, silence hallucination rate, and worst offenders.
- **`pinned_ids.json`** — the pinned source-sample ids that make network selection
  reproducible.
- **`data/`** — generated audio. **Never committed** (see `data/.gitignore`, which
  ignores everything except itself).

## Slices and counts (154 clips total)

| slice | n | what it is | source |
|---|---|---|---|
| `clean` | 44 | read speech, 3–10 s | `openslr/librispeech_asr` config `clean`, split `test` |
| `accented` | 22 | non-US English meeting speech, 3–10 s | `edinburghcstr/ami` config `ihm`, split `train` |
| `noisy` | 24 | clean/accented/technical + generated white/pink/babble noise, SNR 5/10/20 dB | derived |
| `long` | 12 | 15–40 s, consecutive utterances from one LibriSpeech speaker concatenated, `ref` = concatenated refs | derived from `librispeech_asr` |
| `silence` | 12 | pure silence / very low-level generated noise, `ref = ""` (hallucination probe) | synthetic |
| `technical` | 28 | generic infra/DevOps/hardware-AI sentences, TTS | `gTTS` (en), synthetic |
| `technical_noisy` | 12 | technical clips + generated noise, SNR 10/20 dB | derived |

Noise is generated locally (white, 1/f "pink", and a 6-voice amplitude-modulated
"babble"). No noise datasets are downloaded.

## Rebuild

```bash
cd /home/jamesm/gitprojects/voice-transcriber
VTOUT=$(nix build .#default --print-out-paths --no-link | tail -1)
PY=$(grep -oE '/nix/store/[^ "]*python3[^ "]*/bin/python' "$VTOUT/bin/vt" | head -1)
PYTHONPATH=$PWD/src "$PY" eval/build_eval.py
```

- Network access to `huggingface.co` is required for the first build (LibriSpeech,
  AMI) and for `gTTS` (technical slice). Everything is written to disk so
  **scoring runs fully offline**.
- Audio is decoded without `torchcodec` by reading raw bytes (`Audio(decode=False)`)
  and decoding with `soundfile`/`librosa`.
- **Reproducibility:** on the first build the script writes `pinned_ids.json`
  (exact LibriSpeech/AMI/LibriSpeech-concat ids, technical sentence indices, and
  the noise plan). Subsequent builds select exactly those ids. Delete
  `pinned_ids.json` (or run `--reset-pins`) to re-select the first N qualifying
  samples deterministically from the streams.
- `--no-network` rebuilds only the synthetic/silence/noise slices (requires the
  source WAVs to already exist).

## Score

```bash
PYTHONPATH=$PWD/src "$PY" eval/score.py                     # all 154 clips
PYTHONPATH=$PWD/src "$PY" eval/score.py --limit 3           # smoke test
PYTHONPATH=$PWD/src "$PY" eval/score.py --slice clean --slice technical
PYTHONPATH=$PWD/src "$PY" eval/score.py --no-int8 --json eval/fp32.json
```

Clips are streamed through the app's real `StreamingMicroBatcher` (energy VAD,
micro-batch cuts, overlap de-duplication, trailing-silence trim) →
`transcribe2`/`transcribe_cohere` (Cohere ASR) →
`post_processor.clean_speech_transcription(skip_slm=True)`. 100 ms audio blocks are
fed as fast as possible (accuracy is unaffected by wall-clock pacing).

Key flags:

| flag | meaning |
|---|---|
| `--blocks-ms N` | feed block size (default 100 ms) |
| `--slice NAME` | restrict to a slice (repeatable) |
| `--ids a,b,c` | restrict to specific clip ids |
| `--limit N` | first N selected clips |
| `--int8` / `--no-int8` | force `VT_INT8_DYNAMIC=1` / `0` (A/B quantisation) |
| `--number-digits` / `--no-number-digits` | override `number_digits` from `config/config.yaml` |
| `--json OUT` | write full per-clip results + aggregates |
| `--verbose` | don't silence model/pipeline stdout |

The app's **real config is honoured**: `number_digits` is read from
`config/config.yaml` (currently `false`) and applied via
`post_processor.set_number_digits_enabled(...)` before any transcription.
`VT_INT8_DYNAMIC` is read by the backend as usual.

## Metrics

- **WER** — corpus word error rate = Σ word-level Levenshtein edits / Σ reference
  words, computed across all clips in a slice (and overall over all speech clips).
- **CER** — corpus character error rate (spaces removed), same aggregation.
- **exact** — fraction of clips whose normalised hypothesis equals the normalised ref.
- **silence hallucination rate** — fraction of `silence` clips with non-empty output.
- Editing is Levenshtein DP implemented in `score.py`; **no extra dependencies**.

Scoring normalisation: lowercase, every non-`[a-z0-9]` character replaced by a
space, whitespace collapsed. This deliberately splits identifiers, paths,
versions, IPs and CIDRs into tokens on both sides
(`server_setup` → `server setup`, `192.168.1.10` → `192 168 1 10`,
`10.0.0.0/24` → `10 0 0 0 24`) so WER measures word accuracy, not punctuation
conventions. The `silence` slice is excluded from WER/CER aggregates (its
reference is empty); it is reported only via the hallucination rate.

## Baseline (reference machine, CPU, `VT_INT8_DYNAMIC=1`)

Produced with `number_digits=false`, 100 ms blocks, `eval/results.json`:

| slice | n | WER | CER | exact | latency |
|---|---|---|---|---|---|
| clean | 44 | 2.81% | 1.43% | 70.45% | 0.84 s |
| accented | 22 | 12.32% | 7.66% | 13.64% | 1.02 s |
| noisy | 24 | 3.01% | 1.24% | 70.83% | 0.85 s |
| long | 12 | 1.78% | 0.87% | 50.00% | 3.27 s |
| technical | 28 | 8.44% | 5.46% | 50.00% | 1.04 s |
| technical_noisy | 12 | 8.12% | 4.52% | 50.00% | 0.86 s |
| **overall (speech)** | **142** | **5.10%** | **2.97%** | **54.23%** | |
| silence | 12 | — | — | — | hallucination **0/12** |

Latency is per-clip wall time (after model load) and is shown only as context;
this harness is about accuracy, not a latency benchmark.

## What this set covers

- Real human read speech (LibriSpeech), non-US English meeting speech (AMI).
- Additive white / pink / babble noise at a range of SNRs.
- Multi-utterance long-form inputs that exercise micro-batch boundaries.
- Technical vocabulary/identifiers (commands, tools, IPs, versions, hex, paths,
  snake/kebab/camel tokens).
- Silence/hallucination probing.

## Honest caveats — what it does NOT cover

- **TTS is not human speech.** The `technical` / `technical_noisy` slices are
  synthesised with gTTS, which pronounces acronyms and tool names
  (`systemctl`, `nginx`, `journalctl`, `0xDEADBEEF`, paths) differently from a
  human. This slice therefore **under-tests the exact jargon errors real
  dictation produces**, even though `ref` is the literal text fed to TTS.
  Treat technical WER as a lower bound on real-world difficulty.
- **The `accented` source is AMI meeting speech**, not a clean accented
  read corpus. Its references contain disfluencies/repetitions that the app's
  post-processor intentionally removes, so its WER is an upper bound / partly a
  post-processing artifact rather than pure acoustic error. `google/fleurs` only
  publishes `en_us` on the Hub — `en_gb`/`en_au`/`en_in` configs do not exist —
  and `mozilla-foundation/common_voice` needs auth, so those were skipped (per the
  "do not fail the build on auth" rule).
- **Not dictation-from-mic.** Audio goes straight into the batcher; the live mic
  capture path, device gain, Bluetooth hands-free mode, and real-time chunking
  jitter are not reproduced.
- **No crosstalk, overlapping speakers, or code-switching.** Single speaker per
  clip (except AMI's natural meeting overlaps are minimal in the headset channel).
- **No music, far-field reverberation, or domain-specific hard accents** beyond
  AMI's speaker pool.
- Noise is synthetic and stationary(ish) — not real cafés, streets, or keyboards.
- The set tests the **ASR + post-processor** path with `skip_slm=True`; the
  optional SLM rewrite pass is not part of this evaluation.
