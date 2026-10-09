# Meeting Pipeline (D4, and the core of D5)

**Status:** shipped — turn slicing, per-turn ASR, per-turn post-processing and a
plain-text transcript artifact. Design rationale is in
[`plan-diarization.md`](plan-diarization.md) §3, §5.3, §7 and §8; the capture
side is in [`meeting_mode.md`](meeting_mode.md).

This is the join between the two halves of meeting mode. `meeting.py` captures a
long recording and hands the read-back audio to an `on_audio` consumer after the
user stops; `meeting_pipeline.MeetingPipeline` is that consumer. It runs as a
**background batch job** and never injects text.

```
meeting.stop ─► read-back (16 kHz mono) ─► diarize.diarize()
                                                    │  ([] == one speaker)
                                                    ▼
                              slice at turn boundaries ─► transcribe2 per turn
                                                    │
                                                    ▼
                              post_processor.clean_speech_transcription per turn
                                                    │
                                                    ▼
                              render + write transcript ─► status / TUI
```

The code lives in `src/voice_transcriber/meeting_pipeline.py` (with the
`src/meeting_pipeline.py` shim, like `meeting`/`diarize`). `meeting.py` stays
about capture.

## The central design decision

Diarize first, then transcribe **each turn** (plan §3). Turn boundaries *are*
the ASR inputs, so no timestamp alignment is ever needed — and this is a
consequence of Cohere Transcribe emitting no timestamps, not a preference.
Per-turn calls also keep failures local (one bad turn cannot poison the
meeting) and let the existing post-processor run per turn.

Critically, the post-processor is called **once per turn with the turn's own
text**. A turn boundary is a hard boundary: handing the whole meeting to
`clean_speech_transcription` as one string would let the retraction/list logic
operate across a speaker change. This is pinned by a test.

## Concurrency: one model, two producers, dictation first

**One ASR model, shared deliberately.** The dictation micro-batcher and the
meeting pipeline both call `transcribe2.transcribe_audio` on the same model
singleton. Sharing costs no extra memory (~2 GB stays ~2 GB). The model is
never loaded twice.

**The inference lock.** Inference on the shared model is not safe to run
concurrently (the Cohere processor has a cached-call path with real per-call
state), so `transcribe2` exposes a module-level re-entrant `inference_lock` and
`transcribe_audio` acquires it around every call. Dictation therefore needs no
change — it is serialized automatically. The lock guards **inference**, not the
load: the backend's `_model_lock` still owns loading.

**Dictation yields, the meeting never blocks it.** The pipeline holds the lock
only for the duration of one turn (it is released between turns — never held
across the whole meeting). Before starting each turn it checks the engine's
live recording flag (`is_dictating`, wired in `main.py` from
`SimpleVoiceTranscriber.recording`) and sleeps briefly (default 100 ms, never a
busy-wait) while a dictation is live. It then re-checks the flag *after* taking
the lock, so a dictation that started during the wait is never overtaken.

**Honest worst-case latency.** If a dictation starts in the tiny window after
the pipeline has taken the lock for a turn, it waits for the **remainder of one
in-flight turn's ASR** — never the whole meeting. Turns are capped at 35 s
(Cohere's own `max_audio_clip_s`), so the theoretical worst case is one 35 s
clip. In practice a diarized turn is much shorter, and the cap is a backstop,
not the normal path. This residual is inherent to sharing one model; the
long-term fix (a second ASR capacity, or a timestamp-capable ASR such as
Parakeet per `plan-parity-roadmap.md` §3.1) is out of scope here.

## Diarization is an enhancement, not a prerequisite

`diarize.diarize()` never raises and returns `[]` when the backend or weights
are unavailable; `[]` means "one speaker, one transcript". The pipeline turns
that into a single turn spanning the whole recording and still produces a full
transcript, so meeting mode works out of the box. A broken diarizer is caught
and degrades the same way.

Labels are suppressed via `diarize.should_label(turns)`: a single-speaker
recording is **not** covered in `[Speaker 1]`.

## The artifact

On success the transcript is written to:

* `$VT_MEETING_OUTPUT_DIR`, or
* `<per-user data dir>/meetings` (e.g. `~/.local/share/vt/meetings`).

The file is `<data dir>/meetings/meeting-YYYYMMDD-HHMMSS.txt`, written
atomically (temp file + `os.replace`, temp removed on every path). The content
is a header plus one line per turn:

```
Meeting transcript
Date: 2026-10-09 13:54:51
Duration: 02:05
Speakers: 2

[Speaker 1] Hello there.
[Speaker 2] General Kenobi.
```

The rendered text is also kept in memory (`MeetingPipeline.last_transcript`) and
surfaced through `status` as `meeting_transcript`, `meeting_transcript_path` and
`meeting_speakers`, so a caller can read the result over the control API without
touching the filesystem.

**No `meeting_output_dir` settings key was added.** `$VT_MEETING_OUTPUT_DIR` is
the supported override; a settings key would have to land in every surface
including `tui-rs`, which this milestone is deliberately scoped out of. The full
configuration set is D6's job.

## Progress, state and cancellation

The pipeline reports through the seams `meeting.py` already exposes:

* `progress(processed, total)` → `MeetingSession.set_progress`, scaled so
  diarization owns the first 40 % and the per-turn ASR the rest. A user sees
  "transcribing, 80 %", not silence for twenty minutes (plan §7).
* `stage_callback(stage)` → `MeetingSession.set_stage`, with stages
  `diarizing`, `transcribing`, `rendering`, `idle` (plus `reading` while the
  spill file is read back). `status` reports it as `meeting_stage` and the TUI
  shows it.

The pipeline runs on the session's `vt-meeting-finalize` **daemon** thread, so
it can never block process exit. `MeetingSession` owns a cancel event that
`close()` sets; the pipeline checks it at every turn boundary and while waiting
on the dictation flag, so a quit returns promptly instead of running a whole
meeting out. A cancelled run leaves no artifact. The spill temp file is closed
and deleted by `AudioSpillBuffer` in a `finally` on success, failure or cancel.

## Tests

`tests/shared/test_meeting_pipeline.py` (model-free, device-free; the diarizer,
transcriber and post-processor are injection seams):

* two-voice turn **count and ordering**, and that each ASR call is exactly one
  turn long;
* the post-processor is called once per turn with that turn's text;
* the unavailable-diarization / single-speaker fallback still produces a full
  transcript, with labels suppressed;
* the pipeline waits while a dictation is live, and the inference lock is
  released between turns;
* `transcribe_audio` serializes two producers on the inference lock;
* cancellation and the failure path leave no artifact or temp file, and a quit
  unblocks a pipeline parked on a live dictation;
* progress is monotonic and reaches 100 %;
* an end-to-end capture → pipeline → `status` run, plus a real-weights test that
  is skipped when the diarization models are absent.

## Not here (later milestones)

* The speakers map and renamable labels, JSON/Markdown output (D5).
* The `diarization` / `diarization_speakers` / `diarization_model` settings and
  the `meeting_output_dir` key (D6).
* DER recorded in `eval/` (D7).
