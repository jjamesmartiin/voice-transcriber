# Cleanup Modes: How Much the Pass May Change Your Words

The post-processing pass does two genuinely different jobs, and they are worth
separating because they carry different risk:

* **removing noise** — a hallucination on silence (`"Thank you."` decoded from a
  1 s clip), filler (`"uh"`), stutters (`"the the"`), a clipped onset consonant
  (`"t needs"` → `"needs"`), trailing mutterings (`"…, never mind"`).
* **resolving self-corrections** — you changed your mind, so the retracted words
  go: `"schedule it for Tuesday no sorry Wednesday"` → `"schedule it for
  Wednesday"`.

Implemented by `src/voice_transcriber/post_processor.py` →
`set_cleanup_mode()` and the gates in `clean_speech_transcription()`, enforced by
`tests/shared/test_cleanup_modes.py`.

## 1. The three modes

| Mode | Noise | Self-corrections | Use when |
| --- | --- | --- | --- |
| `off` | kept | kept | you want every word that was said, verbatim |
| `artifacts` | removed | kept | you want the junk gone but your words left alone |
| `full` | removed | resolved | the shipped default: you want the correction to land |

Aliases: `none`/`raw`/`verbatim`/`no`/`disabled` → `off`; `noise`/`safe`/
`cleanup`/`light`/`artifact` → `artifacts`; `all`/`corrections`/`complete`/
`heavy` → `full`.

An **unrecognised** value means `full`, which is the opposite of the structure
modes (where it means `off`) and deliberate: this setting *defaults* to the
shipped behaviour, so a typo must not look like a feature that stopped working.

## 2. What the modes do **not** govern

Number and date formatting, the custom dictionary, casing, the punctuation
preset, spell/serial handling and structured output are separate settings, and
they are not gated here. They change how text *looks* — how many digits, which
word, which case, which punctuation — rather than which words survive.

That line is deliberate: `cleanup_mode: off` with `number_digits: digits` still
writes `25`, and `structure_mode: blocks` still writes your list as a list. What
`off` guarantees is that nothing is **deleted** and nothing is **rewritten as a
correction**.

## 3. Worked example

The sentence that prompted the middle mode — a good dictation, then a correction,
then an apology:

```
in          : Let's schedule it for Tuesday no sorry Wednesday
off         : Let's schedule it for Tuesday no sorry Wednesday.
artifacts   : Let's schedule it for Tuesday no sorry Wednesday.
full        : Let's schedule it for Wednesday.
```

And the silence hallucination, where `artifacts` earns its keep:

```
in          : Thank you.          (a 1s clip of nothing)
off         : Thank you.
artifacts   : (empty)
full        : (empty)
```

## 4. Invariants

1. `full` is the default and reproduces the pre-existing behaviour exactly.
2. `off` deletes nothing: no hallucination stripping, no filler, no stutters, no
   self-correction rewriting — and the model-backed SLM pass does not run either,
   since its contract is retraction and filler removal.
3. `artifacts` never rewrites a correction: the retracted words stay in place.
4. An unknown value is `full`, never a silent `off`.
5. The mode is a single global that callers may pin per utterance
   (`clean_speech_transcription(..., cleanup_mode="off")`), which is how the tests
   exercise it.

## 5. Where the strings live

| Surface | Location |
| --- | --- |
| Behaviour (single source of truth) | `src/voice_transcriber/post_processor.py` → `_CLEANUP_MODE`, `_removes_noise()`, `_resolves_corrections()` |
| Mode names and aliases | `post_processor.normalize_cleanup_mode()` |
| Setting, persistence, env override | `src/voice_transcriber/t2.py` → `CLEANUP_MODE`, `set_cleanup_mode()`, `VT_CLEANUP_MODE` |
| Control API | `src/voice_transcriber/control.py` → the `cleanup` verb, `status.cleanup_mode` |
| Rich settings modal | `src/voice_transcriber/t2.py` → the `cleanup` menu row |
| ratatui settings modal | `tui-rs/src/settings_picker.rs` → `SettingKind::CleanupMode` |
| Enforcement | `tests/shared/test_cleanup_modes.py`, `tests/shared/test_config_sync.py`, `tests/shared/test_control.py` |
