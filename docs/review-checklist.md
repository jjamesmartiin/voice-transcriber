# Review checklist — what to try, and what to expect

For reviewing this session's work by hand. Ordered so the biggest features come first. Every
command is exact; "expect" is what you should actually see, and "wrong if" is how you'd know it's
broken rather than merely different.

## Before you start: the running engine is the OLD release

Your live engine (`vt-1.3.1`, from the nix store) predates all of this — it has none of the new
features, and two engines fight over the same control socket. To review the branch:

```bash
# stop the old one (find it by PID; do NOT pkill -f "src/main.py", the pattern matches your shell)
ps -ef | grep -F main.py | grep -v grep
kill <PID>

# start the branch's engine
cd /home/jamesm/gitprojects/voice-transcriber
nix develop --command python src/main.py
```

`help --json` is the authoritative verb list and works with no engine running:

```bash
nix develop --command python src/main.py help --json
```

---

## 1. Meeting mode (the largest new feature)

Capture is **non-injecting** — it never types into your focused window.

```bash
nix develop --command python src/main.py meeting on          # required first
nix develop --command python src/main.py meeting-start
# ... talk, or play audio at your mic ...
nix develop --command python src/main.py meeting-stop        # processing continues in background
nix develop --command python src/main.py wait --json         # includes the transcript
nix develop --command python src/main.py status              # meeting_stage / % / transcript path
```

**Expect:** a transcript file in the meeting output directory, with a header and one
`[Speaker N]` line per turn, and `status.meeting_stage` progressing while it processes.

**Wrong if:** the process appears to hang with no `meeting_stage` movement (that was the failure
mode the percentage exists to prevent), or the file lands somewhere unexpected — see item 3.

**Diarization (needs the model, ~52 MB, already installed):**

```bash
nix develop --command python src/main.py diarization on
nix develop --command python src/main.py diarization-speakers 2   # hint; omit to cycle, auto is the default
```

**Expect:** the same recording now splits into speaker-labelled turns. With `diarization off` you
get one speaker and a complete transcript anyway — that is the documented fail-safe, not a bug.

**The speaker map (the live editor):** in the TUI meeting screen, `s` opens it. Keys: `j`/`k` move,
`Enter` renames, `a` adds, `d` deletes, `J`/`K` reorder, `s`/`Esc` save. Or scripted:

```bash
nix develop --command python src/main.py speakers                    # list
nix develop --command python src/main.py speakers "Priya,Tom"        # set, ordered
```

**Worth checking specifically:** this was *advertised but unreachable* before today — pressing
`J`/`K` used to just move the highlight. Try a reorder and confirm the transcript labels follow.

**Output location and form** (decision S8 — repo-local and gitignored):

```bash
nix develop --command python src/main.py meeting-output              # report
nix develop --command python src/main.py meeting-format markdown     # text | json | markdown
```

---

## 2. The on-device formatter

```bash
nix develop --command python src/main.py formatter on
nix develop --command python src/main.py formatter-style formal
nix develop --command python src/main.py formatter-context email
nix develop --command python src/main.py formatter-model s1-mini     # the only choice today
```

Dictate something with a self-correction — *"I need to buy milk and eggs, actually no, bread"* —
with the formatter off, then on, and compare. **Expect** the "on" pass to resolve the retraction to
"I need to buy bread and eggs", not merely re-punctuate.

**Wrong if:** the text is *identical* with it on. That is the exact shape of the bug this session
fixed twice: a guardrail rejection is indistinguishable from the formatter being switched off, so
"nothing happened" is the symptom to be suspicious of.

**Spot-check the `off` guarantee** (a non-negotiable rule): with `formatter off`, `structure off`,
`cleanup off`, the transcript must be *byte-identical* to a build without these features. That is
what the tests pin; you can eyeball it by dictating the same phrase in both states.

**Not verifiable yet:** the first-use download. `formatter-download` exists and the size is stated
before anything is fetched, but **no model bundle has been published**, so a genuinely clean machine
cannot complete that path. The model is already installed here, so it will look like it works.

---

## 3. Structured formatting and cleanup (workstream A)

```bash
nix develop --command python src/main.py structure                    # off | inline | blocks
nix develop --command python src/main.py cleanup                      # off | artifacts | full
```

Try a spoken list — *"I need three things: milk, eggs, and bread"* — with `structure blocks` and
then `structure inline`. **Expect** `blocks` to emit real newlines and `inline` never to (a newline
is an `Enter` keypress in whatever window has focus, which is why the axis is named for transport
rather than appearance).

`cleanup off` promises nothing is deleted — it should leave filler words, stutters and
self-corrections alone while still writing numbers and punctuation as configured.

---

## 4. The eval/review artifact (not a runtime feature, but the most reviewable thing here)

This exists so the ASR numbers can be checked rather than believed.

```bash
nix develop --command python eval/review_report.py     # regenerates from recorded results, no model
xdg-open eval/report.html
```

**Do this one with headphones.** It shows, per clip: an inline audio player, the reference
transcript, the hypothesis, and a word-level diff — plus per-slice roll-ups.

The recorded baseline, from `eval/README.md`: **WER 3.57 %**, CER 1.43 %, exact 70.4 %, silence
hallucination 0/12. Two slices are honest weak spots: `long` at 2.19 % WER / 33.3 % exact, and
`accented` at 9.42 % WER / 36.4 % exact (AMI references contain disfluencies the post-processor
deliberately removes, so part of that is an artefact).

**Worth knowing:** this baseline's own `meta` records the exact configuration and a `reproduce`
command, because the previous one claimed a configuration it had not actually used.

---

## 5. Diarization metric

```bash
nix develop --command python eval/score_diarization.py
```

**Expect** `DER 0.0145`, 4 turns, ordering A,B,A,B, PASS. This is a **smoke gate, not an accuracy
claim** — a four-turn clip of maximally separated voices exercises almost none of the failure modes
that matter. The real number would need AMI SDM, which is recorded as a follow-up.

---

## 6. What you cannot review yet — don't go looking

- **The E3 int8-ONNX ASR backend.** The toolchain prerequisite landed and the model provably
  decodes; the feature does not exist. There is no verb for it.
- **The formatter's clean-machine download** (no published bundle).
- **AMI-SDM diarization accuracy** (not measured).
- **Anything on Windows or macOS.** Never verified there; the gaps are logged in `docs/TODO.md`.

---

## 7. Spot-checks that are worth more than they look

- **A dead key that now works:** the speaker editor's `J`/`K` reorder (item 1).
- **A silent failure that is now visible:** enable the formatter with the model deliberately
  missing (`VT_FORMATTER_MODEL_PATH=/nonexistent.gguf`) — you should get unformatted text and a
  *logged reason*, where previously you got unformatted text and nothing else.
- **Nothing leaking processes:** `pgrep -c llama-server` should stay flat while you dictate with the
  formatter on. It used to grow by one per test run.
- **`off` means off:** every feature above has an `off` that must be byte-identical to before. If
  any `off` changes your output, that is a bug worth reporting immediately.

---

## 8. The audit pass (2026-10-10): what to try, and what is still yours to check

Everything below has a model-free test that fails when the fix is reverted, so these are the
*judgement* checks — the ones a test cannot make. Ordered by how much damage a miss would do.

### 8.1 Your live config is the thing to check first

The save path changed from a truncating write to temp-file + `os.replace`, and an unreadable
config is now renamed to `config.yaml.corrupt-<timestamp>` instead of being overwritten with
defaults. Verify against your **real** config:

```bash
cp config/config.yaml /tmp/config.backup.yaml      # belt and braces
nix develop --command python src/main.py           # starts clean, no .corrupt- file appears
ls config/                                          # expect config.yaml and nothing new
```

**Wrong if:** a `config.yaml.corrupt-*` file appears while you did nothing, or any setting you
had set has reverted to a shipped default.

### 8.1b Exercising the backup path without touching your real config

There is no env var for the config path (`get_config_file()` probes a fixed candidate list), so
drive the loader directly against a temp copy. This is the shape of the regression test, run by
hand:

```bash
nix develop --command python - <<'PY'
import os, sys, tempfile
sys.path.insert(0, "src")
import t2
d = tempfile.mkdtemp(); p = os.path.join(d, "config.yaml")
open(p, "w").write("ui_theme: magenta\npunctuation")   # a write cut off mid-file
t2.load_audio_config(p)                                 # warns, falls back to defaults
t2.set_typing_wpm(90)                                   # any settings change saves
print(sorted(os.listdir(d)))
PY
```

**Expect:** `['config.yaml', 'config.yaml.corrupt-<timestamp>']` — the original bytes preserved
in the backup, a fresh valid config, and no `.tmp` file left behind.

### 8.2 The four output fixes, read with your own eyes

These are deterministic, so the suite proves them; what it cannot tell you is whether the result
reads well. Dictate each, then read the injected text:

| Say | Expect |
| :--- | :--- |
| "call 555 1212" | `call 5551212` — still collapses |
| "we need 500 2000 more" | `we need 500 2000 more` — *not* `5002000` |
| "bullet point milk. bullet point eggs." with `structure blocks` | two bullets on separate lines |
| "connect to 10 dot 0 dot 0 dot 0 slash 24" | `10.0.0.0/24` |

Then set `cleanup off` and say *"that is a eyes thing"* — **expect `that is a eyes thing`**,
verbatim. `off` used to quietly rewrite it to `AI`.

**One deliberate regression to judge:** with no preposition, `"call bob I mean alice"` is no
longer resolved (it stays as spoken). That is the cost of stopping the matcher from eating
`"know what"` in `"you know what I mean right"`. If losing that rewrite annoys you more than the
old bug did, say so — the guard is one condition.

### 8.3 Hotkeys on a real desktop (Linux first, then the other hosts)

- With a `meta`-based bind (`hotkeys` in config, or `hotkey add meta+shift`), hold it, release it,
  and make sure **no OS shortcut fires** — the engine now waits for Super to be released before
  typing. Before the fix it typed while Super was still down.
- Toggle middle-click push-to-talk **off while you are holding the middle button**, then dictate
  normally. Expect normal latency. The stale flag used to add the full hold-wait to every
  dictation for the rest of the run.
- In the Rich TUI, press `Esc` — it should quit (it used to do nothing). **Press an arrow key
  too**: that must *not* quit.

### 8.4 A real first-run download, and a real meeting

```bash
rm -rf ~/.local/share/vt/models/cohere     # ~2.8 GB to re-fetch — only if you have the link
nix develop --command python src/main.py
```

**Expect:** the download survives a dropped connection (it retries a truncated part instead of
aborting the install), and a second engine started at the same time waits rather than deleting
the first one's weights. Do this on a tethered or flaky link if you can — a clean fast link will
not exercise it. The revision check itself only proves useful the next time `REVISION` is bumped.

Meeting mode: start a capture, **stop it mid-recording**, then immediately start another and let
it finish. Both should produce transcripts, and the second must not lose its audio (that was the
`close()` race). If you have a way to fill a disk, a short spill file should now mark the
transcript as incomplete rather than presenting it as whole.

### 8.5 Not checkable here at all — say the word and I will stop claiming otherwise

- **Native Windows**: never launched on a Windows machine by this project. The Super/Meta fix,
  the middle-click fix, the `VT_CONTROL_SOCKET` + TCP control API, and the release ZIP all ship
  on tests and review only. `scripts\run.bat doctor` output is the useful thing to send.
- **macOS**: never launched. The middle-click fix is untested there, and the *unfixed* letter
  bind gap still stands — a letter or digit bind on macOS reports success and never fires.
- **WSL**: the PowerShell bridge has still never executed anywhere.
- **`tests/e2e/`**: needs the weights and a microphone, so nothing in it ran for this pass.

### 8.6 Meeting mode: start it from the TUI, and watch it transcribe live

Meeting mode used to be startable only through the control API, and it showed a
timer with no words until you stopped. Both are fixed; this is the manual check.

1. In the settings modal, turn **Meeting Mode** on first (the capture row says so
   if you skip this), then choose **Meeting Capture** → *Start a meeting capture*.
   The modal closes and you land on the meeting screen.
2. Talk. **Expect:** a `❯ MEETING` line with the elapsed timer, and below it the
   words you just said, updating as you speak — the newest few lines, wrapped,
   in a block of fixed height (it must not grow, or the screen would move as you
   talk). A pause shows as a space there; the paragraph break it really is shows
   up in the written transcript.
3. **Expect no floating notification pill.** Meeting capture never shows one
   (the pill belongs to dictation); if you see one, that is a bug.
4. Choose **Meeting Capture** again (or press Space on the meeting screen) to
   stop. **Expect:** the live block is replaced by `Finishing · N%`, and when the
   batch pass finishes you get a transcript file with `[Speaker N]` labels —
   the labels appear only at the end, which is a limitation, not a bug
   (`docs/plan-diarization.md` §4.5 explains why live labelling needs a
   different model).

**Known and deliberate:** the live text is provisional and unfiltered — it may
contain ASR noise that the written transcript cleans up, and it has no speaker
labels. `status.meeting_live_transcript` carries the same text if you want to
watch it from a script:

```bash
nix develop --command python src/main.py status --json | python -c 'import json,sys; print(json.load(sys.stdin)["meeting_live_transcript"])'
```

**Wrong if:** the words never appear while you talk (check `meeting_live_transcript`
in `status` — if that grows, the engine is fine and the front end is not), or the
screen jumps up and down as text accumulates (the live block is meant to hold a
fixed height).
