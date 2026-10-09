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
cd /path/to/voice-transcriber    # the repo root
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
| `--no-skip-slm` | run the optional SLM rewrite pass (default: skip it) |

The JSON `meta` block records the full configuration and the exact invocation so
a baseline can state how it was produced: `backend`, `int8_dynamic`,
`number_digits` (+ `number_digits_config`), `blocks_ms`, `skip_slm`, `device`,
`git_rev`/`git_dirty`, `config_source` (path + whether it loaded), `manifest`,
`invocation` (argv), `env` (the relevant environment variables, verbatim), and
`reproduce` (a single copy-pasteable shell line). Extra keys are additive; older
result JSON files that predate them still load.

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

Regenerated 2026-10-09 with `number_digits=false`, `skip_slm=True`, 100 ms blocks,
all 154 clips, `eval/results.json`. The exact command and all settings are recorded
in `results.json` → `meta` (`backend`, `int8_dynamic`, `number_digits`, `blocks_ms`,
`skip_slm`, `git_rev`, `config_source`, `invocation`, `env`, `reproduce`), so the
baseline is self-describing rather than depending on this paragraph:

```bash
nix develop --command env PYTHONPATH=$PWD/src VT_INT8_DYNAMIC=1 \
  python eval/score.py --backend cohere --int8 --no-number-digits \
  --blocks-ms 100 --json eval/results.json
```

| slice | n | WER | CER | exact | latency |
|---|---|---|---|---|---|
| clean | 44 | 2.07% | 0.72% | 81.82% | 0.42 s |
| accented | 22 | 9.42% | 3.65% | 36.36% | 0.41 s |
| noisy | 24 | 2.73% | 1.58% | 75.00% | 0.38 s |
| long | 12 | 2.19% | 0.75% | 33.33% | 1.54 s |
| technical | 28 | 4.52% | 2.07% | 82.14% | 0.45 s |
| technical_noisy | 12 | 0.64% | 0.25% | 91.67% | 0.48 s |
| **overall (speech)** | **142** | **3.57%** | **1.43%** | **70.42%** | |
| silence | 12 | — | — | — | hallucination **0/12** |

> The previous baseline recorded **5.10%** WER with `git_rev` unrecorded. It no longer
> reproduced: 85 of 154 hypotheses changed with the refs identical, because the app
> (post-processor / chunker) improved underneath it. The table above is the
> current, reproducing number. See `docs/TODO-parity.md` §4E.
>
> `normalize()` used to call `post_processor.set_number_digits_enabled(True)` and
> never restore it, so in a `number_digits=false` run only clip 1 was actually
> transcribed with number words; the rest ran in digits mode. Fixed 2026-10-09
> (`convert_number_words_to_digits(..., mode="digits")` is now applied locally and
> the scorer writes no product state). 17 of 154 hypotheses changed -- `3`→`three`,
> `2`→`two`, `1st`→`first`, `16th`→`sixteenth` -- with overall WER unchanged at
> 3.57% (the scorer normalises both sides), CER 1.42%→1.43% and exact
> 69.72%→70.42%. The `long` slice did not move. Pinned by
> `tests/shared/test_eval_score.py`.

Latency is per-clip wall time (after model load) and is shown only as context;
this harness is about accuracy, not a latency benchmark.

## Review report (human-verifiable, no model run)

`review_report.py` turns a recorded results JSON + the manifest into a single
self-contained page for checking **what the audio was** against **what was
transcribed**: every clip gets an inline `<audio>` player (referenced by relative
path — the WAVs are not embedded), the reference, the hypothesis, a word-level
diff, and per-clip WER, plus a per-slice roll-up.

Regenerate it from the recorded results **without re-running the model**:

```bash
nix develop --command python eval/review_report.py
```

Inputs default to `eval/results.json` + `eval/manifest.jsonl`; outputs are
`eval/report.html` (committed — the human artifact) and `eval/review.json`
(machine readable, **gitignored**: it is pure derived data whose only inputs are
`results.json` + `manifest.jsonl`, so tracking it would add ~500 KiB of diff
noise to every future results change). Both paths are overridable with
`--out`/`--json`. The diff compares whitespace tokens on a lowercased `[a-z0-9]`
key, so case/punctuation-only differences (which scoring normalisation ignores)
are not flagged.

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
