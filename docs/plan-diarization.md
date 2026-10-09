# Plan: Diarization — Meeting Mode (multi-speaker capture)

**Status:** proposal, ready to implement · rewritten 2026-10-08
**Owner:** James
**Supersedes:** the previous revision of this document, which assumed option (1)
("multi-voice dictation convenience") and named `pyannote/speaker-diarization-community-1`
as the model. Both of those are now wrong — see §2 and §4.
**Prerequisite reading:** [`docs/plan-structured-formatting.md`](plan-structured-formatting.md),
[`docs/formatting.md`](formatting.md), [`docs/releasing.md`](releasing.md)

---

## 1. The decision, recorded

**Diarization is a Meeting Mode, not a dictation option.**

Recorded 2026-10-08. You press record (no push-to-talk hotkey, no held key), the app
captures for minutes to hours, and when you stop you get a **speaker-labelled transcript
artifact** — not typed text. This is the only place diarization is reliable, it matches
what every comparable product does (Wispr Flow ships diarization only in its separate
"Notetaker" meeting product, never in dictation), and it removes the two hardest
requirements the previous revision was carrying:

* no sub-second latency budget (it is a batch job; §7),
* no text-injection path (the output is a file, not keystrokes; §8).

Everything below follows from that decision.

## 2. Measured starting point

* **The ASR backend cannot diarize and cannot give timestamps.** `CohereLabs/cohere-transcribe-03-2026`
  (`src/voice_transcriber/model_download.py:41`) is audio-in/text-out. Cohere's own docs
  state, verbatim: *"Timestamps/speaker diarization: The model does not feature either of
  these."* The model card repeats it.
* **But the architecture was built for it.** `config.json` sets
  `prompt_defaults = {"diarize": "<|nodiarize|>", "timestamp": "<|notimestamp|>"}`, and the
  tokenizer defines `<|diarize|>`, `<|spkchange|>`, `<|spk0|>`–`<|spk15|>`,
  `<|spltoken0|>`–`<|spltoken33|>`. The tokens are **inert in the released weights**. Do not
  build on them; do not expect prompting to work.
* **A community fine-tune does diarize in one pass**: `syvai/cohere-transcribe-diarize`
  (Apache-2.0) extends the vocab with speaker + timestamp tokens and emits labels directly.
  It is a genuine option (§4.4) but it is one person's fine-tune with a 30 s window cap and
  its own stitching model — treat it as an experiment, not the shipping default.
* **Nothing in this repo mentions diarization** outside this plan and the structured-formatting
  plan that deferred it here.

## 3. What "speaker labels" actually requires

Two sub-problems, and they are easy to conflate:

1. **Segmentation + clustering** — who spoke when. This is what a diarizer does. It returns
   `(start, end, speaker_id)` intervals and needs no ASR at all.
2. **Attribution** — which *words* belong to which speaker. This normally needs word-level
   timestamps, which our ASR does not produce.

The previous revision of this plan quietly assumed (2) would be solved by timestamp
alignment. It cannot be, without adding a forced-alignment model per language.

**Solution, and the central design decision of this plan: diarize first, then transcribe each
turn.** Turn boundaries *become* the ASR inputs, so no timestamp alignment is ever needed.

> **This design is a consequence of the ASR, not a preference.** It exists only because Cohere
> Transcribe emits no timestamps — confirmed twice over: Cohere's own docs, and the sherpa-onnx
> implementation source, which sets only `r.text` and `r.tokens`. A timestamp-capable ASR
> would make the simpler and better-established WhisperX-style pipeline viable (transcribe
> once, diarize independently, align the two) with no per-turn ASR fan-out. See
> `plan-parity-roadmap.md` §3.1 (workstream E): Parakeet TDT 0.6B v3 has native word
> timestamps and is 6× smaller. **Settle the ASR question before building this.**

```
                    ┌──────────────────────────────────────────┐
  meeting audio ───►│ 1. VAD + segmentation + clustering       │  sherpa-onnx
   (whole file)     │    -> [(0.0,4.2,spk0), (4.4,9.1,spk1),…]│  (~35 MB of models)
                    └──────────────────────────────────────────┘
                                      │
                                      ▼  merge adjacent same-speaker turns,
                                         drop sub-300 ms fragments
                    ┌──────────────────────────────────────────┐
                    │ 2. slice audio at turn boundaries        │  no new model
                    └──────────────────────────────────────────┘
                                      │
                                      ▼  one ASR call per turn
                    ┌──────────────────────────────────────────┐
                    │ 3. existing Cohere backend, per turn     │  already shipped
                    └──────────────────────────────────────────┘
                                      │
                                      ▼  existing post-processor per turn
                    ┌──────────────────────────────────────────┐
                    │ 4. render speaker turns                  │  new, model-free
                    └──────────────────────────────────────────┘
```

Why this is the right call here:

* It sidesteps the missing timestamps completely — no wav2vec2 alignment model, no new
  language coverage problem.
* It reuses the ASR and the entire post-processor unchanged.
* Cohere already chunks at 35 s internally (`max_audio_clip_s: 35`), and most meeting turns
  are shorter than that, so per-turn calls are in the model's sweet spot.
* Failures are local: one bad turn does not poison the transcript.

Known cost: many small ASR calls instead of one big one, which is slower on CPU. For an
offline batch job that is acceptable (§7), and turns can be batched into one call per
speaker-run when consecutive turns are close together.

Overlap handling: where two speakers overlap, assign the region to the higher-energy
speaker and mark the turn `(overlap)` rather than inventing a word-level split. Overlapped
speech is where every diarizer is weakest; pretending otherwise in the output would be
worse than a visible marker.

## 4. Model choice (researched, with licences)

### 4.1 Chosen: sherpa-onnx two-stage pipeline

| Component | File | Size | Licence |
| --- | --- | --- | --- |
| Segmentation | `sherpa-onnx-pyannote-segmentation-3-0/model.onnx` | **6.96 MB** | MIT (k2-fsa conversion of `pyannote/segmentation-3.0`) |
| Speaker embedding | `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` | **~40 MB** | Apache-2.0 (3D-Speaker) |
| Clustering | library code | 0 | Apache-2.0 |
| Optional VAD front-end | Silero VAD | **~2 MB** | MIT |

**Total: ~50 MB, no Hugging Face token, fully redistributable.** CPU speed is the headline:
**RTF ≈ 0.2 — about 5× faster than realtime**, measured in upstream logs
(`Duration: 86.650 s … RTF 0.202`). Accuracy is competitive: 3D-Speaker reports DER 10.3 %
(Aishell-4), 11.75 % (VoxConverse), 21.76 % (AMI SDM).

**Verified against the local nixpkgs checkout, so the packaging question is closed:**

* `pkgs/by-name/sh/sherpa-onnx/package.nix` → `pkgs.sherpa-onnx`
* `pkgs/development/python-modules/sherpa-onnx/default.nix` → `pkgs.python3Packages.sherpa-onnx`
  (a thin `buildPythonPackage` that copies the prebuilt bindings out of the native package;
  its only Python dependency is `numpy`, and it **bundles onnxruntime** — no separate
  `onnxruntime` dependency to add)

So workstream D needs **one line** in `flake.nix`'s `pythonEnv` plus the matching
`pyproject.toml` entry — see the formatter plan §6 for the combined diff.

#### Exact artifacts

Model release tags (copy these URLs **literally** — the embedding tag really is misspelled
`recongition`, and inventing the correct spelling will 404):

```
https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx
```

The segmentation tarball extracts to `sherpa-onnx-pyannote-segmentation-3-0/model.onnx`.
Both stages also ship **`model.int8.onnx`** variants — use those to cut size and CPU cost,
and verify quality does not regress before committing to them.

Also available and directly relevant to meeting mode: **`sherpa-onnx-reverb-diarization-v1`**
(and `-v2`), tuned for **reverberant single-channel** audio — i.e. one microphone in a room,
which is exactly this use case. Evaluate it against the plain pipeline; it is a drop-in
segmentation model swap.

#### Exact Python API

```python
import sherpa_onnx

config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
    segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
        pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
            model=SEG_ONNX
        ),
    ),
    embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB_ONNX),
    clustering=sherpa_onnx.FastClusteringConfig(num_clusters=num_speakers, threshold=0.5),
    min_duration_on=0.3,
    min_duration_off=0.5,
)
if not config.validate():
    raise RuntimeError("check that both model files exist")

sd = sherpa_onnx.OfflineSpeakerDiarization(config)
audio = resample_to(audio, sd.sample_rate)          # read sd.sample_rate, do not assume
result = sd.process(audio, callback=progress_cb).sort_by_start_time()
for r in result:
    ...                                             # r.start, r.end, r.speaker
```

> **Corrections after implementing this (D3), verified against sherpa-onnx 1.12.25:**
> * `window_shift_ratio` **does not exist** on
>   `OfflineSpeakerSegmentationPyannoteModelConfig` — it takes `model` only, and passing the
>   extra keyword raises `TypeError`. The earlier draft of this snippet carried it.
> * Result items have **no `.overlap` attribute**, so `Turn.overlap` is always `False` on
>   this backend. The field stays in the dataclass (the contract is shared) but nothing
>   should read it as meaningful yet.
> * The `progress` callback must return an `int` for the library, while the D1 contract's
>   callback returns `None`, so the backend adapts it in one line rather than changing
>   either interface.

Four things this settles, each of which was an open design question in the previous
revision:

1. **`num_clusters=-1` means "auto"**; otherwise pass the known count. `threshold` only
   applies when auto, and **a smaller threshold yields *more* speakers** (0.5 is the
   default). That maps directly onto the `diarization_speakers` setting in §6 — and note
   it is the opposite of the intuition, so document it.
2. **Progress reporting is built in.** `process(audio, callback=...)` calls back with
   `(num_processed_chunk, num_total_chunks)`; returning 0 continues. That is the progress
   indicator §5.2 requires, for free — no invented polling loop, and it is the hook the
   meeting-mode UI should surface.
3. **The library already merges turns.** `min_duration_on=0.3` / `min_duration_off=0.5`
   mean our `Turn` dataclass should **not** re-implement merging with different constants.
   Either expose these two values and keep `merge_turns()` thin, or drop `merge_turns()`
   entirely and let sherpa own it. Do not have two merging passes with two sets of numbers.
4. **`sd.sample_rate` is authoritative.** The example resamples to it before `process()`.
   Dictation audio is already 16 kHz, but meeting capture may not be — read the property
   rather than hardcoding 16000, or a capture-path change silently degrades accuracy.

This is why the previous plan's model is being replaced.

### 4.2 Rejected: pyannote.audio (the previous plan's choice)

Its code is MIT and `speaker-diarization-community-1` is CC-BY-4.0, but **both are
`gated: auto` on Hugging Face** — they require accepting terms and passing a token at
download time. This repo's whole distribution story is "the client never needs a
Hugging Face token" (see `docs/releasing.md` and the removal of the root `HF_TOKEN` file).
It also degrades to **slower than realtime on long recordings** — a 1 h 54 m file took
2 h 17 m on a 10-thread CPU — which is exactly the workload meeting mode creates.

It remains acceptable *only* if the maintainer accepts the terms once and mirrors the
weights into our own release, with attribution. That is a legal and packaging question, not
an engineering one, and it buys accuracy we do not need at this stage.

### 4.3 Rejected on licence

`DiariZen` (best measured DER, ~9–17 %) and `nvidia/diar_sortformer_4spk-v1` are
**CC-BY-NC-4.0 — non-commercial**. Unusable for a redistributed app.

### 4.4 Assessed and rejected as the default: Cohere-with-diarization projects

The user's recollection was right — these exist. They were assessed in depth, and **none is
safe as the sole backend today**. The reasoning matters more than the verdict, because one
point is easy to miss:

> **`syvai/cohere-transcribe-diarize` is a full fine-tune of the ASR itself, not an add-on
> diarizer.** Adopting it means *replacing* the Cohere Transcribe weights we ship — trading an
> official model with ~146k downloads/month for a community one with ~229 — and inheriting
> whatever general-ASR regression 2 epochs over 31.7k rows (AMI SDM + synthetic LibriSpeech)
> introduced. That is not "add a diarizer"; it is "change the thing everything else depends on".

| Project | Licence | Token | CPU reality | Timestamps | Health | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| `syvai/cohere-transcribe-diarize` | Apache-2.0 ✔ | none ✔ | ~14× RTF **on an RTX 3090**; a 2B autoregressive decoder on CPU is impractical | **segment-level, 100 ms** — the card markets "word-aligned" but the shipped parser emits one `(speaker, start, end, text)` per turn | 1 contributor, a **3-day burst**, 17 commits, 229 dl/mo, 0 open issues, no user base | **Pilot only.** Clean licence, but no DER published anywhere |
| `bakrianoo/cohereX` | Apache-2.0 *declared in `pyproject.toml`* — **no LICENSE file, GitHub API reports `license: null`** | **required** (base + pyannote both gated) | slow | real **word-level** via wav2vec2 alignment | 1 contributor, 1 day, 74★, idle ~2 months | Best *architecture*, fails on licence hygiene and gating |
| `vincentamato/cohere-transcribe-cli` | **none at all** | required | — | pyannote | 3 commits | **Disqualified** for anything commercial |

Specific findings that should stop a shipping decision:

* **No diarization metrics exist for any of them.** The syvai card publishes throughput only —
  no DER, no speaker-count error, nothing on a standard set.
* **Cross-chunk identity is unvalidated.** It uses 28 s windows / 2 s overlap, then embeddings
  (ReDimNet2 B6) into a single-threshold AHC (τ = 0.45, ≤ 8 clusters). A one-hour meeting is
  exactly where a fixed threshold with no published validation fails.
* **Its bundled helpers are partly broken.** `diarize_long.py` defaults to
  `syvai/hviske-multilingual-diarize-ts`, which **404s**, and a claimed 0.17 % EER for
  ReDimNet2 **contradicts upstream's own 0.29 %**. Two unverifiable/broken claims in one card
  is a trust problem, not a detail.
* Its vLLM path requires **patching vLLM** and is pinned to `0.19.0` (with `0.19.1` known
  broken) — not something to build a release on.
* The ReDimNet2 B6 stitching model **is** real and clean: `PalabraAI/redimnet2`, **MIT**,
  12.3 M params, ~48.5 MB. So the pieces are licensable; the *pipeline built from them* is
  what is unproven. Note it is not pluggable into sherpa-onnx, whose 3D-Speaker embeddings
  are weaker (EER ~0.5–0.8 % vs 0.23–0.29 %) — a real but second-order gap, since
  segmentation and clustering quality usually dominate.

**Decision: sherpa-onnx stays the default** (§4.1) — three small models, a verified licence
chain, no gating, ~35–45 MB, and a 15 k★ actively-maintained upstream. The Cohere options
remain registry entries (`cohere-ft`) behind the `diarization_model` setting, so adopting one
later is a config change.

**The experiment that would settle it**, if anyone wants to revisit (record it in
`docs/TODO.md`): DER **plus speaker-count error** on 30–60 minute multi-speaker recordings,
comparing (a) sherpa-onnx, (b) the syvai window+AHC stitch, (c) full-file pyannote. Nothing
below one hour of real meeting audio is informative, and a GPU is required for (b) to be
measurable at all.

### 4.5 Not chosen, noted for later

`nvidia/Nemotron-3-Diarization` (OpenMDW-1.1, 8 speakers, **streaming**, GGUF via
NeMo-Speech.cpp, Apache-2.0 runtime) is the only modern model that is simultaneously
streaming, permissively licensed and CPU-deployable. It is the right answer **if** live
labelling during the meeting is ever wanted. Meeting mode does not need it today; record it
in `docs/TODO.md` as the streaming upgrade path.

## 5. Architecture

### 5.1 New module

`src/voice_transcriber/diarize.py` — model-free until called, mirroring how
`transcribe2.py` keeps `torch` out of import time. **Swappable backend registry**, the same
shape as the formatter (`plan-on-device-formatter.md` §5.2) and as `transcribe2.BACKENDS`:

```python
BACKENDS: dict[str, str] = {
    "sherpa-onnx":  "voice_transcriber.diarizers.sherpa_onnx",   # default
    "fake":         "voice_transcriber.diarizers.fake",          # tests, no weights
}
DEFAULT_BACKEND = "sherpa-onnx"
```

> Implemented in D1. A third entry was dropped from this sketch: the
> `cohere-ft` candidate it pointed at was rejected as the default in §4.4, and
> the `§4.6` it referenced does not exist. Adding a backend stays a one-line
> change, which is the whole point of the registry.

Every backend implements the same three functions and nothing else:

```python
def available() -> bool: ...
def warm() -> None: ...                                   # optional preload; must not raise
def diarize(audio, sample_rate, *, num_speakers: int | None = None,
            threshold: float = 0.5,
            progress: Callable[[int, int], None] | None = None) -> list[Turn]: ...

@dataclass(frozen=True)
class Turn:
    start: float      # seconds
    end: float
    speaker: int      # 0-based; rendering decides the label
    overlap: bool = False
```

Notes on the signature, each tying back to the verified API in §4.1:

* **`progress` is passed through, not invented.** sherpa-onnx already calls back with
  `(processed, total)`; the meeting-mode UI needs a percentage (§5.2) and this is where it
  comes from. Do not build a parallel progress mechanism.
* **No `merge_turns()` helper.** sherpa-onnx owns turn merging via `min_duration_on` /
  `min_duration_off`; a second merging pass with different constants is how two parts of a
  pipeline end up disagreeing. Expose the two thresholds instead. **D1 implements this as a
  guarantee rather than a preference:** `validate_turns()` enforces that a turn is a
  well-formed interval inside the audio, and never decides that two good turns are one.
* **`num_speakers=None` means auto**, which the backend maps to `num_clusters=-1`.

It must **not** import `sherpa_onnx` at module import (same rule as the ASR backends: a
model-free test run must never pay for it). Load lazily behind a cached getter.

### 5.2 New capture lifecycle (the actual new product surface)

The existing engine is push-to-talk shaped: `start_recording` → `record_audio` →
`process_recording` → inject. Meeting mode needs a **second lifecycle that does not inject**:

| | Dictation (exists) | Meeting mode (new) |
| --- | --- | --- |
| Trigger | hold hotkey | start/stop verb, or a hotkey tap |
| Duration | seconds | minutes–hours |
| Audio retention | discarded after transcription | **held in memory / spilled to a temp file** |
| ASR | streaming micro-batcher | per diarized turn, after stop |
| Output | clipboard / typed text | transcript file + optional clipboard |
| TUI | VU meter + state | elapsed timer + turn count |

Two hard requirements fall out of "minutes to hours":

1. **Memory.** An hour of 16 kHz float32 mono is ~230 MB — survivable, but two hours is
   460 MB and the arrays are copied several times. Spill to a temp file past a threshold
   (e.g. 10 minutes) and read it back for diarization. Decide in M2, do not discover it.
2. **Progress.** The user must see that an hour-long job is running and roughly how far
   along. `tui.update_state()` + control-API `status` need a meeting-mode state with a
   percentage in the existing "configured vs effective" reporting style.

### 5.3 Where it plugs in

```
meeting.start ─► capture loop ─► [stop]
                                    │
                                    ▼
                     diarize.diarize(whole audio)      <- new module
                                    │
                                    ▼
                     slice turns ─► transcribe2.transcribe_audio()   <- existing
                                    │
                                    ▼
                     post_processor.clean_speech_transcription()    <- existing
                                    │
                                    ▼
                     render_turns() ─► transcript artifact          <- new, model-free
```

The post-processor is called **once per turn with the turn's own text**, exactly as it is
called per chunk today. It must never see the whole meeting as one string, or the
retraction/list logic will operate across speaker boundaries — a turn boundary is a hard
boundary, the same way a paragraph is in `docs/formatting.md`.

## 6. Settings surfaces

Following the repo's "everything is a toggle" rule
(`docs/plan-structured-formatting.md` §11) and the established pattern
(`docs/cleanup_modes.md`, `docs/formatting.md` §6). **No behaviour may be reachable only by
editing config**, and every feature must be switchable off.

| Key | Values | Default | Notes |
| --- | --- | --- | --- |
| `diarization` | `off`, `auto`, `on` | `off` | `off` never loads the model. `auto` labels only when >1 speaker is found, so a single-speaker meeting is byte-identical to a plain transcript. `on` always labels. |
| `diarization_speakers` | `auto`, or `2`–`8` | `auto` | A hint. Everything is more accurate when the count is known — offer it, do not require it. |
| `diarization_model` | model name | `sherpa-onnx` | Only if >1 diarizer ships. Resolves through the new model registry (§6.1). |

Every one of these must land in all of the following — the full checklist, derived from how
`structure_mode` and `cleanup_mode` did it:

1. `post_processor.py` / owning module: the runtime state + `normalize_<x>()` (aliases,
   unknown-value policy). **Unknown value policy: `off`** — this feature loads models and
   must fail safe, unlike `cleanup_mode` which fails to the shipped behaviour.
2. `t2.py`: module global, `DEFAULT_SETTINGS` entry, config read + `VT_*` env override +
   startup push, `save_audio_config()` update, `set_/toggle_/cycle_` setters.
3. `t2.py`: a row in `select_settings_picker()`'s `settings_defs`, a branch in
   `get_setting_state()`, a branch in the action dispatch.
4. `main.py`: `_wire_tui_callbacks` handler, `_sync_tui_state` key, a `handle_control`
   branch.
5. `control.py`: a `VERBS` entry with `choices`, plus `status` keys — and note `help --json`
   is the catalogue, so the docs must not hardcode the verb list.
6. `tui.py` (Rich) and `tui_ratatui.py` (bridge) + `tui-rs/src/{settings_picker,app,ipc}.rs`
   (mirrored literals, pinned by the `#[cfg(test)]`-split grep guard described in
   `tests/shared/test_mode_presets.py`).
7. Tests: `tests/shared/test_<x>.py` (behaviour), `test_config_sync.py` (round-trip,
   unknown value), `test_control.py` (verb + `status`), `test_settings_menu.py`
   (every toggle the modal exposes is in `DEFAULT_SETTINGS`).
8. Docs: a spec doc modelled on `docs/cleanup_modes.md`, plus `README.md`,
   `docs/control_api.md`, `docs/architecture.md`, `CHANGELOG.md`, `docs/TODO.md`.

**Restart vs hot reload:** the diarization model is startup-loaded like `model_backend`
(`main.py:356`), so changing the model requires a restart. The `diarization` **mode** should
be hot (it only decides whether to run the pass), so a user can turn it off without
restarting. Report both in `status` — this is the "configured vs effective" split
`docs/formatting.md` §1 established.

### 6.1 Model registry (shared with the formatter workstream)

`model_download.py` is hardwired to exactly one model: `REPO_ID`, `REVISION`,
`SAFETENSORS_SHA256`, `MODEL_BUNDLE_TAG`, `_REQUIRED_LOCAL`, and the function names
`cohere_models_dir()`, `ensure_local_cohere()`, `install_from_local_bundle()`,
`is_local_model_complete()` all bake in the single-artifact assumption.

Diarization adds a second artifact, and the formatter workstream adds a third. Generalise
**once**, before either lands:

```python
@dataclass(frozen=True)
class ModelSpec:
    name: str                 # "cohere", "diarization", "formatter"
    repo_id: str
    revision: str
    asset_prefix: str         # "<prefix>.partN.xz"
    bundle_tag: str           # "model-<name>-<revision[:12]>"
    required_local: tuple[str, ...]
    digests: dict[str, str]   # file -> sha256  (replaces the single SAFETENSORS_SHA256)
    license_file: str
    target_subdir: str

MODELS: dict[str, ModelSpec] = {"cohere": COHERE, ...}
```

then `models_dir(name)`, `ensure_model(name, ...)`, `is_model_complete(name)`.

Two facts that make this cheap: **the Nix/AppImage builds never carry weights** (so no
packaging change for a new artifact type), and the existing tar → raw-split → xz →
`SHA256SUMS`-last machinery is size-generic. The only real constraint is **GitHub's 2 GiB
per-asset cap**, which the ~40 MB diarization bundle is nowhere near. `--parts N` is already
the knob.

## 7. Latency: explicitly out of scope

`docs/agent_testing_workflow.md` sets ≤1.5 s release-to-clipboard. **Meeting mode does not
participate in that SLA** — it is a batch job measured in minutes, and that is a deliberate,
documented exemption rather than a regression. Requirements instead:

* a *throughput* target: diarization RTF ≤ 0.3 and a total meeting-mode target of better
  than realtime on a typical 8-core desktop (diarization ~5× realtime, ASR the bottleneck);
* a visible progress indicator, because "no output for 20 minutes" is indistinguishable
  from a hang;
* the dictation SLA must be **provably unaffected** — with `diarization: off` (the default)
  the engine must be byte-identical to today, pinned by a test.

## 8. Output artifact

The output is a document, not typed text, so the Enter-keypress hazard that dominates
`docs/formatting.md` §1 does not apply. Proposed, to be refined in M4:

* plain text with `[Speaker 1]` / `[Speaker 2]` turn labels and a header (date, duration,
  speaker count, model versions);
* optionally JSON, one object per turn — machine-readable is what makes the eval in §9
  possible;
* optional Markdown;
* a **speakers map** so a user can rename `Speaker 2` → `Priya`. Names are asked for
  *after* transcription, never during.
* Where does it go? A path from config (`meeting_output_dir`), plus "reveal in file
  manager" / copy-path actions. Do not invent a document database.

**Privacy:** speaker embeddings are biometric data. They must never leave the machine,
never be written to the stats file, never be logged, and never be uploaded by any future
telemetry. If a speakers map persists across meetings (nice feature, real risk), it must be
opt-in and stored locally with a documented delete path.

## 9. Verification

| Layer | What is pinned | Tier |
| --- | --- | --- |
| Unit, model-free | `validate_turns()` (non-finite bounds, non-positive intervals, out-of-range turns, clamping, ordering, partial-drop), `parse_speakers()`, the single-speaker label suppression, the fail-safe returning `[]` on every backend failure, and the "turn boundary is a hard boundary for the post-processor" rule. Merging and gap-dropping are **not** here: they are sherpa-onnx's, by design (§5.1). | `tests/shared/` |
| Unit, model-free | The model registry: a second `ModelSpec` resolves, per-file digests verify, the single-model `test_model_download.py` pins are relaxed without losing the "one declaration home" property. | `tests/shared/` |
| Fixture | A tiny synthetic ONNX fixture and a **fake diarizer** implementing the `Turn` protocol, so the whole pipeline is testable without downloading a model. | `tests/shared/` |
| E2E, needs weights | A two-voice fixture through the real pipeline, asserting **turn count and speaker ordering**, not just the words. | `tests/e2e/` |
| Quality | **DER on a small labelled set**, recorded in `eval/` next to the ASR numbers so a regression is visible. Reuse the existing `eval/` manifest mechanism. | `eval/` |
| Regression | `diarization: off` produces byte-identical output to today. | `tests/shared/` |
| Platform | Linux first. Windows/WSL inherit the same engine path, but the **memory footprint must be measured there** — the Windows bundle already ships a large base. | `docs/TODO.md` |

## 10. Milestones

| D | Content | Exit criteria |
| --- | --- | --- |
| **D0** | Decide the stack (§4.1 vs §4.4) | **done** — sherpa-onnx, recorded above |
| **D1** | `diarize.py` backend contract + `fake` diarizer (model-free) | **done** — model-free tests green; no `sherpa_onnx` import at module import |
| **D2** | Meeting capture lifecycle: start/stop, long capture, temp-file spill, progress state | a 10-minute recording completes without exhausting memory; `status` reports progress |
| **D3** | Real `diarizers/sherpa_onnx.py` + a spike on real weights | model-free tests green; RTF measured on real weights |
| **D4** | Turn slicing → per-turn ASR → per-turn post-processing | two-voice E2E asserts correct turn count and ordering |
| **D5** | Output artifact + speakers map | a real meeting produces a readable labelled transcript |
| **D6** | Settings end-to-end (§6) | `off` byte-identical to today; both TUIs expose it; `help --json` lists it |
| **D7** | Eval entry, DER recorded, `docs/TODO.md` notes | DER in `eval/`; unverified platforms named |

> **Where the `ModelSpec` generalisation went.** Earlier revisions of this table made it D1,
> because diarization was the first feature to need a second artifact. It was lifted out into
> its own workstream (**B** in [`plan-parity-roadmap.md`](plan-parity-roadmap.md)) once the
> formatter needed a *third*, and it is already shipped (`refactor/model-registry`). §6.1 below
> is therefore a description of finished work, not a plan — kept because it is the record of
> why the registry is shaped the way it is.

## 11. Risks

| Risk | Mitigation |
| --- | --- |
| Long recordings blow up memory | Temp-file spill past a threshold (D2); measure before shipping. |
| Clustering is the CPU bottleneck and scales with length | Known and documented; offer `diarization_speakers: N` as a hint, which makes clustering dramatically cheaper and more accurate. |
| `auto` labels a single-speaker meeting by mistake | Pin it with a test; it is the property most likely to annoy users. |
| Model licensing turns out wrong | Only Apache-2.0/MIT components are used; record licences in `config/licenses/` as the Cohere model does, and add the entry to the release NOTICE. |
| Second model download for an off-by-default feature | Make it a deliberate, separately-versioned bundle; state the size (~40 MB) in the prompt; never download during a dictation. |
| Speaker identity drifting across a long recording | Per-turn labels are re-derived per meeting, never persisted; document that labels are per-meeting, not a voiceprint. |

## 12. Open questions

1. **Label style** — `[Speaker 1]`, `Speaker 1:`, or user-assigned names? Names need a
   mapping UI. Recommendation: numeric at transcription, renamable afterwards.
2. **Is a second model download acceptable** for a feature off by default? ~40 MB is small;
   the answer is probably yes, but it must be the user's choice at first use.
3. **Output location** — configurable directory vs alongside a chosen file. Needs a
   decision before D5.
4. **Should meeting capture be a hotkey tap, a control verb, or a TUI action?** The control
   API needs `meeting-start` / `meeting-stop` regardless, because that is how the app is
   scripted and tested.
