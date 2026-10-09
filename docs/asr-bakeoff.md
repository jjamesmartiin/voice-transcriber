# ASR bake-off: Parakeet TDT 0.6B v3 vs Cohere Transcribe

Workstream E (`plan-parity-roadmap.md` §3.1, `docs/TODO-parity.md` §4E). The point of
this document is a **measurement and a verdict**, not a feature. Every number below
was measured on this machine on 2026-10-09 with the commands shown; nothing is quoted
from a leaderboard.

**Verdict: keep Cohere Transcribe as the default.** Parakeet is dramatically smaller,
faster and lighter, and it has timestamps Cohere lacks — but it loses on **every**
accuracy slice, by ~2.3 WER points whole-clip and ~6 points through the shipped
pipeline. Accuracy is the metric that matters most for dictation. See
[Verdict](#verdict) for the confidence and the conditions that would change it.

---

## 1. Setup

| | |
| --- | --- |
| Host | AMD Ryzen AI 9 HX PRO 370 (24 logical cores), 54 GiB RAM, Linux (Nix devshell) |
| ASR runtime | `sherpa-onnx` 1.12.25 (`OfflineRecognizer.from_transducer`, `model_type="nemo_transducer"`) and PyTorch/Transformers (Cohere) |
| Parakeet weights | `~/.local/share/vt/models/parakeet/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8/`, CC-BY-4.0 |
| Cohere weights | `models/cohere`, Apache-2.0 (HF-gated upstream, mirrored locally) |
| Eval set | `eval/manifest.jsonl`, 154 clips / 1082.1 s — `clean` 44, `accented` 22, `long` 12, `technical` 28, `noisy` 24, `technical_noisy` 12, `silence` 12 |
| Scoring | `eval/score.py` (WER / CER / exact / silence-hallucination), `number_digits=False`, 100 ms feed blocks |

Parakeet artifact sizes actually on disk (the task brief said `encoder.int8.onnx` was
211,405,824 bytes; the real file is **652,184,281 bytes**, i.e. ~641 MB for the whole
extraction, ~487 MB compressed):

```
encoder.int8.onnx  652,184,281
decoder.int8.onnx   11,845,275
joiner.int8.onnx     6,355,277
tokens.txt               93,939
```

### Code under test

* `src/voice_transcriber/transcribe_parakeet.py` — the backend
  (`transcribe_audio` / `preload_model` plus the `get_model` / `load_model` /
  `unload_model` helpers the app and eval call). `sherpa_onnx` is imported lazily;
  `import voice_transcriber.transcribe_parakeet` pulls in no ASR runtime.
* `src/voice_transcriber/transcribe2.py` — `"parakeet"` added to `BACKENDS`.
  **`DEFAULT_BACKEND` is unchanged (`"cohere"`).**
* `eval/score.py` — new `--backend` flag; the backend is recorded in the JSON it
  writes instead of a hardcoded `"cohere"`. With no flag it uses the app's
  `model_backend` config value through the same `transcribe2.set_backend` path the
  app uses (absent key → shipped default `cohere`), so the no-flag behaviour is
  unchanged.
* `tests/shared/test_transcribe_parakeet.py` — 19 model-free tests + 1 opt-in
  real-model test.

### Exact commands

```bash
# Parakeet, full 154-clip eval, JSON + peak RSS:
RSS_FILE=/tmp/parakeet.rss nix develop --command python /tmp/run_with_rss.py \
    eval/score.py --backend parakeet --json /tmp/parakeet.json

# Cohere at the shipped default (dynamic int8 OFF):
RSS_FILE=/tmp/cohere_default.rss nix develop --command python /tmp/run_with_rss.py \
    eval/score.py --json /tmp/cohere_default.json

# Cohere at the int8_dynamic setting the checked-in baseline used:
RSS_FILE=/tmp/cohere_int8.rss nix develop --command python /tmp/run_with_rss.py \
    eval/score.py --int8 --json /tmp/cohere_int8.json
```

`/tmp/run_with_rss.py` is a 20-line wrapper that runs `eval/score.py` via `runpy` and
reads `VmHWM` from `/proc/self/status` at the end. **Peak RSS is the kernel's
high-water mark for the whole eval process** (model load + all 154 clips), written to
a file rather than stderr — `transcribe_cohere` dups fd 2 to `/dev/null` at import,
so an stderr print is swallowed on the Cohere path.

---

## 2. Three axes together

### 2.1 Through the shipped pipeline (StreamingMicroBatcher, 100 ms blocks)

| Metric | Cohere default (bf16) | Cohere `--int8` (dynamic) | **Parakeet** (int8 ONNX) |
| --- | ---: | ---: | ---: |
| **Accuracy** – overall WER | **3.35 %** | 3.57 % | 9.52 % |
| overall CER | **1.40 %** | 1.42 % | 6.84 % |
| exact-match rate | **71.13 %** | 69.72 % | 50.70 % |
| technical id fidelity | **98.28 %** | 94.83 % | 93.10 % |
| silence hallucination | 0.0 % (0/12) | 0.0 % (0/12) | 0.0 % (0/12) |
| **Speed** – RTF (Σdur / Σlatency) | 7.35× | 13.48× | **36.44×** |
| Σ clip latency | 147.2 s | 80.3 s | **29.7 s** |
| per-clip latency mean / median / p95 / max | 0.96 / 0.76 / 2.38 / 4.08 s | 0.52 / 0.44 / 1.44 / 2.72 s | **0.19 / 0.16 / 0.49 / 0.86 s** |
| **Memory** – peak RSS | 4.69 GiB | 22.15 GiB | **1.57 GiB** |

RTF here is the ratio of summed clip duration to summed per-clip `latency_s` recorded
by `score.py` (it excludes model load). Model-load wall time was ~1.2 s for Parakeet,
~0.9 s for Cohere default, and ~3.5 min for Cohere `--int8` (dynamic quantization).

Two things stand out beyond the accuracy gap:

* **Cohere `--int8` is a memory trap.** `_maybe_quantize_dynamic` calls `model.float()`
  on the BF16 checkpoint and then quantizes, so the process needs the float32 model
  **and** the quantized copy at once: **22.15 GiB peak RSS** versus 4.69 GiB for the
  shipped default. It is faster (13.5× vs 7.35×) but also slightly less accurate. The
  shipped default already leaves it off; this measurement is a reason to keep it off.
* **Parakeet is ~5× faster and ~3× lighter** than the shipped Cohere config, in line
  with the ~36× realtime figure the plan cited.

### 2.2 Per-slice WER (pipeline)

| Slice | Cohere default | Cohere `--int8` | **Parakeet** | Parakeet mean lat |
| --- | ---: | ---: | ---: | ---: |
| clean (44) | 2.51 % | 2.22 % | 3.99 % | 0.17 s |
| accented (22) | 9.42 % | 9.42 % | 16.91 % | 0.17 s |
| noisy (24) | 3.01 % | 2.73 % | 4.10 % | 0.15 s |
| technical (28) | 2.66 % | 4.26 % | 5.32 % | 0.19 s |
| technical_noisy (12) | 0.64 % | 0.64 % | 5.10 % | 0.20 s |
| long (12) | 1.78 % | 2.19 % | 16.28 % | 0.62 s |

Cohere wins every speech slice. Parakeet's worst relative losses are `long`
(16.28 % vs 1.78 %) and `accented` (16.91 % vs 9.42 %). The `long` number is much
worse than the model's real ability — see §3.

### 2.3 Whole-clip (micro-batcher bypassed)

Because §3 shows the micro-batcher is confounding Parakeet's score, both backends were
also run feeding each clip whole (`StreamingMicroBatcher` bypassed, `transcribe_audio`
called once per clip; same scoring):

| Metric | Cohere default | Parakeet |
| --- | ---: | ---: |
| overall WER | **2.65 %** | 4.93 % |
| overall CER | **0.77 %** | 2.23 % |
| exact-match rate | **74.6 %** | 58.5 % |
| silence hallucination | 0.0 % | 0.0 % |
| RTF | 8.23× | **36.98×** |
| Σ clip latency | 131.5 s | **29.3 s** |
| per-clip latency median / p95 / max | 0.74 / 2.10 / 2.93 s | **0.16 / 0.55 / 0.91 s** |

Whole-clip per-slice WER:

| Slice | Cohere default | Parakeet |
| --- | ---: | ---: |
| clean | 1.78 % | 2.66 % |
| accented | 7.25 % | 14.98 % |
| noisy | 2.46 % | 3.01 % |
| technical | 2.13 % | 4.26 % |
| technical_noisy | 0.64 % | 2.55 % |
| long | 1.64 % | 3.15 % |

Cohere still wins every slice. The gap narrows from 6.2 to ~2.3 WER points once the
batcher is removed, and Parakeet's `long` score improves from 16.28 % to 3.15 % — i.e.
most of the pipeline `long` penalty is the chunking interaction, not the model.

---

## 3. The chunking interaction (a real finding)

Parakeet's pipeline WER is roughly **double** its whole-clip WER (9.52 % vs 4.93 %).
It is not random: the micro-batcher's energy-trough chunks sometimes make the
sherpa-onnx TDT decoder emit **zero tokens** for a segment that plainly contains
speech.

Evidence, `long-0007` (26.4 s, 69 reference words):

```
whole-clip  : 70 hyp words — full sentence, correct
pipeline    : 33 hyp words — the middle of the sentence is gone
```

The batcher split it into 5 chunks. Transcribing those same 5 chunks with each backend:

| chunk | duration | Cohere | Parakeet |
| --- | ---: | --- | --- |
| 0 | 6.51 s | "Upon the large square … golden moon." | same (19 words) |
| 1 | 5.29 s | "Beams formed … yellow flagstones." | **""** |
| 2 | 6.34 s | "Bragelonne watched … the loud and." | **""** |
| 3 | 5.66 s | "Uncivil slumbers … wearing his." | 14 words |
| 4 | 3.20 s | "His blue and gold … violet suit." | **""** |

The chunk audio is fine — Cohere transcribes all five. On chunk 1 the empty result is
reproducible in a fresh process and independent of `num_threads`, `dither`,
`blank_penalty` and `greedy_search` vs `modified_beam_search`. It is also
**length-sensitive in a non-monotonic way**: prepending silence to chunk 1 rescues it
at 0.05–0.45 s but not at ≥0.5 s, and appending silence does not help; chunks 2 and 4
are not rescued by a 0.1 s pad at all. That is a decoder/segment-boundary behaviour,
not silence and not corrupted audio.

Consequence for a future decision: Parakeet's shipped-pipeline number is pessimistic
by ~2×, and a Parakeet switch would require re-tuning the chunker (whole utterances,
or padded segments) first. That work is in `micro_batcher.py`, which this workstream
does not own; **stop, don't edit — this is the change that file needs.**

---

## 4. Silence — the explicit check

The plan warned sherpa-onnx needed a silence guard because the decoder hallucinates on
silence. **It did not reproduce here.** The 12 `silence` clips (72.2 s total) were
decoded **three** ways:

1. Through the shipped pipeline (as scored above): **0/12 non-empty**.
2. Through `transcribe_parakeet.transcribe_audio` (energy guard applied): **0/12**.
3. **Raw decoder with the guard bypassed** (the honest model-only probe): **0/12**.

The raw table (peak/RMS are the clip's):

```
silence-0000 4.0s peak=0.0000 rms=0.00000 raw=''
silence-0001 5.5s peak=0.0000 rms=0.00000 raw=''
silence-0002 3.0s peak=0.0005 rms=0.00010 raw=''
silence-0003 6.2s peak=0.0000 rms=0.00001 raw=''
silence-0004 6.5s peak=0.0004 rms=0.00010 raw=''
silence-0005 7.0s peak=0.0002 rms=0.00004 raw=''
silence-0006 8.0s peak=0.0000 rms=0.00000 raw=''
silence-0007 4.5s peak=0.0000 rms=0.00001 raw=''
silence-0008 9.0s peak=0.0005 rms=0.00010 raw=''
silence-0009 5.0s peak=0.0002 rms=0.00005 raw=''
silence-0010 3.5s peak=0.0000 rms=0.00000 raw=''
silence-0011 10.0s peak=0.0005 rms=0.00010 raw=''
RAW non-empty: 0/12
```

All peaks are ≤ 0.0005, far below the shared 0.015 guard, and the model returns empty
without it. So Parakeet's silence hallucination rate is **0.0 % (0/12)** on this
corpus, matching Cohere. The guard is still kept in the backend (it mirrors Cohere and
costs nothing), but it is not what produces the zero here — the model does.

---

## 5. Word/segment timestamps — answered with code

`transcribe_with_timestamps()` in `transcribe_parakeet.py` is the function used to
establish this. What the sherpa-onnx result object actually exposes for the NeMo TDT
transducer:

```
result attributes: ['durations', 'emotion', 'event', 'lang', 'segment_durations',
                    'segment_texts', 'segment_timestamps', 'text', 'timestamps',
                    'tokens', 'words', 'ys_log_probs']
  words              = []   # EMPTY for the transducer
  segment_timestamps = []   # EMPTY
  segment_texts      = []   # EMPTY
  segment_durations  = []   # EMPTY
```

So:

* **No native word field and no segment field.** `words`/`segment_*` are empty; those
  exist for other model families (e.g. whisper/punctuation graphs), not this one.
* **Token-level timestamps are exposed and usable.** `tokens` is a BPE word-piece
  stream, and `timestamps[i]` / `durations[i]` are that piece's start and duration in
  seconds (80 ms frame granularity). Lengths match exactly.
* **Word boundaries are reconstructible**, because a word-piece that begins with a
  space starts a new word. Worked example on the bundled `test_wavs/en.wav`:

  ```
  text: "Ask not what your country can do for you, ..."
  tokens:     [' A', 'sk', ' not', ' what', ' your', ' co', 'un', 'tr', 'y', ...]
  timestamps: [ 0.0,  0.24, 0.4,   0.64,  0.8,   0.96,  1.04, 1.12, 1.2, ...]
  words:      [{'word':'Ask','start':0.0,'end':0.40},
               {'word':'not','start':0.40,'end':0.64},
               {'word':'what','start':0.64,'end':0.80},
               {'word':'your','start':0.80,'end':0.96},
               {'word':'country','start':0.96,'end':1.28}, ...]
  ```

  The merging code is `_merge_word_pieces()`; it uses `start + duration` for each
  word's end so the final word gets a real end time (e.g. `country.` ends at 3.76 s on
  a 3.845 s clip) instead of falling back to the clip length.

**Answer:** Parakeet has usable **token timestamps from which word timings are derived**
— not a first-class word/segment API. That is still exactly what
`plan-diarization.md` §3's "diarize first, then transcribe each turn" design lacks
today: with word start/end times, the simpler transcribe-once-then-align pipeline is
viable. The design decision it unblocks is real; the measurement is that Cohere still
wins on accuracy, so the trade-off would have to be made deliberately.

---

## 6. The checked-in baseline is stale

`eval/results.json` records `overall_wer = 0.05099046221570066` with
`int8_dynamic = "1"`. Re-running the **same** configuration on the current branch
(`--int8`) gives **3.57 %**, not 5.10 %. The 154 references are byte-identical; 85 of
154 hypotheses differ. The cause is code drift after the baseline commit
(`b7f9ad4`, 2026-09-18, "optimize micro-batching energy cuts, stem overlap
deduplication, punctuation modes, and the SLM pass").

So the honest comparison is the fresh Cohere run above (3.57 % int8 / 3.35 % default),
not the checked-in 5.10 %. This document uses the fresh numbers. **`eval/results.json`
should be regenerated** (by the owner of that file), otherwise every future comparison
is against a relic.

---

## 7. Verdict

**Keep Cohere Transcribe as the default. Do not switch to Parakeet.**

The three axes, stated plainly:

* **Accuracy: Cohere wins, clearly.** Whole-clip 2.65 % vs 4.93 % WER (Parakeet makes
  ~1.9× the errors); shipped pipeline 3.35 % vs 9.52 %. Cohere wins all six speech
  slices in both modes. The plan predicted Parakeet would be "~1 WER point behind"; on
  this corpus it is ~2.3 points behind whole-clip and ~6 points behind in the shipped
  pipeline. `accented` (14.98 % vs 7.25 %) and `long` (3.15 % vs 1.64 %) are the worst
  slices.
* **Speed: Parakeet wins, decisively.** 36.4× vs 7.35× RTF, and ~5× lower per-clip
  latency (median 0.16 s vs 0.76 s). Both are comfortably below the per-clip figures
  Cohere already ships with, so speed is not the app's current bottleneck.
* **Memory: Parakeet wins, decisively.** 1.57 GiB vs 4.69 GiB peak RSS, and the model
  is 641 MB on disk vs 3.9 GiB. This matters on 8–16 GiB machines, and it is the
  strongest argument for Parakeet.

Parakeet's other two advantages — CC-BY-4.0 ungated weights and token timestamps — are
real but do not outweigh a near-2× error increase on the primary metric. (Note the
Cohere-vs-faster-whisper precedent: this repo chose Cohere *because it won the
measurement*. The same rule applies here.)

**Confidence:** high in the measurement (same corpus, same harness, same day, both
backends measured fresh, references verified identical); medium-high in the
generalisation beyond `eval/` — 154 clips, one accent mix, one host.

**What would change my mind:**

1. A chunker change that feeds Parakeet whole utterances or padded segments and closes
   the whole-clip gap (`long` already improves 16.28 % → 3.15 % when the batcher is
   bypassed; the remaining `accented` gap is the one to watch).
2. If `accented` closed to <1 WER point behind Cohere, Parakeet's size/speed/memory and
   timestamps would make it the better default for a dictation app.
3. A product decision that prioritises a **low-resource mode** (Parakeet is the only
   one that fits comfortably in a small-RAM machine) or **diarization-with-timestamps**
   (Parakeet's timestamps simplify `plan-diarization.md` §3) over the accuracy gap. That
   is a human call, not a measurement.
4. A newer Parakeet checkpoint (v3 is multilingual; an English-tuned variant could close
   the accented/silence gap).

What would **not** change my mind: Cohere `--int8`'s speed. It is faster but both
slower-to-fix and 22 GiB peak RSS, so it is not a path to "make Cohere more like
Parakeet".

---

## Appendix — `transcribe_parakeet` notes

* Lazy `sherpa_onnx` import behind `_get_sherpa()`; `tests/shared/` proves it in a
  subprocess. `transcribe2.set_backend("parakeet")` imports no runtime.
* Model location: `$VT_PARAKEET_MODELS` → `<repo>/models/parakeet` → per-user
  `~/.local/share/vt/models/parakeet`, accepting either the parent or the extraction
  directory. There is **no `parakeet` entry in `model_download.MODELS`** yet, so the
  backend searches rather than calling `models_dir("parakeet")` (adding the spec is the
  model-registry workstream's file, not this one).
* `num_threads` defaults to `min(cpu_count, 8)` (`$VT_PARAKEET_THREADS` overrides).
* `language` is accepted and ignored — the v3 checkpoint auto-detects and the
  `from_transducer` API has no language parameter. No ITN/punctuation rules are wired.
* Silence guard mirrors Cohere via `micro_batcher.has_speech_activity`.
