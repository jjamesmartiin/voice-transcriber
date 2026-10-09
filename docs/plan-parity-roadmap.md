# Roadmap: parity with cloud dictation

**Entry point for this workstream.** Read this first; it says what to do, in what order,
and where the detail lives. Everything here is offline-first: no feature may require a
network call, an account, or telemetry.

> **Resuming after a break?** The actionable, resumable checklist — including current branch
> and worktree state — is [`TODO-parity.md`](TODO-parity.md). Read that first if you want to
> know what to *do*; read this file to know *why*.

## 1. The goal

Close the quality gap to commercial cloud dictation (Wispr Flow measured as the reference)
**without anything leaving the machine** — while keeping the guarantees this app already
makes: every feature switchable, `off` meaning today's behaviour byte-for-byte, and the
release-to-clipboard SLA for the default configuration.

## 2. Where the evidence lives

| What | Where |
| --- | --- |
| How Wispr Flow actually works, measured from its live demo (protocol, latency, formatting behaviour, raw payloads) | `~/gitprojects/pi-browser-benchmark/results/WISPR-FLOW-REPORT.md` and `plans/VOICE-TRANSCRIBER-FEATURE-PARITY.md` |
| The parity acceptance criterion (the four reference fixtures with Wispr's recorded outputs) | [`plan-on-device-formatter.md`](plan-on-device-formatter.md) §4 |
| Measured gaps in this app vs Wispr | [`plan-on-device-formatter.md`](plan-on-device-formatter.md) §3 |
| Model/licence research (why sherpa-onnx; why not pyannote) | [`plan-diarization.md`](plan-diarization.md) §4 |
| The "everything is a toggle" rule and how a setting lands end to end | [`plan-structured-formatting.md`](plan-structured-formatting.md) §11, [`plan-diarization.md`](plan-diarization.md) §6 |

## 3. Workstreams

| # | Workstream | Plan | Status |
| --- | --- | --- | --- |
| **A** | **Structured formatting + error correction** — lists, paragraphs, pause boundaries, retraction resolution, cleanup modes | [`plan-structured-formatting.md`](plan-structured-formatting.md), specs in [`formatting.md`](formatting.md) and [`cleanup_modes.md`](cleanup_modes.md) | **Implementing / largely done** (M1–M5 on `main`) |
| **B** | **Model registry generalisation** — make `model_download.py` support N model artifacts instead of one hardwired Cohere bundle | [`plan-diarization.md`](plan-diarization.md) §6.1 | **Not started — do this first** |
| **C** | **On-device formatter** — a bundled small language model for casing/punctuation/structure parity | [`plan-on-device-formatter.md`](plan-on-device-formatter.md) | **Decided:** ship `superwhisper/s1-mini` (462 MiB Q4 GGUF) behind a **swappable backend registry**. Attribution debt accepted temporarily; an in-house fine-tune is the follow-up. |
| **D** | **Diarization (meeting mode)** — record long, produce a speaker-labelled transcript | [`plan-diarization.md`](plan-diarization.md) | Product decision **made** (meeting mode). Default backend sherpa-onnx; a Cohere-diarize fine-tune is a candidate alternative backend, to be settled once assessed. |
| **E** | **ASR benchmark: Parakeet TDT 0.6B v3 vs Cohere Transcribe** — decide the ASR *before* building more on top of it | §3.1 below | Proposed. **Blocked on a ~490 MB download — deferred while on a hotspot.** |

### 3.1 Workstream E, and a strategic question about the ASR itself

Research surfaced two things that make the ASR choice worth re-opening — it was last settled
against faster-whisper base/small, which is a much weaker field than what exists now.

**Finding 1 — the stack can lose `torch` entirely.** sherpa-onnx has a real Cohere Transcribe
implementation (ONNX, added ~April 2026, present in the nixpkgs-packaged v1.13.3, model
variant `sherpa-onnx-cohere-transcribe-14-lang-int8`). Since sherpa-onnx also provides VAD,
punctuation and diarization, **ASR + VAD + punctuation + diarization can all run on one
onnxruntime-only stack.** That drops the `torch` dependency (~190 MB compressed CPU wheel,
~0.7–1 GB installed) and moves the Cohere weights from 4.13 GB BF16 to a 2.89 GB int8 ONNX —
with *no* change to what the ASR says.

**Finding 2 — a smaller model is plausibly better for this app.** Parakeet TDT 0.6B v3
(CC-BY-4.0) against the current Cohere Transcribe:

| | Cohere Transcribe | Parakeet TDT 0.6B v3 |
| --- | --- | --- |
| Parameters | 2B | 0.6B |
| Ship size | 4.13 GB BF16 / 2.89 GB int8 ONNX | **487 MB int8** |
| English avg WER (Open ASR Leaderboard) | **5.42** | 6.32 (LibriSpeech clean 1.92) |
| CPU speed | not published; torch CPU | **~36× realtime** on a desktop CPU |
| **Native timestamps** | **no** | **yes — word and segment** |
| Licence | Apache-2.0 (repo is HF-gated) | CC-BY-4.0 (ungated) |

That is roughly **6× smaller, far faster on CPU, and ~1 WER point behind** — and it hands us
something the current pipeline has had to design *around*: **word-level timestamps.**

**Why that last row is architectural, not cosmetic.** The diarization plan's central design
decision (`plan-diarization.md` §3) is "diarize first, then transcribe each turn" — chosen
only because Cohere emits no timestamps, so speakers cannot be aligned to words. A
timestamp-capable ASR makes the simpler, better-tested WhisperX-style pipeline viable:
transcribe once, diarize independently, align the two. It also removes the per-turn ASR call
fan-out that §3's design requires.

**The honest caveat:** this is a leaderboard comparison, not our data, and the repo has a
precedent of choosing Cohere *because it won a measurement* — 2.79 % WER against faster-whisper
base/small's 8.75 %/9.38 % on the 154-clip eval. Parakeet is a much stronger contender than
that field was, but the way to settle it is the way that already worked: **add Parakeet as a
third entry in `transcribe2.BACKENDS` and run `eval/score.py`** — the registry exists for
exactly this, and the corpus is already built.

Measure WER/CER **and** wall-clock RTF **and** peak RSS together; accuracy alone would repeat
the mistake the Cohere-vs-whisper comparison avoided by also checking speed.

**Do not start this on a hotspot** — it needs a ~490 MB model download. Everything else in
this roadmap needs no network at all.

## 4. Order, and why

```
A (in flight) ──┐
                ├──► B (model registry) ──┬──► C (formatter)
                │                         └──► D (diarization)
                └─────────────────────────┘
```

1. **B before C or D.** Both C and D ship an extra model artifact, both need the same
   generalisation of `model_download.py` (`ModelSpec` + `models_dir(name)` +
   `ensure_model(name)`), and doing it twice is how the two paths drift. One change, two
   consumers.
2. **C and D are independent** of each other and can be done in either order or in
   parallel by two agents — they touch different modules (`formatter.py` vs `diarize.py`)
   and only share B and the settings-surface checklist.
3. **Neither C nor D may regress A.** Both must leave `off` byte-identical to today, and
   the existing `eval/` baseline (`eval/results.json`) must not get worse.
4. **Both C and D are swappable by construction.** The formatter has a backend registry
   (`plan-on-device-formatter.md` §5.2) and the diarizer names a model through the same
   registry — because the model chosen today is explicitly a first step, not a destination.
5. **Workstream E can run in parallel with anything and is the cheapest way to change the
   app's future** — it is one backend module plus one eval run against a corpus that already
   exists. Doing it *before* C and D means diarization is designed against a known ASR, not
   against an assumption.

## 5. Non-negotiable rules

1. **Off by default, and `off` is byte-identical to today.** Pinned by a test in every
   workstream.
2. **Every feature is a toggle**, reachable from both settings modals and discoverable via
   `help --json`. `help --json` is the catalogue — never hardcode a verb list in docs.
3. **No new network dependency in `src/`.** Weights arrive through the existing
   revision-keyed bundle mechanism, nothing else.
4. **No Hugging Face token, ever, at runtime.** This is why pyannote was rejected for D and
   why only Apache-2.0/MIT components are used.
5. **Everything model-backed is optional and fails open.** A model that is missing, slow or
   wrong must never cost the user their dictation.
6. **The only component allowed to invent text is the formatter, and its output is
   validated** (see [`plan-on-device-formatter.md`](plan-on-device-formatter.md) §8).
7. **The ≤1.5 s SLA applies to the default configuration.** The formatter is the one
   documented, opt-in exemption; report its cost separately in `status`.

## 6. Verification expectations

* Model-free tests in `tests/shared/` for all behaviour — a **fake formatter** and a **fake
  diarizer** implement the same interfaces, so nothing in the shared tier needs weights.
* `eval/` gains a `formatting` slice (input → expected output) and, for D, a DER number
  recorded next to the ASR numbers.
* Anything only verifiable on a real Windows/macOS host gets an entry in `docs/TODO.md`
  naming exactly what is unverified — that is the established convention in this repo.
* Every phase ends with `./scripts/test.sh` green and its own commit, not a mega-merge.

## 7. Deliberately out of scope

* **Live/streaming diarization.** `nvidia/Nemotron-3-Diarization` (OpenMDW-1.1, 8 speakers,
  streaming, CPU via NeMo-Speech.cpp) is the upgrade path if it is ever wanted; noted in
  `plan-diarization.md` §4.5.
* **Active-application context detection** (per-app tone). It changes what the app reads
  from the screen, so it needs its own privacy decision. `formatter_context` stays a manual
  setting until then.
* **Learning from the user's own corrections.** The most powerful privacy-preserving
  feature imaginable and the easiest to get wrong; it needs its own design document.

## 8. Superseded

`docs/cloud_benchmark_plan.md` (a plan to *benchmark* against cloud services) is obsolete —
this roadmap is a *product* plan, and the measurement work lives in the sibling
`pi-browser-benchmark` repo. Safe to delete.
