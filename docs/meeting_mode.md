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

> **To end a capture, press `Space`** (or activate the *Meeting Capture* settings
> row, or send the `meeting-stop` verb). **Do not reach for `q` or `Esc`:** those
> quit the engine, and the shutdown path *cancels* the finalize
> (`MeetingSession.close` sets the cancel flag) so the audio is read back and
> then discarded — no transcript is written. Cancelling is deliberate, since a
> long finalize would otherwise block quitting, but it means quitting mid-meeting
> loses the recording.

The map is live: it is read at render time, so a name changed while the pipeline
is still transcribing turns is applied to the finished document. The control API
also exposes it as metadata (`speakers`, aliases `speaker-names` / `speaker-map`)
for scripting, and `status` reports it as `speakers`.

### The speaker-label rendering rule

This is the one rule every artifact form shares; it lives in
`meeting_pipeline.MeetingPipeline.speaker_label()`:

* a turn's numeric speaker index *i* (zero-based) is labelled with `names[i]`
  when that slot holds a non-empty name;
* a blank slot, or an index past the end of the list, falls back to the
  readable **`Speaker N`** with `N = i + 1` — a partial map still renders and a
  transcript is never label-less;
* with diarization off, or labelling overridden off, the transcript is
  unlabelled: `text`/`markdown` emit the bare text, `json` sets `name` to
  `null` and `labelled` to `false`.

`text` renders `[<label>] <text>` lines, `markdown` renders
`**<label>:** <text>`, and `json` carries both the numeric `speaker` and the
resolved `name`.

### The frozen bridge contract (S2/S3)

The ratatui half of D5 was derived from the merged Python code and is frozen
here. It is enforced by source-level parity guards
(`tests/shared/test_config_sync.py`, `tests/shared/test_meeting_screen.py`) so
the two languages cannot drift.

**Control verbs** (see `docs/control_api.md`; `python src/main.py help --json`
is authoritative):

| Verb | Value | Choices | Returns |
| --- | --- | --- | --- |
| `diarization` (engine also accepts `diarization-mode`) | `state` | `off`, `on` | `diarization`, `diarization_setting` |
| `diarization-speakers` (engine also accepts `diarization_speakers`) | `count` | `auto`, `2`–`8`; omit to cycle | `diarization_speakers` |
| `diarization-model` (engine also accepts `diarization_model`) | `model` | `diarization` (required) | `diarization_model` |
| `meeting-output` (aliases `meeting-output-dir`, `meeting-output-folder`) | `folder` | — | `meeting_output_dir` |
| `meeting-format` (engine also accepts `meeting-output-format`, `meeting_format`) | `format` | `text`, `json`, `markdown`; omit to cycle | `meeting_output_format` |
| `speakers` (aliases `speaker-names`, `speaker-map`) | `names` | ordered, comma-separated string or list | `speakers` |

The `control.py` catalogue is the aliased surface (`help --json` resolves aliases
client-side); the parenthesised spellings are accepted directly by
`handle_control` for the frontends.

Setting `speakers` is **transcript metadata**, so it does **not** write
`config/config.yaml`; the other five are settings and do.

**`status` fields:** `diarization` (effective) and `diarization_setting`
(configured) — the same value today, reported as a pair like the formatter —
plus `diarization_speakers`, `diarization_model`, `meeting_output_dir`,
`meeting_output_format`, `speakers`, and the D4 trio `meeting_transcript`,
`meeting_transcript_path`, `meeting_speakers`. While a capture is running,
`meeting_live_transcript` carries the provisional transcript described below
(empty once it stops); `meeting_transcript` is the finished, written artifact.

**Python → Rust (`cfg` wire):** `diarization` (string `off`/`on`),
`diarization_speakers` (either the string `auto` or an integer `2`–`8`),
`diarization_model` (registry id), `meeting_output_dir` (string),
`meeting_output_format` (string), and `speakers` (an ordered array of strings).
The Rust `App` stores the machine values verbatim; only the *display* differs
(`"S1-mini" by "Superwhisper"` for the formatter model).

**Rust → Python (`cmd` wire):**

```
{"t":"cmd","cmd":"cycle_diarization"}
{"t":"cmd","cmd":"cycle_diarization_speakers"}
{"t":"cmd","cmd":"cycle_diarization_model"}
{"t":"cmd","cmd":"cycle_meeting_output_format"}
{"t":"cmd","cmd":"set_meeting_output_dir","path":"<folder>"}
{"t":"cmd","cmd":"set_speakers","speakers":["Priya","Sam"]}
{"t":"cmd","cmd":"toggle_meeting"}     # the meeting screen's Space/Enter
```

The speaker editor is **native to each frontend**: the Rich editor is
`t2.select_speaker_editor()`, and ratatui owns `tui-rs/src/speaker_picker.rs`
(open from the meeting screen's `s`). Both produce the same ordered list and
both write it back through `set_speakers`/the `on_set_speaker_names` seam, so
there is exactly one speakers map on the engine. The `open_speaker_editor`
command remains accepted by `tui_ratatui.py` for a frontend that wants to
delegate to the Rich editor; the ratatui frontend does not send it.

### The settings labels (both frontends, identical strings)

| Setting key | Row title | Off/empty state | On/value state | Badge |
| --- | --- | --- | --- | --- |
| `diarization` | Speaker Labels | `Off: one speaker per transcript` | `Labels who said what in meeting transcripts` | `[OFF]` / `[ON]` |
| `diarization_speakers` | Speaker Count | `Auto-detect the speaker count` | `Expecting N speakers` | `[AUTO]` / `[N]` |
| `diarization_model` | Speaker Model | — | `Model: <id>` | `[MODEL]` |
| `meeting_output_dir` | Transcript Folder | — | `Saved to <path>` | `[DIR]` |
| `meeting_output_format` | Transcript Form | — | `Transcript form: <form>` | `[FORM]` |

The row titles match the Python settings menu exactly; the descriptions and
badges are the strings `t2.*_setting_state()` returns, and the ratatui source
literals are pinned against them by the parity guards.

## Progress state

`status` (and the TUI) carry a meeting sub-state:

| Key | Meaning |
| --- | --- |
| `meeting` | effective toggle (`off`/`on`) |
| `meeting_setting` | configured toggle |
| `meeting_state` | `idle`, `recording` or `processing` |
| `meeting_elapsed_s` | seconds since capture began; frozen once processing starts |
| `meeting_progress` | batch-phase completion, 0–100 |
| `meeting_live_transcript` | provisional transcript of the running capture (empty unless recording) |
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

### The live transcript (what the screen shows while recording)

A meeting that showed only a timer would be a poor way to notice the microphone
is wrong, so the ratatui meeting screen also renders **the words spoken so far**.
Three things about it are deliberate:

- **It is a view, not the transcript.** A second `StreamingMicroBatcher` is
  created when a capture starts and released the instant it stops
  (`_start_meeting_live_batcher` / `_stop_meeting_live_batcher` in `main.py`).
  The artifact that gets written is still the diarized batch one produced after
  the stop, so the live view is display-only: it may contain ASR noise the
  written transcript cleans up, and it does not slow the batch pass down by
  holding the ASR lock any longer than it must.
- **It has no speaker labels and never will, until a streaming diarizer exists.**
  Offline clustering cannot know the speaker count from the first seconds of a
  meeting, so labels are assigned after the stop (see `plan-diarization.md`
  §4.5 for the one model that would change that). The screen says so rather than
  leaving the reader to wonder why every line looks like it came from one person.
- **It is tapped, not captured twice.** `MeetingSession(on_chunk=…)` forwards each
  captured chunk from `_append_chunk`; `main.py` feeds that to the display
  batcher. The session stays model-free (it only forwards), the tap runs on the
  capture thread and only appends to a queue, and a raising tap is swallowed -
  a lost line must never wedge a capture that is otherwise recording fine.

The wire message is its own (`{"t":"meeting_text","text":"…"}`) rather than
part of the `state` update, which carries the elapsed timer and is re-sent on
every progress tick. The Rust side renders a **fixed number of lines** holding
the wrapped *tail* of the text: the row feeds the front end's reserved status
height, so a growing transcript must not move the layout.

The **Rich** front end does not render this block: it is a scrolling REPL rather
than a fixed layout, and printing on every chunk would fight the prompt. It gets
the text on `status` like every other front end, and `_on_meeting_live_text`
tolerates a front end with no `set_meeting_live_text` at all.

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
