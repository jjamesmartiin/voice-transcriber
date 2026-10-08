# Plan: Structured Formatting (lists, paragraphs, punctuation repair)

**Status:** draft for review
**Branch:** `feat/structured-formatting` (worktree: `~/gitprojects/voice-transcriber-fmt`)
**Base:** `main` @ `0656d57 release: v1.3.1` — target release v1.4.0
**Owner:** James

## 1. Goal

Dictated speech should come out *structured* the way it was *spoken*, not as one
flat wall of prose — and the post-processor must stop damaging the cues that
mark that structure.

The motivating utterance (spoken with pauses, in this document's terminology a
"cue + enumeration"):

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

Desired `type`/`type_fast` output (no `Enter`, see section 5): the same text
with the list rendered inline, e.g. `... I can list them like: - Thing one. -
Thing two. - Thing three.`

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
| D2 | `. Thing two.` -> ` Thing two` — the "false ASR break" rule swallows a *real* boundary because a short capitalised sentence looks like an artifact | `LOWERCASE_AFTER_PERIOD_REGEX`, step 9 in `clean_speech_transcription()` (~:2445) |
| D3 | Spoken quotes disappear: `I can say, "X," and` loses the quotation; the comma inside the quote is also dropped | no quote handling anywhere |

Structural limits (not bugs — missing machinery):

- **No structure concept exists.** No bullet, list, or newline logic anywhere in
  `src/`.
- **Pause boundaries are destroyed before formatting can use them.**
  `micro_batcher.finish_and_get_text()` and `transcribe_cohere.py:498` stitch
  ASR results with `" "`. The micro-batcher *knows* where the silences were
  (`_dispatch_current_chunk`, `is_forced` / energy-trough vs. silence cut) but
  throws that away.
- **Newlines themselves survive** `clean_speech_transcription()` (verified:
  `"First item.\nSecond item.\nThird item."` round-trips unchanged). The
  pipeline could emit them; it just cannot produce them.

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

## 4. Phases

### Phase 1 — Punctuation repair (D1–D3)

No newlines, no new settings, no behaviour change for anyone not hitting these
patterns. Safe as a standalone patch.

- **D1:** in `normalize_mid_sentence_casing()`, treat `:` as a boundary that
  *permits* a capital (list cue / label), i.e. exclude it from the
  "lowercase this" decision.
- **D2:** add an enumeration guard to the step-9 collapse: do not delete a
  boundary when the following token is capitalised **and** the sentence it
  starts is short **and** the pattern is repeated/parallel (>=2 occurrences).
  Never delete a boundary the guard classifies as enumeration, even if the rest
  of the rule fires.
- **D3:** restore spoken quotes: re-wrap quoted speech (`I can say X and ...`
  where X is a full clause) and keep the comma inside the closing quote.

Tests: extend `tests/shared/test_post_processor.py`. Regression pins must encode
the user's sentence as a whole (input -> exact expected output), plus negative
cases proving ordinary "false break" collapsing still works (stutter, dangling
preposition, `. and` -> `, and`). Docs: `docs/mode_presets.md` section 3 gains
the enumeration rule.

### Phase 2 — Deterministic structure stage

New pure function `process_structure_blocks(text, *, boundaries=None, mode=...)`
in `post_processor.py`, called from `clean_speech_transcription()` **after**
cleaning/conjunctions but **before** `apply_punctuation_mode()` (the preset's
`_strip_punctuation` would eat the markers otherwise — see hazard H3).

Cues to detect (each independently testable):

1. Explicit introducers: `... like:`, `... the following:`, `... these things:`,
   `... as follows:`, `here's a list`.
2. Spoken markers: "bullet point", "new line", "new paragraph", "next item".
3. Enumerations: `thing one/two/three`, `first/second/third`, `one/two/three`
   as parallel sentence openers, `step one/two`, `item 1/2/3`.
4. Parallel openers: >=3 consecutive short sentences sharing a head token.

Emit: `\n- item` for unordered, `\n1. item` for ordinal cues, `\n\n` between
paragraphs. Rules are greedy-safe: if the detector is not confident, it returns
the input unchanged (no partial surprises).

Tests: new `tests/shared/test_structure_blocks.py` (table-driven, model-free).
Eval: add input->expected pairs to `eval/` for the formatting axis.

### Phase 3 — Pause-aware boundaries (the real fix)

Regex cues only rescue *spoken* cues. A pause is a stronger signal than any
word, and the micro-batcher already has it.

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

### Phase 4 — Setting, through every surface

| Layer | Change |
| --- | --- |
| Defaults | `t2.DEFAULT_SETTINGS["STRUCTURE_MODE"] = "off"` + save/env key `VT_STRUCTURE_MODE` |
| Post-processor | `set_structure_mode()` / `get_structure_mode()`, `structure_mode=` kwarg on `clean_speech_transcription()` |
| Control API | `VERBS["structure"] = {choices: [...], required: True, returns: [...]}`; add `structure_mode` to `status.returns`; `docs/control_api.md` |
| Rich TUI | settings-modal row + picker, driven from a presentation table (same pattern as `PRESET_PRESENTATIONS`) |
| ratatui | `tui-rs/src/settings_picker.rs` row + mirrored preview, pinned by a `test_preset_previews_match_the_spec`-style test |
| Docs | `README.md` settings table, `docs/mode_presets.md` (or a sibling `docs/formatting.md`), `docs/architecture.md` pipeline description, `CHANGELOG.md` |

Proposed values (see section 5 for the safety rule):

| Value | Meaning |
| --- | --- |
| `off` | Today's flat prose. Default — no surprise formatting. |
| `inline` | List cues rendered inline (`... like: - a. - b. - c.`). Never emits a newline. Safe for typing. |
| `blocks` | Real `\n- item` bullets and `\n\n` paragraph breaks. |

## 5. The typing-safety rule (needs a decision)

Newline *typing* works: `platform/linux/hotkeys.py:194` maps `"\n"` ->
`KEY_ENTER`; the Windows path maps CRLF -> `VK_RETURN`. But Enter **sends the
message** in Slack/Discord/Teams and **executes** in a terminal. A formatter
that injects newlines into a chat box is a footgun, not a feature.

Recommendation: `blocks` **downgrades to `inline` in `type`/`type_fast` mode**,
and both UIs plus `status` display it as `blocks (clipboard only)`. Only
clipboard/paste output (`copy_to_clipboard`, `output clipboard`) gets real line
breaks. A separate opt-in for typed newlines can be added later if wanted — it
should not be the default, and it should be its own documented setting, not a
silent consequence of `blocks`.

## 6. Hazards to keep in mind

- **H1 — Enter.** See section 5. Any newline that reaches `type_text()` presses Enter.
- **H2 — Preset clobbering.** `t2.set_output_mode()` / `cycle_output_mode()`
  force `punctuation_mode = "full"` when `AUTO_TYPE_AUTO_PUNCTUATE` is set. The
  structure setting must not be written from those paths, and existing tests
  that assert that behaviour must keep passing.
- **H3 — Ordering.** `apply_punctuation_mode()`'s `_strip_punctuation()` deletes
  `[^\w\s-]`, which would erase `- ` bullets and the sentinel. The structure
  stage must run first, and the preset must treat already-structured text
  sanely (e.g. `no_punctuation` must not reflow a bulleted list into prose).
- **H4 — Transport parity.** Windows/WSL use a different transport, not a
  different API; the setting must be visible and identical everywhere. The
  ratatui frontend is Unix-only.
- **H5 — Flakes only see git-tracked files.** `git add` every new file (new
  tests, new docs) before claiming a Nix build or `nix run` works.
- **H6 — Worktree lacks gitignored state.** `models/`, `dist/`, and
  `config/config.yaml` are untracked, so this worktree has no weights and no
  live user config. Shared tests are model-free and must stay so; drive the app
  here with `VT_CONTROL_SOCKET` pointed at this worktree to avoid touching the
  user's running instance.
- **H7 — Legacy `formatting_level`.** `t2.load()` still reads
  `formatting_level` as a preset fallback, and a stale
  `feature/formatting-levels` branch (142 commits behind) implements an
  incompatible `formal|semi_formal|informal` notion of the same name. Keep
  reading the key for back-compat, stop writing it, and do **not** revive that
  branch — it is superseded by the preset system.

## 7. Test & doc surface to update

- `tests/shared/test_post_processor.py` — D1–D3 regression pins.
- `tests/shared/test_structure_blocks.py` — new, table-driven.
- `tests/shared/test_mode_presets.py` — preset idempotence/difference matrix
  must still hold with structure off; structure on must not break `full`'s
  identity property.
- `tests/shared/test_config_sync.py` — new key round-trips and appears in
  `DEFAULT_SETTINGS`.
- `tests/shared/test_control.py` — the `structure` verb dispatch + `help`
  catalogue (no hardcoded verb counts).
- `tests/wsl/*`, `tests/windows/*` — structural pins if the typing path gains a
  newline branch.
- `eval/` — formatting cases with expected output.
- Docs per Phase 4, plus `docs/TODO.md` for anything only verifiable on a real
  Windows host.

## 8. Milestones

| M | Content | Exit criteria |
| --- | --- | --- |
| M1 | Phase 1 (D1–D3) | The motivating sentence round-trips; `tests/shared` green |
| M2 | Phase 2 + eval cases | Table-driven structure tests green; `off` is byte-identical to today |
| M3 | Phase 3 (sentinel + boundaries) | Pause-separated items list correctly without cue words; model-free tests green |
| M4 | Phase 4 (setting end-to-end) | Control API, both TUI modals, docs, `CHANGELOG.md`; `nix build .#vt-tui` green |

Each milestone is its own commit series on `feat/structured-formatting`; the
branch merges to `main` only with `./scripts/test.sh` green.

## 9. Open questions

1. **Typed newlines** — confirm the section 5 recommendation (blocks downgrade
   to inline when typing) rather than typing Enter.
2. **Value naming** — `off | inline | blocks` vs. `off | bullets | full`. The
   control-API verb name (`structure`) and config key (`structure_mode`) follow
   from it.
3. **Example corpus** — a handful of real dictations with the desired output
   would harden the Phase 2 detector and the eval set far better than invented
   cases. Requested from the user.
4. **Paragraph breaks** — is a spoken pause >= ~700 ms a paragraph break, or only
   when a list/structure cue is present?
