# Meeting Mode (long, non-injecting capture)

**Status:** capture lifecycle (D2), diarization + per-turn ASR + the transcript
artifact (D3/D4) and the diarization settings (D6) are shipped. The renamable
speakers map, JSON/Markdown forms and the output-directory setting are D5 — see
[`plan-diarization.md`](plan-diarization.md), especially §5.2 and §8. One
prerequisite gap remains: the diarization model has no download path yet (see
"Prerequisite gap" below).

Meeting mode is a **second capture lifecycle**. Dictation is push-to-talk and
seconds long: it captures, transcribes and *injects* typed text. Meeting mode is
start/stop and minutes-to-hours long: it captures, **holds** the audio, and will
produce a document (a speaker-labelled transcript) rather than keystrokes. The
two lifecycles share no state: a long meeting capture cannot clear the dictation
stop flag, and the dictation path is untouched while a meeting runs.

## The lifecycle

```
idle ──meeting-start──▶ recording ──meeting-stop──▶ processing ──▶ idle
  ▲                                                              │
  └──────────────────────────────────────────────────────────────┘
```

* **idle → recording** on `meeting-start` (or the settings modal's *Meeting
  Capture* action). Starting twice is an error, not a second capture.
* **recording → processing** when the capture source ends, either because
  `meeting-stop` was requested or because the device failed. The stop call
  returns immediately; a worker thread joins the capture and reads the audio
  back, so the hotkey/UI/control threads are never blocked.
* **processing → idle** when the audio has been read back and handed to the
  batch consumer (D4's seam; see `MeetingSession(on_audio=...)`). The temp spill
  file is deleted in a `finally` on every path — success, capture failure or
  read-back failure.

Stopping when not recording, or starting when already recording, raises
`MeetingStateError`. The control API turns that into `{"ok": false, ...}`; it
never raises into the engine and never starts a partial capture.

Meeting capture does **not** inject: it never calls the typing or clipboard
paths. It is also exempt from the ≤1.5 s release-to-clipboard SLA
(`plan-diarization.md` §7) — it is a batch job, not dictation.

## Settings

Every setting is a toggle: it lives in `t2.DEFAULT_SETTINGS`, is settable from
the settings modal in both frontends, and is discoverable and settable through
the control API. `python src/main.py help --json` is the authoritative verb
catalogue — do not treat the names below as an exhaustive list.

| Key | Values | Default | Notes |
| --- | --- | --- | --- |
| `meeting` | `off`, `on` | `off` | The capture-side enable toggle. `off` (the shipped default) means `meeting-start` refuses and the dictation path is byte-identical to a build without the feature. Unknown values fail safe to `off`. |
| `meeting_spill_minutes` | `1`–`240` | `10` | In-memory spill threshold, in minutes. The settings modal cycles 5/10/20/30/60; the config file and control API accept any value in range. Unknown or out-of-range values fall back to the default rather than to "no spill". |
| `diarization` | `off`, `on` | `off` | Speaker labels for meeting transcripts (D6). `off` never loads the diarization model and never runs the pass, so a meeting is transcribed as one speaker. Unknown values fail safe to `off` (the pass loads a model; a typo must not turn it on). |
| `diarization_speakers` | `auto`, `2`–`8` | `auto` | Optional expected speaker count hint. Everything is more accurate, and clustering much cheaper, when the count is known — but it is only a hint. Out-of-range or unparseable values fall back to `auto`, never to a guessed count. |
| `diarization_model` | registry name | `diarization` | Which diarization model bundle to load (the `diarization` `ModelSpec` in `model_download.py`). The model is startup-loaded like `model_backend`, so changing it needs a restart; the `diarization` **mode** above is hot. Unknown names warn and fall back to the shipped default. |
| `meeting_output_dir` | path | `meetings` | Where the finished transcript is written. Repo-relative by default, so a checkout's transcripts land in `<repo>/meetings/` (gitignored); an absolute path is used as-is. `VT_MEETING_OUTPUT_DIR` always wins. |
| `meeting_output_format` | `text`, `json`, `markdown` | `text` | The artifact form. `text` is byte-identical to the pre-D5 artifact; `json` is one object per turn; `markdown` is a headed Markdown document. Unknown values fall back to `text`. |

`status` reports the pair `meeting` (effective) and `meeting_setting`
(configured), in the same configured-vs-effective style as `structure_mode` /
`structure_setting` and `formatter` / `formatter_setting`. They are equal today
— there is no downgrade path yet — but they are reported separately so a future
gate (for example diarization being switched off in D6) cannot quietly turn one
into the other.

Env overrides: `VT_MEETING`, `VT_MEETING_SPILL_MINUTES`, `VT_DIARIZATION`,
`VT_DIARIZATION_SPEAKERS`, `VT_DIARIZATION_MODEL`, `VT_MEETING_OUTPUT_DIR`,
`VT_MEETING_OUTPUT_FORMAT`.

Config keys: `meeting`, `meeting_spill_minutes`, `diarization`,
`diarization_speakers`, `diarization_model`, `meeting_output_dir`,
`meeting_output_format`.

The diarization keys are a meeting-mode **enhancement**, not a second feature
gate. Enabling `meeting` alone still captures and transcribes, but as a single
speaker; `diarization: on` adds the pass that labels who said what.

### Prerequisite gap: the diarization models have no download path yet

Meeting mode's speaker labels need the sherpa-onnx pyannote-segmentation and
3D-Speaker embedding graphs. Those weights are **not downloadable through the
app today**: the on-device model registry has a `diarization` spec landing in
parallel, but no client path installs it, and `diarize.py`'s backend silently
degrades to "no diarization" (one speaker) when the graphs are absent. On this
development machine the graphs were placed under the model directory by hand,
which is why the verified runs in `docs/meeting_pipeline.md` work here at all.
Until the registry entry ships a download path, treat speaker labels as
available only where the weights have been installed manually — they are not a
first-run experience. This is deliberate D7 research recorded rather than
implied away.

### The speakers map is transcript metadata, edited on the meeting screen

The **speakers map** turns a numeric label into a name: `Speaker 2` becomes
`Priya`. It is an **ordered, editable list of names** — entry 1 names speaker 1,
entry 2 names speaker 2, and so on. There is deliberately no `S1`/`S2` concept in
the UI; it is just names. A slot left blank, or a speaker past the end of the
list, falls back to the readable `Speaker N` label, so a partial map still
renders.

**This is the one editable thing that is not in the settings modal, and that is
on purpose.** The repo invariant is "the settings modal is the only
configuration entry point", and this is not configuration: a speaker name is
per-meeting **transcript metadata**, like the transcript text itself. It is
therefore implemented as a distinct *meeting-screen* action, not a settings row
(`select_speaker_editor()` in `t2.py`, reached through the `on_open_speaker_editor`
seam), and it is never written to `config/config.yaml`.

Because the meeting screen has its own action, it has its own terminal key map,
scoped strictly to that screen:

| Key | Meeting screen | Everywhere else (unchanged) |
| --- | --- | --- |
| `Space` / `Enter` | start/stop the meeting capture | start/stop dictation |
| `s` / `S` | open the speaker-name editor | open the settings modal |
| `Esc` | quit | quit |
| `q` / `Ctrl+C` | quit | quit |
| `,` | (not bound) | open the settings modal |
| `r` | (not bound) | reset the terminal |

Outside the meeting screen the global contract is exactly what it always was:
`Space`/`Enter` record, `s`/`S`/`,` settings, `r` reset, `q`/`Esc`/`Ctrl+C` quit.
Both halves are pinned by `tests/shared/test_meeting_screen.py`.

The map is live: it is read at render time, so a name changed while the pipeline
is still transcribing turns is applied to the finished document. The control API
also exposes it as metadata (`speakers`, aliases `speaker-names` / `speaker-map`)
for scripting, and `status` reports it as `speakers`.

## Progress state

`status` (and the TUI) carry a meeting sub-state:

| Key | Meaning |
| --- | --- |
| `meeting` | effective toggle (`off`/`on`) |
| `meeting_setting` | configured toggle |
| `meeting_state` | `idle`, `recording` or `processing` |
| `meeting_elapsed_s` | seconds since capture began; frozen once processing starts |
| `meeting_progress` | batch-phase completion, 0–100 |
| `meeting_spill_minutes` | the configured threshold |
| `diarization` | effective diarization toggle (`off`/`on`) |
| `diarization_setting` | configured diarization toggle (same value today; reported as a pair so a future gate cannot quietly turn one into the other) |
| `diarization_speakers` | the configured speaker-count hint (`auto` or an int) |
| `diarization_model` | the configured model registry name |

A live capture has no known total, so `meeting_progress` stays `0` while
recording and `meeting_elapsed_s` is what tells the user "how far along" it is.
The percentage becomes meaningful in the **processing** phase, where it comes
from the backend's own `(processed, total)` callback — the sherpa-onnx
diarization callback the plan identifies in §4.1 note 2 — passed through
`MeetingSession.set_progress(processed, total)` and `progress_percent()`. It is
never an invented polling counter. (D2's read-back of the spill file already
drives it; D4 will drive it from the diarizer.)

The TUI shows the meeting state as `MEETING`, with the elapsed timer and
percentage in the sub-state line. It is a distinct state from `RECORDING`, so
dictation's `wait`/settle logic is not confused by a meeting in progress.

## Memory: the temp-file spill

16 kHz mono float32 is `16 000 × 4 = 64 000 bytes/s` (62.5 KiB/s). An hour is
`3600 × 64 000 = 230.4 MB`, and that is before the copies the capture path makes
(the in-memory chunk list, the read-back concatenation, the diarizer's own
working array). Two hours is 460 MB.

So the in-memory tail is capped at `meeting_spill_minutes` (default **10**):
`600 s × 64 000 B/s ≈ 38.4 MB`. Older audio is written to a temp file created
with `tempfile.NamedTemporaryFile(prefix="vt-meeting-", suffix=".f32")` — i.e.
the platform temp directory, honouring `TMPDIR` — and read back exactly once,
after stop. Peak capture memory is therefore bounded by the threshold no matter
how long the meeting runs; the full-meeting allocation happens once, in
`read_all()`, after stop.

The temp file is deleted by `AudioSpillBuffer.close()`, which runs in a
`finally` on every exit path (normal completion, capture failure, read-back
failure) and also from `__del__` as a backstop. A capture that never spills
(a short meeting) never creates a file at all. Nothing from a meeting is left on
disk.

## Decisions made in D2 (recorded for the user)

The plan left these open (§12); these are the defaults D2 picked, each with the
reason it was chosen.

1. **Spill threshold and location.** 10 minutes, in the platform temp directory,
   deleted on every exit. Chosen because 10 minutes is the plan's own suggestion
   and keeps the in-memory tail under ~40 MB while a whole meeting still costs
   only one temp file.
2. **How capture is started.** The control verbs are the contract
   (`meeting-start` / `meeting-stop`) because that is how the app is scripted
   and tested. On top of that, the settings modal exposes a *Meeting Capture*
   action row in the Rich TUI, backed by the wired `on_toggle_meeting` callback.
   No new global hotkey and no new terminal key are added: the terminal key set
   is fixed (Space/Enter, s/S/`,`, r, q/Esc/Ctrl+C) and meeting mode must not
   disturb it. Both frontends render the `MEETING` state (the ratatui frontend
   has a dedicated run state for it), and both mirror the two *settings* rows;
   only the Rich modal has the capture *action* row so far. A ratatui user
   starts a meeting through the control API. This is the one known parity gap.
3. **What `status` reports while running.** The sub-state above. `meeting` /
   `meeting_setting` for the configured-vs-effective pair, `meeting_state` for
   idle/recording/processing, `meeting_elapsed_s` for the live timer, and
   `meeting_progress` for the batch-phase percentage (0 until processing).
4. **Progress during capture.** The percentage is a batch-phase metric because a
   live capture has no total. Reporting a percentage against an invented
   denominator would be a lie; the elapsed timer is the honest "how far along"
   during capture.

## Not in D2

* Diarization and turn slicing — D4 shipped these (`meeting_pipeline.py`).
* The transcript artifact, speaker labels and the speakers map — D4 shipped the
  artifact and the speaker labels; the renamable speakers map is D5's
  remaining work (`docs/plan-diarization.md` §8).
* The diarization settings (`diarization`, `diarization_speakers`,
  `diarization_model`) — D6 shipped these; the model download path is the
  prerequisite gap noted above.
