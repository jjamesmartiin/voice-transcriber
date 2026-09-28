# Mode Presets: Formatting Spec

The five **mode presets** are the user-facing names for the punctuation and
formatting modes implemented by `post_processor.apply_punctuation_mode()`.

This document is the contract for what each preset does, and — more
importantly — for what makes each one *different* from the other four. It is
enforced by machinery, not by good intentions:

| Enforced by | What it locks down |
| --- | --- |
| `tests/shared/test_mode_presets.py` | The previews below are what `apply_punctuation_mode()` really produces, the differences between presets hold, and both UIs advertise the spec's strings. |
| `tui-rs/src/settings_picker.rs` (`test_preset_previews_match_the_spec`) | The ratatui settings modal shows the same previews, and they all fit its columns. |

Change a preset's behaviour, its sample, or the wording of a preview and one of
those fails.

---

## 1. Presets and their aliases

`t2.get_canonical_preset_name()` maps every accepted alias onto a canonical id;
`post_processor.apply_punctuation_mode()` accepts all of them.

| Canonical id | Aliases | Badge | Switcher name | Switcher id |
| --- | --- | --- | --- | --- |
| `full` | `default`, `standard` | `[DEFAULT]` | Default (Standard) | `default` |
| `no_terminal_period` | `casual`, `semi_formal`, `no_period`, `no_ending_period` | `[CASUAL]` | Casual (No Ending Period) | `casual` |
| `no_punctuation` | `autocorrect`, `phone`, `none`, `no_punct` | `[PHONE]` | Autocorrect (Phone Style) | `autocorrect` |
| `aesthetic_lowercase` | `aesthetic`, `lowercase_punct`, `lower_punct` | `[AESTH]` | Aesthetic Lowercase | `aesthetic_lowercase` |
| `gen_z` | `genz`, `pure_gen_z`, `lowercase_no_punctuation`, `lowercase_no_punct` | `[GEN Z]` | Pure Gen Z | `gen_z` |

A non-empty but **unrecognised** mode is a no-op: the text is returned
unchanged. A missing mode (`None` or `""`) is not a no-op — it means "use the
currently configured preset". Aliases are interchangeable — `casual` and
`no_period` must produce byte-identical output.

## 2. The sample sentence

Every preset is demonstrated with the *same* sentence so the differences are
comparable at a glance. It deliberately contains a capitalised first word, a
comma, a question mark, a contraction with a capital `I` (`I'm`), and a final
period — one cue per formatting axis.

```
IN:  Hey, how are you? I'm good.
```

| Preset | Preview (exact, shown in the UI) |
| --- | --- |
| `full` | `Hey, how are you? I'm good.` |
| `no_terminal_period` | `Hey, how are you? I'm good` |
| `no_punctuation` | `Hey how are you I'm good` |
| `aesthetic_lowercase` | `hey, how are you? i'm good` |
| `gen_z` | `hey how are you i'm good` |

Both UIs wrap the preview in quotes (`"Hey, how are you? I'm good."`); the
quotes are presentation, not part of the value.

## 3. Rules

| Preset | Rule |
| --- | --- |
| `full` | Return the text untouched: full punctuation, standard capitalisation and grammar. |
| `no_terminal_period` | Keep capitalisation and internal punctuation; strip a trailing `.` (and the whitespace around it). A trailing `?`/`!` is kept. |
| `no_punctuation` | Strip punctuation, but keep intra-token separators so `I'm`, `don't` and `3.14` survive; re-capitalise a standalone `i` / `i'…` to `I` and the first letter of the text. |
| `aesthetic_lowercase` | Lowercase everything (including `i`), keep internal punctuation, strip a trailing `.`. |
| `gen_z` | Lowercase everything and strip punctuation entirely. |

## 4. Difference matrix

Computed from the previews above. This is the table that has to keep holding —
if a change makes two presets land on the same row, the presets have stopped
being distinguishable and `test_the_differences_hold_up` fails.

| Preset | Keeps internal `,`/`?` | Trailing period | Any uppercase | Strips all punctuation |
| --- | --- | --- | --- | --- |
| `full` | ✅ | ✅ | ✅ | ❌ |
| `no_terminal_period` | ✅ | ❌ | ✅ | ❌ |
| `no_punctuation` | ❌ | ❌ | ✅ | ✅ |
| `aesthetic_lowercase` | ✅ | ❌ | ❌ | ❌ |
| `gen_z` | ❌ | ❌ | ❌ | ✅ |

Every axis is exercised by at least one pair, and no two rows are equal:

* `full` vs `no_terminal_period` — the trailing period.
* `no_terminal_period` vs `no_punctuation` — internal punctuation.
* `no_punctuation` vs `gen_z` — capitalisation.
* `aesthetic_lowercase` vs `gen_z` — punctuation.
* `aesthetic_lowercase` vs `no_terminal_period` — capitalisation.

## 5. Invariants

1. `full` is the identity on the sample.
2. `no_terminal_period` = `full` with a trailing period removed.
3. `no_punctuation` = `no_terminal_period` with punctuation stripped, intra-token
   separators kept, and `I` re-capitalised.
4. `aesthetic_lowercase` = `no_terminal_period`, lowercased.
5. `gen_z` = `no_punctuation`, lowercased.
6. The five previews are pairwise distinct.
7. Every preset is idempotent: applying it twice equals applying it once.
8. An unrecognised (non-empty) mode is a no-op; a missing mode falls back to the
   currently configured preset.

## 6. Where the strings live

| Surface | Location |
| --- | --- |
| Behaviour (single source of truth) | `src/post_processor.py` → `apply_punctuation_mode()` |
| Canonical presentation table | `src/t2.py` → `PRESET_SAMPLE`, `PRESET_PRESENTATIONS`, `PRESET_SWITCHER_ID_BY_CANON`, `get_preset_presentation()` |
| Rich preset switcher | `src/t2.py` → `select_preset_picker()` (reads the table) |
| Rich settings menu | `src/t2.py` → `get_preset_presentation()` (same table) |
| ratatui settings modal | `tui-rs/src/settings_picker.rs` → the `PunctuationMode` arm (mirrored literals) |
| Spec test | `tests/shared/test_mode_presets.py` |
| ratatui-side lock | `tui-rs/src/settings_picker.rs` → `test_preset_previews_match_the_spec` |

The Python surfaces read one table, so they cannot drift from each other. The
Rust row is a separate language, so it keeps mirrored literals: the Rust test
pins them exactly, and `test_ratatui_settings_modal_shows_the_spec` greps the
non-test half of the Rust file for the spec's previews. Adding a preview to one
side without the other fails the suite.
