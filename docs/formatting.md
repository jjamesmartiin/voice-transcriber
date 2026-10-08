# Structured Output: Lists, Bullets and Breaks

The **structure stage** turns spoken layout into written layout: a dictated list
becomes a bulleted list, a spoken "new paragraph" becomes a paragraph break.

It is implemented by `src/voice_transcriber/post_processor.py` →
`process_structure_blocks()` and enforced by
`tests/shared/test_structure_blocks.py`.

## 1. The three modes

| Mode | Emits | Safe for |
| --- | --- | --- |
| `off` | nothing — the identity | everything; **default** |
| `inline` | markers only (`- item`, `1. item`), never a newline | typing (`type`, `type_fast`) |
| `blocks` | real bullets and paragraph breaks (`\n- item`, `\n\n`) | clipboard / paste |

Aliases: `none`/`flat`/`no` → `off`; `dashes`/`safe`/`single-line` → `inline`;
`bullets`/`full`/`lists` → `blocks`. An **unrecognised** value means `off`, not a
guess: failing to `off` is the only safe failure for a formatter that injects
keystrokes.

### The newline rule

`blocks` is never used for auto-typed text. A newline is an `Enter` keypress to
every sink, and `Shift+Enter` is not a portable newline — some applications
ignore it — so there is no one-size-fits-all key for "new line". Enter **sends**
in Slack/Discord and **executes** in a shell. Callers that type must downgrade
`blocks` to `inline`.

## 2. What it recognises

| Spoken | Becomes |
| --- | --- |
| An introducer, then parallel items: `I can list them like: Thing one. Thing two.` | a bulleted list |
| Three or more short parallel sentences: `Deploy alpha. Deploy beta. Deploy gamma.` | a bulleted list |
| Ordinal-labelled items: `Thing one. Thing two.` | a bulleted list |
| Numbering labels: `Step one: install. Step two: configure.` | `1. install` / `2. configure` |
| `bullet point` / `next bullet` (after a clause break) | starts a bullet |
| `new line` / `next line` (after a clause break) | a line break |
| `new paragraph` (after a clause break) | a paragraph break |

Introducers: a colon, or `like`, `as follows`, `the following [things]`,
`these things`, `here's/here are the [things]`.

## 3. What it deliberately does not do

* **Prose is never reformatted.** A cue with one item (`Note: Remember this.`),
  two sentences with no cue, or parallel sentences whose only shared head word is
  a pronoun or conjunction (`We shipped it. We tested it.`) stay as prose.
* **No casing or wording is invented.** Items keep their words; in `blocks` mode
  a single trailing `.` is dropped per item because bullets do not need one.
  Layout cues are the only words that are removed (`Step one:` → `1.`).
* **`first`/`second` are not renumbered.** They stay bullets: turning
  "First, update the config" into "1. update the config" would delete a spoken
  word for no layout gain, and the list already reads as ordered.
* **No pause-based boundaries yet.** Silence is a stronger signal than any cue
  word, but the micro-batcher's segment boundaries are not carried this far down
  the pipeline yet — that is Phase A3 in
  `docs/plan-structured-formatting.md`.

## 4. Invariants

1. `off` is the identity — byte for byte, including whitespace.
2. `inline` output contains no `\n`.
3. An unknown mode is `off`; a missing mode falls back to the configured one.
4. The punctuation presets never flatten a list built here: `_strip_punctuation()`
   keeps newlines and a leading `- `/`1. ` marker, so `no_punctuation`, `gen_z`
   and `aesthetic_lowercase` lowercase and strip text *inside* the items while the
   list survives.
5. The stage is a pure function of `(text, mode)`; it reads no config, clock or
   network.

## 5. Worked example

```
in : Okay I can start pressing and holding to record here and it'll just work. I
     can say I'm going to do three things here and then it'll do three different
     things. I can list them like: Thing one. Thing two. Thing three.
```

`inline` (what auto-typing gets):

```
Okay … I can list them like: - Thing one. - Thing two. - Thing three.
```

`blocks` (what a paste gets):

```
Okay … I can list them like:

- Thing one
- Thing two
- Thing three
```

## 6. Where the strings live

| Surface | Location |
| --- | --- |
| Behaviour (single source of truth) | `src/voice_transcriber/post_processor.py` → `process_structure_blocks()` |
| Mode names and aliases | `post_processor.normalize_structure_mode()` |
| Cue vocabulary | `post_processor._LIST_CUE_BODY`, `_SPOKEN_BREAK_REGEX`, `_NUMBER_LABEL_PREFIX_REGEX` |
| Enforcement | `tests/shared/test_structure_blocks.py` |
| Design and phasing | `docs/plan-structured-formatting.md` |
