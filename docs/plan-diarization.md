# Plan: Diarization (multi-speaker mode)

**Status:** proposed, not started
**Owner:** James
**Prerequisite reading:** [`docs/plan-structured-formatting.md`](plan-structured-formatting.md),
[`docs/formatting.md`](formatting.md)

## 1. What is being asked for

Transcription that says *who* spoke: a meeting, interview or two-person dictation
comes out as speaker turns rather than one undifferentiated paragraph.

It is a **separate mode, not a formatting option**. It needs a segmentation stage
that does not exist, a second model, and a decision about what the output even
means — none of which belongs in the post-processor.

## 2. Measured starting point

* **The ASR backend cannot do it.** `CohereLabs/cohere-transcribe-03-2026`
  (`src/voice_transcriber/model_download.py:41`) is audio-in/text-out ASR. Its
  `model.transcribe()` accepts audio, sample rates and a language — there is no
  speaker parameter, and the model card advertises no diarization. Confirmed by
  inspecting the call site in `transcribe_cohere.py:484-501`.
* **There is a proven recipe with this exact model.** `bakrianoo/cohereX` runs
  Cohere Transcribe and offers `--diarize` by pairing it with
  `pyannote/speaker-diarization-community-1`. So the path is: keep the ASR, add a
  segmentation pass, assign the ASR segments to speakers.
* **Nothing in this repo mentions diarization or speakers** outside this plan and
  the structured-formatting plan that deferred it here.

## 3. Why this is not a small feature

| Problem | Why it is hard |
| --- | --- |
| Two models, one hotkey | A diarization model must be downloaded, licensed, loaded into memory and kept warm alongside 2.8 GB of ASR weights. |
| Speaker identity across chunks | The micro-batcher transcribes 4.5-7 s chunks independently. Speaker labels must be *stable* across those chunks, or turn 4 is "Speaker 1" again. |
| Streaming vs offline | Diarization pipelines are batch-oriented (global clustering). Push-to-talk wants sub-second feedback. The honest first cut is offline-after-stop, not live. |
| Overlapping speech | Two people talking over each other is exactly where diarization is least reliable and where users notice most. |
| Output shape | A turn change needs a line break — and a line break is an Enter keypress (see `docs/formatting.md` §1). Speaker turns inherit the same clipboard-first rule as `blocks`. |
| Privacy | Speaker embeddings are biometric data. They must never leave the machine, never be logged, and never land in the stats file. |

## 4. The product question that has to be answered first

**Push-to-talk dictation is single-speaker by construction.** You hold a key and
talk; nobody else is on your microphone. Diarization only earns its place for
*long-form meeting capture* — which is a different capture mode: no hotkey,
minutes to hours of audio, and a summary/transcript artifact rather than typed
text.

So before writing code, decide which of these this is:

1. **A convenience for multi-voice dictation** (two people at one desk, an
   interview). Keeps PTT, adds `[Speaker 1]`/`[Speaker 2]` labels to the same
   output path. Small, cheap, useful.
2. **A meeting mode** (record a room for an hour, get a diarized transcript).
   New capture lifecycle, new UI surface, new storage, and the typing path is
   irrelevant. Large, and arguably a different product.

This plan assumes **(1)** until told otherwise, because it reuses everything that
exists. Option (2) should be its own plan.

## 5. Design

### Stage placement

```
audio -> micro_batcher (VAD, chunking) -> ASR (Cohere) -> [diarizer] -> post_processor -> output
```

A new optional module `src/voice_transcriber/diarize.py`, invoked by the engine
after stitching and before `clean_speech_transcription()`, so the post-processor
stays model-free and pure. It is not part of the HAL: this is engine logic, not an
OS difference.

### Setting (per the everything-is-a-toggle rule)

| Key | Values | Default |
| --- | --- | --- |
| `diarization` | `off`, `auto`, `on` | `off` |

* `off` — never load the model, never add a label.
* `auto` — label only when more than one speaker is detected (the common case),
  so a single-speaker dictation is byte-identical to today.
* `on` — always label, even for one speaker.

Surfaces, exactly as the structure setting did it (`docs/formatting.md` §1 is the
template): `DEFAULT_SETTINGS` + env override, `t2` setters, a control verb
(`diarization`), `status` reporting both the configured and effective value, and a
row in the settings modal of **both** frontends.

### Output convention

```
[Speaker 1] Remind me to send the invoice on Wednesday.
[Speaker 2] And book the room for Friday.
```

* Turn changes become a line break, consecutive segments by the same speaker merge
  into one turn, and the label is added once per turn, not per sentence.
* Inline (typed) mode renders ` [Speaker 2] ` inline instead, because of the Enter
  hazard — the same downgrade rule as `blocks`, and for the same reason.
* The post-processor must not bullet-ify across a speaker turn: a turn boundary is
  a hard boundary, like a paragraph.
* **`auto` must not add a label to a single-speaker utterance.** That is the
  property most likely to annoy users, so it is the one to pin first.

### Model and packaging

`pyannote/speaker-diarization-community-1` as the third-party precedent uses it.
Two constraints to resolve before committing:

1. **Licence and gating.** The pyannote community pipeline may be gated behind
   accepting terms; verify the exact artifact and its licence, and confirm it can
   be redistributed the way `scripts/publish_model_bundle.sh` redistributes the
   Cohere weights. If it cannot, the feature either needs a different model or a
   user-supplied path.
2. **Size.** It must fit the existing bundle story: weights are published on a
   revision-derived tag, not per release (`docs/releasing.md`). A second model
   means a second bundle, a second `REVISION`-style constant, and its own
   publishing step — or a deliberate decision to make diarization a
   download-on-first-use extra rather than part of the base install.

## 6. Verification plan

| Layer | What is pinned |
| --- | --- |
| Unit (model-free) | Turn merging, label formatting, single-speaker `auto` suppression, interaction with the structure stage (a turn is not bulleted), inline-vs-blocks rendering. `tests/shared/` — no weights. |
| E2E (has weights) | A two-voice fixture through the real pipeline, asserting the turn count and the speaker ordering, not just the words. |
| Quality | Diarization Error Rate on a small labelled set, recorded in `eval/` next to the ASR numbers so a regression is visible. |
| Platform | Linux first; Windows/WSL inherit it through the same engine path, but the extra model's memory footprint needs measuring there (the Windows build ships a 2.8 GB base already). |

## 7. Milestones

| D | Content | Exit criteria |
| --- | --- | --- |
| D0 | Answer §4 (dictation convenience vs meeting mode) and §5's licence/gating question | a written decision, no code |
| D1 | Spike: run the pyannote pipeline on a two-voice clip offline, outside the app | a turn list with timestamps and a measured DER |
| D2 | `diarize.py` + assignment of ASR segments to turns, unit-tested | model-free tests green; E2E turn count correct |
| D3 | Output convention + `diarization` setting end-to-end (config, verb, status, both TUIs, docs) | `off` byte-identical to today; single speaker in `auto` unlabelled |
| D4 | Streaming/latency work, or an explicit decision to stay offline-only | a measured latency budget, or documented as offline-only |
| D5 | Eval entry + `docs/TODO.md` verification notes | DER recorded; unverified platforms named |

## 8. Open questions for James

1. **Dictation convenience or meeting mode?** (§4) — this changes everything below
   it.
2. **Is a second model download acceptable** for a feature that is off by
   default, or should diarization be an optional extra that the user installs
   deliberately?
3. **Label style** — `[Speaker 1]`, `Speaker 1:`, or a name the user assigns
   (`[Alice]`)? Names would need a mapping UI, which is a feature of its own.
4. **Does `auto` need a speaker-count hint?** Meeting invitees are known; guessing
   from audio is the least reliable part of every diarization pipeline.
