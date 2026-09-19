# Voice Transcriber — ASR accuracy/speed handoff

**Goal: 100% accurate technical dictation at the best latency.**
Current best measured: **WER 4.22%, CER 2.40%, exact-match 61.3%, silence-hallucination 0/12** on a 154-clip eval set.

---

## 1. Repo + environment

```
cd /home/jamesm/gitprojects/voice-transcriber
VTOUT=$(nix build .#default --print-out-paths --no-link 2>/dev/null | tail -1)
PY=$(grep -oE '/nix/store/[^ "]*python3[^ "]*/bin/python' "$VTOUT/bin/vt" | head -1)
PYTHONPATH=$PWD/src "$PY" <script>
```
- Backend: `cohere` (transformers), **CPU only** (`torch.cuda.is_available()==False`), 24 logical cores.
- Do NOT run `nix run` (it takes over the terminal). Don't edit `flake.nix`/`tui-rs/` unless asked.
- If you use tmux: **only your own named session. NEVER `tmux kill-server`.**

---

## 2. Working tree state (uncommitted — read before changing anything)

| file | state | notes |
|---|---|---|
| `src/transcribe_cohere.py` | int8 dynamic quantization **available but OFF by default** (`VT_INT8_DYNAMIC=1` enables) + a 1×1-conv→Linear patch; CPU threads `min(cpu,8)` | Measured ~20% faster, accuracy ±0.1pp (neutral). Load time +~11s one-time, so it stays opt-in. |
| `src/micro_batcher.py` | `min_chunk_sec=4.5` (the 2.0 change was **reverted**) + an overlap fix on silence cuts | Overlap fix is accuracy-neutral; see §4. |
| `src/post_processor.py` | `shall|will` added to `DANGLING_WORDS_REGEX` | Accuracy-neutral on the eval set. |
| `tests/test_transcribe/short_word.md`, `short_phrase.md` | **corrected fixtures** | They were mislabeled; see `tests/test_transcribe/PROVENANCE.md`. Don't revert. |
| `eval/` | **untracked** eval set + scorer | The main tool. |

Nothing is committed. `src/main.py`, `src/t2.py`, `flake.nix`, `tui-rs/` contain unrelated ratatui-frontend work — leave them alone.

---

## 3. The eval set (use this, not the old 11 clips)

- `eval/build_eval.py` (rebuild), `eval/score.py` (score), `eval/manifest.jsonl`, `eval/README.md`.
- **154 clips**, real speech + augmentation + TTS:
  `clean` 44 (LibriSpeech), `accented` 22 (AMI IHM), `noisy` 24, `long` 12, `silence` 12, `technical` 28 (gTTS), `technical_noisy` 12.
- Metrics: corpus **WER + CER** (Levenshtein), exact-match, **silence hallucination rate**, per-slice + worst offenders.

```bash
PYTHONPATH=$PWD/src "$PY" eval/score.py                       # all 154
PYTHONPATH=$PWD/src "$PY" eval/score.py --no-int8 --json eval/x.json
PYTHONPATH=$PWD/src "$PY" eval/score.py --slice technical --limit 5
```
Baseline artifacts already on disk: `eval/baseline.json`, `eval/E_overlaponly.json`, `eval/A2_overlapfix_int8.json`, `eval/fp32.json`, `eval/current.json`, `eval/D_oldchunk_newpost.json`.

---

## 4. Key measured result: the chunker change was an accuracy regression

Full decomposition (all on the 154-clip set, `--no-int8` unless noted):

| run | chunker | post-proc | int8 | **WER** | CER | exact |
|---|---|---|---|---|---|---|
| C | old (min 4.5, fixed 0.3s overlap) | old | off | **4.22%** | 2.40% | 61.3% |
| D | old | new | off | **4.22%** | 2.40% | 61.3% |
| E | overlap-fix + min **4.5** | new | off | **4.22%** | 2.40% | 61.3% |
| A2 | overlap-fix + min **4.5** | new | **on** | 4.33% | 2.45% | 58.5% |
| B | overlap-fix + min **2.0** | new | off | **5.43%** | 3.20% | 54.2% |
| A | overlap-fix + min **2.0** | new | on | 5.10% | 2.97% | 54.2% |

**Conclusions (evidence-backed):**
1. `min_chunk_sec 4.5 → 2.0` **hurts accuracy**: +1.21pp WER overall; `accented` 6.76% → 13.04% (≈2×). **Keep 4.5.** On the old 11-clip set this looked fine (9/9) — that's exactly why 11 clips was insufficient.
2. The **silence-cut overlap fix is accuracy-neutral** (E == C exactly). Keep it as a correctness fix (it removes the `autoregressive → "Progressive"` mid-word artifact) but it buys no aggregate WER.
3. The **post-processor restart heuristic is a no-op** (D == C exactly). Low value, non-zero risk → prefer removing it.
4. **int8 is ~accuracy-neutral** (±0.1pp; direction flips between runs) for ~20% speed. Keep unless you need every 0.1pp.

---

## 5. Known metric artifacts — fix these BEFORE chasing model accuracy

1. **Number words.** Technical refs are the literal TTS text (`"port eight thousand eighty"`) but the model emits digits (`"port 8080"`) → 3 spurious edits per clip. Either write refs in digit form or normalize numbers in `score.py`. Inflates `technical`/`technical_noisy`.
2. **AMI disfluencies.** `accented` refs contain disfluencies (`"TH YEAH THE SEARCH IS I GUESS..."`); the pipeline *deliberately* cleans disfluencies, so it's penalized for correct product behaviour. Treat `accented` WER as an **upper bound**; consider a disfluency-normalized metric.
3. **Identifier formatting isn't measured.** `score.py` normalizes non-alphanumerics to spaces on both sides (`server_setup` → `"server setup"`), so the custom-dictionary win is invisible. Add a separate identifier-fidelity metric if you target that.

---

## 6. Where the remaining error is

`technical` 9.23% · `technical_noisy` 8.75% · `accented` 6.76% (artifact-inflated) · `clean` 2.22% · `noisy` 2.46% · `long` 1.92%.

## 7. Open levers

**Accuracy (priority):**
- Fix §5 artifacts, then re-baseline — likely large apparent gains with zero code change.
- **Vocabulary-aware correction**: `post_processor.set_custom_dictionary` / `load_custom_dictionary_from_file` already exist. Seeding with the user's real jargon (NixOS, nixos-rebuild, systemctl, journalctl, GitLab, merge request, Kubernetes, Ansible, Terraform, SpamAssassin, Postfix, Dovecot, Altium, PCB, PLC, Pololu, UART, SPI, I2C, carrierCode, exitHook, …) is deterministic and cannot hallucinate. **Most promising technical-accuracy lever.**
- ~~Compare the `whisper` backend on the same set.~~ **Done and rejected**: faster-whisper base.en / small.en scored WER 9.38% / 8.75% (vs Cohere 2.79%) and were slower (748s vs ~340s for 154 clips), so the whisper backend and the `model_backend` option were removed.
- Larger/stronger model, or fine-tuning/adaptation.

**Speed (do not trade accuracy blindly):**
- **ONNX Runtime / OpenVINO** instead of eager PyTorch — untried, commonly 2–4× on CPU transformers. Biggest remaining CPU lever.
- GPU (5–10×) if target machines have one.
- int8 (opt-in, ~20%) and threads `min(cpu,8)`.
- Chunk size: smaller chunks reduce latency but **cost accuracy** (measured). Latency vs accuracy is a real tradeoff here.

**Do NOT**: rewrite in Rust for latency — post-release latency is ~100% ASR inference (Python overhead ~1 ms, measured).

---

## 8. Method rules (non-negotiable)

- **Never game the metric**: no hardcoding clip text into the post-processor, no editing ground truth to match model output. Correcting a mislabeled fixture is allowed only with independent proof (cross-correlation) and must be documented (see `PROVENANCE.md`).
- Measure **per-slice WER/CER on all 154 clips**, not exact-match on a handful.
- For **latency**, use real-time (1× wall-clock) pacing. `eval/score.py` feeds blocks fast, so its `latency` field is **not** post-release latency.
- Benchmark on a **quiet machine**, interleaved A/B in one process, **n≥10**, report median + spread. Never compare numbers across runs taken under different load.
- Add an eval slice for any new failure mode you find.
