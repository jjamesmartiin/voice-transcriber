# Plan: Structured Formatting & Error Correction

**Status:** draft for review
**Branch:** `feat/structured-formatting` (worktree: `~/gitprojects/voice-transcriber-fmt`)
**Base:** `main` @ `0656d57 release: v1.3.1` — target release v1.4.0
**Owner:** James
**Priority (2026-10-08):** Workstream B (error correction) first, then Phases 1-4
(structure/formatting), then Workstream C (diarization, separate document).

## 1. Goal

Dictated speech should come out *structured* the way it was *spoken*, not as one
flat wall of prose — and, more importantly, the post-processor must stop
**corrupting** it. Today it does both badly enough to be visible in the
project's own advertised demo (section 9).

The motivating utterance (spoken with pauses):

> Okay I can start pressing and holding to record here and it'll just work. I
> can say, "I'm going to do three things here," and then it'll do three
> different things. I can list them like: Thing one. Thing two. Thing three.

Desired clipboard/paste output:

```
Okay I can start pressing and holding to record here and it'll just work. I can say "I'm going to do three things here" and then it'll do three different things. I can list them like:

- Thing one
- Thing two
- Thing three
```

Desired `type`/`type_fast` output (no `Enter`, see section 5): the same text with
the list rendered inline, e.g. `... I can list them like: - Thing one. - Thing
two. - Thing three.`

## 2. What happens today (measured, not assumed)

Run offline against the real post-processor (`skip_slm=True`, model-free):

```
in:  ... I can list them like: Thing one. Thing two. Thing three.
out: ... I can list them like: thing one. Thing two thing three.
```

Three defects, all in `src/voice_transcriber/post_processor.py`:

| # | Symptom | Where |
| --- | --- | --- |
| D1 | `like: Thing` -> `like: thing` — a colon legitimately introduces a capital, but the mid-sentence-casing pass lowercases it | `normalize_mid_sentence_casing()` (~:251) |
| D2 | `. Thing two.` -> ` Thing two` — the "dangling word before a period" repair listed the cardinals `two/three/four/five`, deleted the period, and handed the joined text to the serial-number collapser (`One. Two. Three.` -> `One. 23.`) | `DANGLING_WORDS_REGEX` + `process_serial_numbers()` |
| D3 | *Premise corrected by measurement:* literal quotes already survive intact (`I can say, "X," and` round-trips). The real gap was **spoken** quotation marks — `quote ... unquote` stayed as words | no spoken-quote handling anywhere |

Structural limits (not bugs — missing machinery):

- **No structure concept exists.** No bullet, list, or newline logic anywhere in
  `src/`.
- **Pause boundaries are destroyed before formatting can use them.**
  `micro_batcher.finish_and_get_text()` and `transcribe_cohere.py:498` stitch ASR
  results with `" "`. The micro-batcher *knows* where the silences were
  (`_dispatch_current_chunk`, `is_forced` / energy-trough vs. silence cut) but
  throws that away.
- **Newlines themselves survive** `clean_speech_transcription()` (verified:
  `"First item.\nSecond item.\nThird item."` round-trips unchanged). The pipeline
  could emit them; it just cannot produce them.

Config interaction, also measured: the live `config/config.yaml` has
`preset: no_punctuation` + `auto_type_auto_punctuate: false`, so punctuation is
stripped wholesale *after* cleaning — and `t2.set_output_mode()` **silently
forces `punctuation_mode = "full"`** whenever typing is enabled with
`AUTO_TYPE_AUTO_PUNCTUATE` on. Any new structure setting must be orthogonal to
the preset and must not be clobbered by that side effect.

## 3. Where the code lives

| Concern | File |
| --- | --- |
| All text cleaning, punctuation presets, dictionary | `src/voice_transcriber/post_processor.py` (shim `src/post_processor.py`) |
| Settings globals, persistence, preset presentation | `src/voice_transcriber/t2.py` (`DEFAULT_SETTINGS` ~:150, `load` ~:763, `save_audio_config` ~:1027) |
| Chunk stitching, pause detection, dedup | `src/voice_transcriber/micro_batcher.py` |
| Recording lifecycle, typing sink | `src/voice_transcriber/main.py` (~:1000-1075) |
| Verb catalogue (source of truth for the API) | `src/voice_transcriber/control.py` -> `VERBS` |
| Rich TUI settings modal | `src/voice_transcriber/t2.py` |
| ratatui settings modal (mirrored literals) | `tui-rs/src/settings_picker.rs` |
| Spec docs | `docs/mode_presets.md`, `docs/control_api.md`, `docs/architecture.md`, `README.md` |

## 4. Phases A — Structure & formatting

### Phase A1 — Punctuation repair (D1–D3)

No newlines, no new settings, no behaviour change for anyone not hitting these
patterns. Safe as a standalone patch.

- **D1 (done):** a colon or dash (`:` `-` `\u2013` `\u2014`) before a capital is a
  list cue, so `normalize_mid_sentence_casing()` keeps that capital. Implemented
  by capturing the character preceding the match *before* the previous-word scan
  reuses the loop index — the first attempt read the clobbered index and silently
  did nothing.
- **D2 (done):** `DANGLING_WORDS_REGEX` no longer lists the cardinals
  `two/three/four/five`. A cardinal is not a dangling preposition, and deleting
  the period after it both joined real sentences and triggered the serial-number
  collapse.
- **D3 (done, reframed):** literal quotes turned out to be fine, so the work was
  spoken quotation marks: `process_spoken_quotes()` rewraps a matched
  `quote ... unquote` / `end quote` pair. A lone "quote" ("this quote is great",
  "and I quote", "I quoted him") is never touched.

Tests: extend `tests/shared/test_post_processor.py`. Regression pins encode the
user's sentence as a whole (input -> exact expected output), plus negative cases
proving ordinary "false break" collapsing still works (stutter, dangling
preposition, `. and` -> `, and`). Docs: `docs/mode_presets.md` section 3 gains the
enumeration rule.

### Phase A2 — Deterministic structure stage

New pure function `process_structure_blocks(text, *, boundaries=None, mode=...)`
in `post_processor.py`, called from `clean_speech_transcription()` **after**
cleaning/conjunctions but **before** `apply_punctuation_mode()` (the preset's
`_strip_punctuation` would eat the markers otherwise — see hazard H3).

Cues to detect (each independently testable):

1. Explicit introducers: `... like:`, `... the following:`, `... these things:`,
   `... as follows:`, `here's a list`.
2. Spoken markers: "bullet point", "new line", "new paragraph", "next item".
3. Enumerations: `thing one/two/three`, `first/second/third`, `one/two/three` as
   parallel sentence openers, `step one/two`, `item 1/2/3`.
4. Parallel openers: >=3 consecutive short sentences sharing a head token.

Emit: `\n- item` for unordered, `\n1. item` for ordinal cues, `\n\n` between
paragraphs. Rules are greedy-safe: if the detector is not confident, it returns
the input unchanged (no partial surprises).

Tests: new `tests/shared/test_structure_blocks.py` (table-driven, model-free).
Eval: add input->expected pairs to `eval/` for the formatting axis.

### Phase A3 — Pause-aware boundaries (the real fix)

Regex cues only rescue *spoken* cues. A pause is a stronger signal than any word,
and the micro-batcher already has it.

- `micro_batcher`: record, per stitched chunk, the boundary kind (`silence` >=
  `min_silence_sec` / `forced` energy trough) and the measured gap.
- Stitch with a private **sentinel** (`\x1e`, ASCII record separator) — *not* a
  literal `\n`, because a raw newline that survives to the typing sink presses
  **Enter** (see H1).
- `clean_speech_transcription()` consumes the sentinel: with structure off, the
  sentinel becomes a space (today's behaviour, byte-identical); with structure
  on, it becomes a real boundary.
- Keep `micro_batcher`'s public return type stable where possible, or return a
  small `StitchedTranscript(text, boundaries)` object and adapt `main.py`.

Tests: `tests/shared/test_micro_batcher_fast.py` for boundary recording; e2e
audio fixture for the full path (local only, per `docs/TODO.md`).

### Phase A4 — Setting, through every surface

| Layer | Change |
| --- | --- |
| Defaults | `t2.DEFAULT_SETTINGS["STRUCTURE_MODE"] = "off"` + save/env key `VT_STRUCTURE_MODE` |
| Post-processor | `set_structure_mode()` / `get_structure_mode()`, `structure_mode=` kwarg on `clean_speech_transcription()` |
| Control API | `VERBS["structure"] = {choices: [...], required: True}`; add `structure_mode` to `status.returns`; `docs/control_api.md` |
| Rich TUI | settings-modal row + picker, driven from a presentation table (same pattern as `PRESET_PRESENTATIONS`) |
| ratatui | `tui-rs/src/settings_picker.rs` row + mirrored preview, pinned by a `test_preset_previews_match_the_spec`-style test |
| Docs | `README.md` settings table, `docs/mode_presets.md` (or sibling `docs/formatting.md`), `docs/architecture.md` pipeline description, `CHANGELOG.md` |

Proposed values (see section 5 for the safety rule):

| Value | Meaning |
| --- | --- |
| `off` | Today's flat prose. Default — no surprise formatting. |
| `inline` | List cues rendered inline (`... like: - a. - b. - c.`). Never emits a newline. Safe for typing. |
| `blocks` | Real `\n- item` bullets and `\n\n` paragraph breaks. Clipboard/paste only. |

## 5. The typing-safety rule (decided)

Newline *typing* works: `platform/linux/hotkeys.py:194` maps `"\n"` -> `KEY_ENTER`;
the Windows path maps CRLF -> `VK_RETURN`. But Enter **sends the message** in
Slack/Discord/Teams and **executes** in a terminal.

**Decided (2026-10-08): the engine never injects a newline.** `Shift+Enter` is not
a portable newline — some applications ignore it — so there is no
one-size-fits-all key for "new line". Therefore:

- `blocks` **downgrades to `inline` in `type`/`type_fast` mode**, and both UIs
  plus `status` report it as `blocks (clipboard only)`.
- Real line breaks are emitted **only** for clipboard/paste output (`output
  clipboard`, `copy_to_clipboard`), where pasting is a deliberate act.
- No "allow Enter in typed output" opt-in is planned. Typed structure would have
  to solve the newline-key problem first (e.g. per-application keymaps), which is
  its own feature.

## 6. Hazards to keep in mind

- **H1 — Enter.** Any newline that reaches `type_text()` presses Enter.
- **H2 — Preset clobbering.** `t2.set_output_mode()` / `cycle_output_mode()` force
  `punctuation_mode = "full"` when `AUTO_TYPE_AUTO_PUNCTUATE` is set. The
  structure setting must not be written from those paths, and existing tests that
  assert that behaviour must keep passing.
- **H3 — Ordering.** `apply_punctuation_mode()`'s `_strip_punctuation()` deletes
  `[^\w\s-]`, which would erase `- ` bullets and the sentinel. The structure stage
  must run first, and the preset must treat already-structured text sanely (e.g.
  `no_punctuation` must not reflow a bulleted list into prose).
- **H4 — Transport parity.** Windows/WSL use a different transport, not a
  different API; the setting must be visible and identical everywhere. The
  ratatui frontend is Unix-only.
- **H5 — Flakes only see git-tracked files.** `git add` every new file (new
  tests, new docs) before claiming a Nix build or `nix run` works.
- **H6 — Worktree lacks gitignored state.** `models/`, `dist/`, and
  `config/config.yaml` are untracked, so this worktree has no weights and no live
  user config. Shared tests are model-free and must stay so; drive the app here
  with `VT_CONTROL_SOCKET` pointed at this worktree to avoid touching the user's
  running instance.
- **H7 — Legacy `formatting_level`.** `t2.load()` still reads `formatting_level`
  as a preset fallback, and a stale `feature/formatting-levels` branch (142
  commits behind) implements an incompatible `formal|semi_formal|informal` notion
  of the same name. Keep reading the key for back-compat, stop writing it, and do
  **not** revive that branch — it is superseded by the preset system.
- **H8 — Editing from the main session.** This session's `edit`/`write` tools are
  scoped to the main worktree, so worktree edits go through `bash` or through a
  subagent launched with `cwd` set to the worktree. Worth knowing before trusting
  a "the file is updated" claim: verify with `git diff`.

## 7. Test & doc surface to update

- `tests/shared/test_post_processor.py` — D1–D3 and B1–B5 regression pins.
- `tests/shared/test_structure_blocks.py` — new, table-driven.
- `tests/shared/test_mode_presets.py` — the preset idempotence/difference matrix
  must still hold with structure off; structure on must not break `full`'s
  identity property.
- `tests/shared/test_config_sync.py` — the new key round-trips and appears in
  `DEFAULT_SETTINGS`.
- `tests/shared/test_control.py` — the `structure` verb dispatch + `help`
  catalogue (no hardcoded verb counts).
- `tests/wsl/*`, `tests/windows/*` — structural pins if the typing path gains a
  newline branch.
- `eval/` — formatting and retraction cases with expected output.
- Docs per Phase A4, plus `docs/TODO.md` for anything only verifiable on a real
  Windows host.

## 8. Milestones

| M | Content | Exit criteria |
| --- | --- | --- |
| M0 | Worktree fix (`da67cf6`) | `find_repo_root()` finds a worktree; `tests/shared` green in the worktree — **done** |
| M1 | Workstream B: error correction (B1–B5) | **done** — the advertised demo resolves, the five false positives are refused |
| M2 | Phase A1 (D1–D3) | **done** — enumerations survive, list cues keep their capital, spoken quotes are rewrapped |
| M3 | Phase A2 + eval cases | Table-driven structure tests green; `off` byte-identical to today |
| M4 | Phase A3 (sentinel + boundaries) | **done** — pause sentinels carried from the micro-batcher; hard cut = paragraph, soft cut = break, `off` byte-identical |
| M5 | Phase A4 (setting end-to-end) | **done** — config + `structure` verb + `status` keys + Rich settings row + ratatui row; docs and `CHANGELOG.md` updated |

Each milestone is its own commit series on `feat/structured-formatting`; the
branch merges to `main` only with `./scripts/test.sh` green.

## 9. Workstream B — Error correction (priority)

The user's priority is not *formatting* speech, it is *not corrupting* it.
Measured against `clean_speech_transcription(..., skip_slm=True)` on the shipped
tree, the retraction path is the worst offender in the codebase — and the case
the README advertises is the one that loses the most text:

```
in : remind me tuesday no wait make that wednesday uh and uh also add a note to the github issue
out: remind make that wednesday and also add a note to the GitHub issue.
```

`"me tuesday no wait"` is deleted **and** the words `make that` survive as literal
text. `README.md:15` and `docs/blog_post.md:147-153` both advertise this exact
utterance resolving to `Remind me Wednesday. Also add a note to the GitHub
issue.` — so the shipped demo is broken, not merely imperfect.

Full measured table (`mode=full`), before and after M1:

| Spoken | Before (v1.3.1) | After (M1) |
| --- | --- | --- |
| `Remind me Tuesday no wait make that Wednesday` | `remind make that wednesday.` | `Remind me Wednesday.` |
| `Remind me Tuesday, actually make that Wednesday` | `remind make that wednesday.` | `Remind me Wednesday.` |
| `Let's make it on Tuesday, no, Wednesday` | `let's make it on Tuesday, no, wednesday.` | `Let's make it on Wednesday.` |
| `Let's make it Tuesday no Wednesday` | `let's make it tuesday no wednesday.` | `Let's make it Wednesday.` |
| `Let's make it on Tuesday. No. Wednesday.` | `let's make it on Tuesday. No, wednesday.` | `Let's make it on Wednesday.` |
| `The meeting is on Monday no Tuesday` | `the meeting is on Monday no tuesday.` | `The meeting is on Tuesday.` |
| `Let's meet at 5 PM... actually 6 PM` | `Let's meet at 6 PM.` | unchanged (already worked) |
| `Please make that happen with the new build` | unchanged | unchanged (the guard refuses it) |

The README/blog utterance now yields `remind me wednesday also add a note to the
GitHub issue.` — the correction resolves and every word survives. It is **not**
two sentences, and no code path can produce two: the optional SLM prompt forbids
adding punctuation that was not spoken. `docs/blog_post.md` advertised
`"Remind me Wednesday. Also add a note to the GitHub issue."`, so the doc was
corrected rather than the claim preserved.

**Status: M1 implemented.** `process_verbal_retractions()` was rewritten with a
chained-trigger regex (`no wait make that` is one retraction), a value-category
guard, and a kept span for pronouns/determiners so `me` is not eaten with the
date. Weekday capitals are preserved by `normalize_mid_sentence_casing()` (the
month rule's sibling), and the five false positives above are refused by two
independent checks. `tests/shared` is green with these cases pinned.

### Causes

`process_verbal_retractions()` + `RETRACTION_REPLACEMENT_PATTERNS[1]`
(`post_processor.py:324-406`):

| # | Bug |
| --- | --- |
| B1 | The correction group is greedy across a *second* trigger, so `no wait make that wednesday` matches `no wait` and then captures `make that wednesday` as the correction. Fix: a correction may not begin with a trigger phrase; chained triggers collapse into one. |
| B2 | The retracted group grabs up to two arbitrary tokens before the value, so `me tuesday` is removed wholesale and `remind me ...` loses `me`. Fix: the retracted span is the value itself, and any prefix the match consumes must be preserved. |
| B3 | Bare `no` is not a trigger at all — only `no wait` / `actually` / `make that` / `I mean` / `or rather` are (`has_1`). This is the most common spoken form (`"Tuesday, no, Wednesday"`). Fix: add `no` as a trigger **guarded by same-category values** (both days, both dates, both clock times, both numbers, both capitalised names) plus a comma/pause, so `"there's no Wednesday meeting"` and `"I have no idea"` are untouched. |
| B4 | Day names are never title-cased and are actively lowercased (`Wednesday` -> `wednesday`) — months have `_title_month()` but the days have no equivalent. Fix: a day-name pass parallel to the month handling, ahead of the mid-sentence-casing pass. |
| B5 | `RETRACTION_TRIGGER_1` includes bare `"mean"` and `has_0` includes bare `"never"` — far broader than the phrases they stand for. Fix: match phrases, not substrings. |

Tests: table-driven in `tests/shared/test_post_processor.py`, including negative
cases (`there is no Wednesday meeting`, `I have no idea`, `I never said that`)
that must pass through unchanged. The README/blog utterance is pinned verbatim, so
the advertised demo cannot silently break again.

## 10. Workstream C — Diarization (separate document, deferred)

Multi-speaker transcription is a *different mode*, not a formatting option: it
needs speaker labels, per-speaker segmentation, and an output convention. It does
not belong in the post-processor or in this branch — it needs its own plan
covering model support (Cohere Transcribe exposes none today), an optional
diarization pass (e.g. a pyannote-style model) plus assignment of ASR segments to
speakers, the `[Speaker 1]` output convention, the latency budget, and the
toggle. Tracked in `docs/TODO.md`. **Plan written: [`docs/plan-diarization.md`](plan-diarization.md)**
(not started; its first step is a product decision, not code).

## 11. Design principle: everything is a toggle

Stated goal for the product: **every feature can be switched on and off**, and
set up, through a setting — no behaviour a user cannot turn off, and no feature
reachable only by hand-editing config. Consequences for this work:

- The structure stage defaults to `off`; `off` must be byte-identical to today.
- The structure setting is orthogonal to the punctuation preset: neither may
  implicitly change the other (see hazard H2).
- Every new toggle lands in `DEFAULT_SETTINGS`, is settable from the settings
  modal in **both** frontends, and is discoverable through the control API
  (`help --json` is the catalogue — never a hardcoded list in the docs).

## 12. Resolved decisions

1. **Typed newlines** — resolved: never inject `Enter`; bullets are
   clipboard/paste-only (section 5).
2. **Value naming** — resolved: `off | inline | blocks` are the canonical values.
   `bullets`, `full` and `lists` stay accepted as *input* aliases, so nobody who
   thinks in terms of "bullets" is locked out, but they are not what the modal
   or the config file shows. Two reasons, both concrete rather than aesthetic:
   * **`full` is already taken.** It is the canonical *cleanup* mode, and the
     same settings list renders it as a `[FULL]` badge
     (`cleanup_modes.md`). Reusing it would put two identical badges on two
     unrelated axes in one modal.
   * **The dangerous axis is transport, not appearance.** `inline` never emits a
     newline; `blocks` does, and a newline is an `Enter` keypress in whatever
     window holds focus. "bullets" describes how the result looks but says
     nothing about the hazard that makes this setting worth being careful with.
3. **Example corpus** — none available for now, so M1/M2 proceed from the measured
   failures above and the README/blog utterances, which are authentic (written
   from real dictation).
4. **Paragraph breaks** — resolved: **a pause is a paragraph on its own; no spoken
   cue is required, and no larger millisecond threshold is used.** The pause must
   simply sit at a *clause boundary*.

   The original question assumed the choice was temporal (is 250 ms enough, or
   should it be ~700 ms?). Measurement says the temporal axis is the wrong one:
   the micro-batcher cuts at `min_silence_sec = 0.25`, and it reports only the
   *kind* of cut (hard = clean silence, soft = forced energy trough), not a
   duration. More importantly, 700 ms is no more a paragraph than 250 ms is when
   the pause lands mid-clause. Before this was fixed, `blocks` mode produced:

   ```
   in   : I was going to the [pause] store and then home.
   out  : I was going to the\n\nstore and then home.     <- blank line mid-sentence
   ```

   and likewise after a comma (`"First we plan,\n\nthen we ship."`) and after a
   function word (`"so the plan is\n\nwe ship on Friday."`). What separates a
   paragraph boundary from a breath is **linguistic**: whether the pause follows
   a finished sentence or a content word.

   So the rule is now: a pause keeps its kind-based treatment (`hard` -> `\n\n`,
   `soft` -> `\n`) only when the text before it ends a clause — a terminal
   `.`/`?`/`!`, or a content word. Straight after a comma, semicolon, colon, or a
   function word (`the`, `and`, `is`, `to`, ...) it joins with a space, exactly as
   `inline` does. See `_pause_is_a_boundary()` in `post_processor.py`.

   This keeps both shapes that matter: the pause-separated list
   (`"milk [pause] eggs [pause] bread"` still becomes paragraphs / bullets) and
   the sentence-per-paragraph case, while removing the mid-sentence blank line.
   `off` and `inline` are untouched - they flatten every pause to a space.
