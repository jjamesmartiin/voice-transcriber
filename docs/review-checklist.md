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
