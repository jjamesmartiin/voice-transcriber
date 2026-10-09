# Handoff — feature-parity session

**Purpose:** everything a fresh session needs to continue this work without
re-deriving it. Deliberately *not* a copy of the task list — that lives in
[`TODO-parity.md`](TODO-parity.md), which is the durable resume point. Read that
first, then this.

Written 2026-10-08, at a point where context was getting long.

---

## 0. Session 2 state — 2026-10-09 (read this before §1)

**Interrupted mid-flight by a network pause — the interrupted work was then recovered,
verified and merged.** Nothing is lost. Every worktree is clean and every commit is on
**github** under `wip/feature-parity-2026-10`. `git.jdm.cx` (gitea) stopped resolving —
re-push when it is back.

**`main` is deliberately untouched** so no release can fire (`release.yml`
triggers only on `v*` tags, verified). This session's owner decisions are recorded
in [`TODO-parity.md`](TODO-parity.md) **§1b as `S1`–`S12`**.

### Committed

| Branch | State |
| --- | --- |
| `wip/feature-parity-2026-10` | **Integration branch.** Docs: §1b decisions, D7/DER design, E3 verification, the regenerated eval baseline, X6. `work/parity-eval` merged. |
| `work/parity-eval` | **Merged.** Baseline regenerated 5.10 %→3.57 % on a clean tree with self-describing `meta`; `eval/review_report.py` → `eval/report.html` (per-clip audio + ref + hyp + diff). |
| `work/parity-formatter` | **MERGED into the integration branch.** 6 commits: C6a registry (adds `FORMATTER` **and `DIARIZATION`** specs, `_model_path` through the registry), C6b first-use prompt with the size, C6c publish path, C9 licence/NOTICE + provenance, C8 docs/CHANGELOG, and the X6 fix + guard pins. |
| `work/parity-meeting` | **MERGED.** 3 commits: `6c01795` A6 (reset takes effect immediately), `da619d6` D6 (diarization settings on every Python surface), `20f73fb` D5a (the live speaker map + a repo-local output directory). The ratatui half is still open. |
| `work/parity-e3flake` | **MERGED.** 1 commit: the narrow `sherpa-onnx` **1.13.3** override. It makes the toolchain *capable* of Cohere Transcribe; it does **not** implement the E3 ASR backend. |

### Working tree state

**All five worktrees are clean — nothing is uncommitted.** The three that were interrupted
mid-flight were checkpointed, finished, verified, reworded into proper commits and merged, so
no recovery depends on an uncommitted file.

The integration branch is **green on the new devshell**: `tests/shared + tests/linux +
tests/wsl + tests/windows` **1613 passed, 3 skipped**, `ruff check src/ tests/` clean, and the
`llama-server` process count is unchanged across runs (no X6 leak).

A note for whoever resumes: three times this session an interrupted agent left behind a
"do not merge as-is" checkpoint commit. Verifying the work and *rewording* the message before
merging matters — a commit that says it must not be merged, sitting in the permanent history,
is worse than no checkpoint at all.

### 0.1 Expensive findings — do not re-derive

* **sherpa-onnx / glibc (new, and the reason `e3flake` pinned a second input).** The locked
  nixpkgs ships **1.12.25** (no Cohere support). Bumping to **1.13.8 does not work**: it is
  linked against **glibc 2.44**, and the locked nixpkgs carries **glibc 2.42**, so it cannot
  be loaded. The working pin is a second input at nixpkgs rev
  `a9630bf480bf699d6792772fd0a5ee1b9084825b`, which provides **1.13.3** (built against glibc
  2.42) and already exposes both `OfflineCohereTranscribeModelConfig` and
  `from_cohere_transcribe`. Also load-bearing: `python.withPackages` **silently drops** any
  package whose `pythonModule` is not the environment's own python, so the python wrapper
  must be rebuilt against the newer set's cached **cpython-313** bindings rather than
  spliced in.
* **X6 — a second `tests/shared` hermeticity hole.** `test_formatter_settings.py::
  test_no_backend_means_the_text_passes_through` is documented "model-free" but loads the
  real 462 MiB formatter and **leaks a `llama-server` per run**; it is **load-dependent** (a
  failed spawn arms a 60 s cooldown, after which it passes) — so *a green run of this tier is
  not evidence*. 42 orphans were killed. The live engine's own server is a child of the engine
  and must be preserved: orphans are distinguishable by parent, never blanket-`pkill`.
* **E3** verified byte-for-byte (sha256 matches the declared digest; all 8 LFS files match the
  HF mirror; decodes `en.wav` at RTF 0.145). `test_wavs/ja.wav` is a **corrupt 145-byte JSON
  error body** in both the tarball and the mirror.
* **eval** 5.10 %→**3.57 %** WER, CER 2.97 %→1.42 %, exact 54 %→70 %. The **`long` slice
  regressed** 1.78 %→2.19 % (worth a bisect), and CIDR `10.0.0.0/24` still renders as
  `10 000/24`.

### Queue on resume

1. Spawn the **Rust/TUI worker** for the live speaker editor. It needs the frozen contract
   (control verbs, `status` fields, bridge message shapes, label strings) **derived from the
   merged D5a code** — the interrupted meeting worker never wrote it down. Everything else in
   D5 is done.
2. **D7** implementation — design recorded in `TODO-parity.md` §4D (a self-contained DER in
   `eval/`, the synthetic espeak fixture as a regression smoke gate, and AMI SDM recorded as
   the future *real* number).
3. **X1–X4** (the cross-cutting gates) plus the adversarial red-team pass (S5).
4. Cleanup: the merged `work/*` branches, the obsolete `feat/structured-formatting` /
   `refactor/model-registry` branches and their worktrees, the stray 3 GB `models/vt-model-dl-*`,
   the E3 scratch tarball (delete the 1.58 GiB `.tar.bz2`, keep the extracted tree), and a
   re-push to gitea once `git.jdm.cx` resolves again.

---

## 1. Read these first, in this order

| Doc | What it is |
| --- | --- |
| [`TODO-parity.md`](TODO-parity.md) | **The authoritative task list.** §0 state, §1 decisions not to re-litigate, §2 what is left grouped by what it blocks, §4 per-workstream checklists, §6 expensive findings, §7 open human decisions |
| [`plan-parity-roadmap.md`](plan-parity-roadmap.md) | Entry point: workstreams A–E, ordering, non-negotiables |
| [`plan-on-device-formatter.md`](plan-on-device-formatter.md) | The formatter: model, runtime, exact prompt contract, guardrails |
| [`plan-diarization.md`](plan-diarization.md) | Meeting mode, sherpa-onnx, the "diarize first" design and why |
| [`formatter-benchmark.md`](formatter-benchmark.md) | Measured: latency, RSS, determinism, parity deviations |
| [`diarization-benchmark.md`](diarization-benchmark.md) | Measured: RTF, turns, speaker counts, memory |
| [`asr-bakeoff.md`](asr-bakeoff.md) | Cohere vs Parakeet, three axes, verdict |
| [`meeting_mode.md`](meeting_mode.md) | Capture lifecycle, spill behaviour, settings |

**If `TODO-parity.md` §2 ever contradicts §4, §4 wins.** That drift happened once.

### The evidence base is in a *different repo* — `/home/jamesm/gitprojects/pi-browser-benchmark`

Nothing above explains **why** any of this is being built. That is here, and a fresh session
will not find it by looking in `voice-transcriber`:

| Path | What it holds |
| --- | --- |
| `results/WISPR-FLOW-REPORT.md` | **The reverse-engineered findings.** How Wispr Flow's demo was probed, the WebSocket protocol, and that its post-processing is server-side and content-driven rather than tab-driven |
| `results/wispr-flow-dataset.json` | The recorded responses |
| `bench/wispr/*.js` | The probe harness (WAV synthesis, mic injection via a `getUserMedia` override, the WS client, WER scoring) |
| `plans/VOICE-TRANSCRIBER-FEATURE-PARITY.md` | The original parity plan, before it was split into the workstreams |

**The four reference fixtures** — the list, retraction, email and long utterances — are the
acceptance criterion for formatter parity. They are recorded in
`docs/plan-on-device-formatter.md` §4 with Wispr's exact outputs, and asserted live by
`tests/e2e/test_formatter_e2e.py`. Regenerate the audio with the TTS recipe in §5.

---

## 2. Where things actually stand

**Git**

- `main` is at `cd8e9ca`, working tree clean.
- **`main` is far ahead of `home/main` (gitea) and nothing has been pushed this session** —
  51 commits at the time of writing, 38 of them this session's.
  **Re-check rather than trust that number:** `git rev-list --count home/main..main`.
- Remotes: `home`/`gitea` → `gitea@git.jdm.cx:jamesm/voice-transcriber.git`,
  `github` → `git@github.com:jjamesmartiin/voice-transcriber.git`.

> ⚠️ **Do not push without asking.** The user has deliberately deferred it ("don't push yet
> since it's not ready"), so this is a *known, chosen* state rather than an oversight. Flag it
> as the biggest risk — everything else on this list is recoverable and an unpushed history
> on one disk is not — but the decision is theirs. `main` tracks `home`/`gitea`; `github` is
> push-only, so a push needs to name the remote.

**Health at the time of writing**

```
1549 passed, 3 skipped   # tests/{shared,linux,wsl,windows}
ruff check src/ tests/   clean
cargo test  (tui-rs)     54 passed
cargo clippy -D warnings clean
e2e (real GGUF)          8 passed
meeting mode             verified end to end on real two-voice audio
```

These are a snapshot too — re-run §5's invocations rather than trusting them, because a
test count that only ever goes up is how a regression gets missed.

**Branch / worktree inventory**

| Branch | State | Action |
| --- | --- | --- |
| `work/d4-pipeline`, `work/e-parakeet` | merged (D4, E) — worktrees removed | delete branches when convenient |
| `work/c2-e2e`, `work/d2-meeting`, `work/d3-sherpa` | merged, worktrees already removed | delete branch when convenient |
| `refactor/model-registry` | merged (B) — worktree `voice-transcriber-models` still present | remove worktree, then delete branch |
| `feat/structured-formatting` | **obsolete**, not merged and does not need to be (A1) | keep until pushed somewhere, then delete |
| the other ~14 branches | pre-existing, not this session's | leave alone |

**Model artifacts** — all under `~/.local/share/vt/models/`

| Dir | Size | Verified |
| --- | --- | --- |
| `cohere/` | 3.9 G | the shipped ASR |
| `formatter/` | 462 M | `s1-mini-q4_k_m.gguf`, sha256 `3b41ebe2…`, GGUF header v3, + `LICENSE`/`NOTICE` |
| `diarization/` | 52 M | pyannote segmentation + 3D-Speaker embedding, `SHA256SUMS` written |
| `parakeet/` | 1.1 G | encoder is **652,184,281 B / 622 MiB** (see §6) |
| `vt-model-dl-w4qagpdo/` | 3.0 G | **stray leftover from the X5 hermeticity leak** — a test really did download it. Safe to delete once you're satisfied nothing references it |

---

## 3. Just landed: `work/d4-pipeline` (merged)

**Meeting mode is now a working feature**, verified end to end rather than only against
stubs: a synthesised 18.7 s two-voice clip produced 4 turns with correct A-B-A-B speaker
assignment and all four turns transcribed verbatim, labelled, and written to a file.

Pipeline: `src/voice_transcriber/meeting_pipeline.py` — resample → diarize the whole
recording → slice at turn boundaries → ASR **once per turn** → post-process each turn with
its own text → render atomically.

**The requirement it was built to**, in the user's words: *stop a meeting, let it process,
and immediately go back to dictating notes*. The design:

- `transcribe2.inference_lock` (module-level, **re-entrant**) guards inference. Re-entrant
  because the pipeline holds it *and* calls `transcribe_audio`, which acquires it again —
  a plain `Lock` deadlocks. Dictation acquires it automatically and needed no change.
- The pipeline waits while a dictation is live **and re-checks inside the lock**.
- The lock is released **between turns**, so a meeting never owns the model for its duration.
- One shared model instance, so **no extra memory**.
- **Honest bound:** a dictation starting in the window after the lock is taken waits the
  remainder of one in-flight turn's ASR (~0.6 s for realistic turns, 35 s in theory).
  Inherent to sharing one model; the fixes are outside D4.

**Diarization is an enhancement, not a prerequisite** — `diarize()` returning `[]` means one
speaker, and the meeting is transcribed anyway. Tested.

**Left over from D5:** the speakers map (renamable `Speaker 2` → `Priya`), optional
JSON/Markdown forms, and a *setting* for the output directory — currently
`VT_MEETING_OUTPUT_DIR` or `<per-user data dir>/meetings/`. The setting is deferred to D6
because a toggle must land in both TUIs including `tui-rs`, which D4 could not touch.

---


## 4. Open decisions waiting on the human

1. **Push.** See §2. Not really a decision, more an omission.
2. **Regenerate `eval/results.json`.** It no longer reproduces — see §6. Cheap to do,
   but it rewrites a recorded baseline, which is why it was asked rather than done.
3. **Where transcripts are written by default.** D4 was told to make it
   configurable rather than guess. Worth choosing before D5 finishes the artifact.
4. **E3** — the sherpa-onnx int8 ONNX path for Cohere (~2.9 GB). Now *applicable*
   (Cohere is kept) and newly motivated, because the existing `--int8` option peaks
   at 22 GiB.
5. **A6** — `reset_to_defaults()` does not push the punctuation preset it changes,
   so it only takes effect next launch. Deliberately deferred; see A6.

---

## 5. Environment, commands, and the traps

### The working invocations

```bash
# Python, tests, ruff — cargo is only available inside nix develop too
nix develop --command python -m pytest tests/shared -q
nix develop --command ruff check src/ tests/
nix develop --command cargo test --manifest-path tui-rs/Cargo.toml

# Offline/network-isolated test runs — NOTE the `ip link set lo up`
timeout 1500 unshare -rn -- sh -c 'ip link set lo up 2>/dev/null; cd <repo> && \
  nix develop --command python -m pytest tests/shared tests/linux tests/wsl tests/windows -q'
```

### Running the app, and driving features without the TUI

There is a **control API** — the engine listens on a Unix socket and `main.py` is itself the
client. This is how features are scripted and tested, and it needs no display:

```bash
nix develop --command python src/main.py toggle     # start recording, or stop if recording
nix develop --command python src/main.py status     # state, device, model, and every setting
nix develop --command python src/main.py help --json  # THE authoritative verb catalogue

# meeting mode (D2/D4) — off by default, so enable it first:
nix develop --command python src/main.py meeting on
nix develop --command python src/main.py meeting-start
nix develop --command python src/main.py meeting-stop    # processing continues in the background
nix develop --command python src/main.py status          # meeting_stage / % / partial transcript

# the formatter (C5)
nix develop --command python src/main.py formatter on
nix develop --command python src/main.py formatter-model s1-mini
```

**`help --json` is the catalogue — never trust a hardcoded verb list in a doc**, including
this one.

### Generating test audio (and the voice-pair trap)

```bash
# The repo's documented TTS recipe. -w writes a WAV.
nix run nixpkgs#espeak-ng -- -v en-us -s 155 -w out.wav "<text>"

# For DIARIZATION you need two voices that actually separate. This pair works
# (≈85 Hz male vs ≈250 Hz female); a first attempt with en-us vs en-gb+f3
# collapsed to one speaker even at num_speakers=2.
nix run nixpkgs#espeak-ng -- -v en+m3 -p 20 -s 145 -w a.wav "<speaker A>"
nix run nixpkgs#espeak-ng -- -v en+f4 -p 85 -s 190 -w b.wav "<speaker B>"
```
Concatentate turns with ~0.7 s of silence between them, resample to **16 kHz mono float32**,
and you have a labelled fixture with known ground-truth boundaries. That is exactly what the
diarization benchmark and the D4 verification used.

### Reproducing the meeting-mode proof

The full recipe is in `docs/TODO-parity.md` §4D (D4). In short: build an A-B-A-B clip with the
voice pair above, then call `MeetingPipeline().process(audio, 16000)` directly with
`VT_MEETING_OUTPUT_DIR` set, and read the file it writes. Expected: 4 turns, correct speaker
ordering, all turns transcribed verbatim, no `*.tmp` left behind.

### The rules this repo enforces — a new agent will be judged on these

1. **Every feature is a toggle, and `off` is byte-identical to today.** Not "equivalent" —
   byte-identical, pinned by a test.
2. **No behaviour may be reachable only by editing config.** Every key lands in
   `config/config.yaml` *and* the documented example, `t2.DEFAULT_SETTINGS`, the environment,
   the control API, **both** TUIs (`tui.py`/`tui_ratatui.py` *and* `tui-rs/`), and the tests.
   `help --json` is the catalogue.
3. **`src/` must not depend on `eval/`**, and nothing in `src/` may gain a network dependency
   at run time. The product is offline-first; the models are a separate, explicit download.
4. **Unknown values fail safe**: formatter/diarization fall back to `off`; `cleanup_mode`
   falls back to the shipped behaviour. The reasons for that asymmetry are in
   `docs/cleanup_modes.md`.
5. **`ruff check src/ tests/` must be clean**, and `tests/shared` must pass with no network —
   there is an autouse hermeticity guard that turns a stray connect into a hard failure.
6. Commit messages carry the *reasoning* and the verification, not just the change. Look at
   any recent commit on `main` for the house style.

### Traps, each of which cost real time

1. **`unshare -rn` creates a netns with loopback DOWN.** Every network-touching
   test needs `ip link set lo up` first. Without it the llama-server e2e suite
   **silently skips** (its skip reason says "could not be started", not "no
   network") and the failures look like a code bug. This cost ~30 minutes once.
2. **Never put backticks in `git commit -m "…"`** — the shell substitutes them and
   silently mangles the message. Write the message to a file and use
   `git commit -F /tmp/msg.txt` with a *quoted* heredoc (`<<'MSG'`).
3. **An `edit` whose `oldText` ends in a newline can eat the next line's newline**,
   producing `return x        elif …` syntax errors. It happened twice. Include the
   following line in the edit, or check with `py_compile` after a batch.
4. **`git commit --amend` amends HEAD.** With a docs commit on top of a code commit,
   `--amend` silently rewrites the *wrong* one. Check `git log --oneline -1` first.
5. **A wall of unrelated test failures usually means one exception aborting a shared
   load path**, not dozens of bugs. The concrete instance: a `NameError` inside
   `load_audio_config` made every later setting keep its old value, which surfaced as
   failures in `test_audit_regressions`, `test_config_sync`, `test_dictionary`, …
6. **Settings live in two places** — `t2`'s globals *and* the post-processor's copies
   (punctuation/structure/cleanup/number modes). A fixture that restores only one
   leaks into later test files. `reset_to_defaults()` rewrites both.
7. **`cargo fmt` is NOT enforced** here — it is only a dev tool in `flake.nix`.
   Running it reformats unrelated files; a subagent did exactly that and 357 lines of
   churn had to be reverted. Tell subagents not to run it.
8. **Subagents were told not to commit**, so their branches show "Already up to date"
   on merge. Commit inside the worktree first (§3).
9. **`model_download`'s accessors raise `KeyError`** for an unregistered name, not
   `None`. Wrapping two lookups in one `try` lets the missing spec abort the path that
   *would* have worked — which is exactly how the `formatter` model became unfindable.

### Process that worked well

- **One git worktree per parallel workstream**, with disjoint file ownership stated in
  the brief. Three ran concurrently without a single conflict; the only file overlaps
  were the ones I predicted and avoided.
- **Brief subagents with verified ground truth** (exact paths, hashes, API symbols
  confirmed from the installed package) and an explicit prohibition list, plus
  "stop and report rather than edit a file you don't own". Every subagent that hit a
  boundary reported instead of crossing it, and two of those reports found real bugs.
- **Require "only what you actually observed"** in the reporting section. That is what
  produced the honest caveats (the Parakeet comparison being unfair; the size
  correction) rather than smoothed-over success.
- **Write the failing test as `xfail` with an exact reason.** The three Wispr parity
  gaps stayed visible for several commits instead of being quietly relaxed, and flipped
  to passing assertions when the fixes landed.

---

## 6. Findings this session that were expensive to establish

Summarised here so they are not re-derived; full detail in `TODO-parity.md` §6 and the
benchmark docs. Also worth knowing: **five confident plan claims turned out to be wrong**, so
when a plan asserts a fact, check it before building on it.

### The working agreement with the user

Worth knowing up front, because it shapes how this went:

- **Autonomy is wanted, with decisions surfaced.** The user asked for work to proceed
  autonomously — including delegating parallel workstreams to subagents — and to be brought in
  only for genuine questions or design forks. Every time that was honoured it produced a
  better outcome; twice a "safe" assumption would have been wrong (the `formatter_model`
  default, and the list-casing rule — where checking the evidence reversed the answer).
- **Push only on request.** See §2.
- **Verification is expected, not asserted.** "Tests pass" and "the feature works" are treated
  as different claims; the user asked directly whether things actually function. That is why
  the meeting-mode proof in §3 was run on real audio rather than trusted from stubs.
- **Metered connectivity matters.** Large downloads were originally avoided for that reason;
  if a task needs >100 MB, say so before starting it.

**The plans contained several confident claims that were wrong.** All were caught by
checking against reality, and all are corrected in place:

| Claim | Reality |
| --- | --- |
| `superwhisper/s1-mini`, `revision="v1"` | No `v1` tag exists (`resolve/v1/config.json` **404s**). Pin by commit sha. |
| A 462 MiB Q4 GGUF in that repo | That repo ships **no GGUF**. The quant is in `superwhisper/s1-mini-GGUF`. The 462 MiB figure was right. |
| `window_shift_ratio` on the pyannote segmentation config | Does not exist in sherpa-onnx 1.12.25; passing it raises `TypeError`. Result items also have no `.overlap`. |
| sherpa-onnx hallucinates on silence and needs a guard | **Did not reproduce** — 0/12, and 0/12 from the raw decoder too. |
| Parakeet encoder ≈ 211 MiB | **652,184,281 B / 622 MiB.** The 211 MB reading was taken while `tar` was still writing. |

**Bugs found by running things rather than reasoning about them:**

- **A guardrail rejection is indistinguishable from the formatter being switched
  off** — both return the cleaned-but-unformatted transcript. Two bugs in C1 were
  silently throwing away *correct* model answers this way. The e2e suite now asserts
  *formatted ≠ unformatted* rather than asserting strings, which is the only reliable
  shape for that test.
- `_canonical_tokens()` matched `[a-z0-9]+`, so `seven PM` → `[7, pm]` but the model's
  `7pm` → `[7pm]`, and guardrail 2 rejected the model's own number normalisation.
- `_render_items()` did not strip a marker the text already carried → `- - Milk`.
- `_model_path()` had **no fallback to the conventional install path**, so an installed
  model was unfindable and enabling the formatter did nothing without an env var.
- The Rust `App` default held a stale backend value; the C5 parity guard missed it
  because it pins **label strings, not defaults**.
- `eval/results.json` **no longer reproduces**: its exact config now gives 3.57 % WER
  rather than the recorded 5.10 %, with identical refs but **85/154** hypotheses
  differing. The app improved underneath it; the baseline needs regenerating before it
  can gate anything.

---

## 7. What is left, briefly

Full detail in `TODO-parity.md` §2 — that is the authoritative list, and this is the shape of
it as of this snapshot.

**Done:** A (all of A1–A5), B (all), C0–C5 + C10, D0–D4, E1/E2/E4, X5. **C4 was dropped by
decision** and **M3b with it** — see C4 for why.

Still open, roughly in priority order:

1. **Push** (§2). 50 commits, nothing on any remote.
2. **D5's remainder** — the speakers map (renamable `Speaker 2` → `Priya`), optional
   JSON/Markdown forms, and a *setting* for the output directory. The artifact itself landed
   with D4.
3. **D6** — `diarization`, `diarization_speakers`, `diarization_model` across every surface
   (this is also what unblocks the output-directory setting above).
4. **D7** — an `eval/` entry and a recorded **DER** next to the ASR numbers.
5. **C6, C9, C8, X3** — publishing the model bundle plus the first-use download prompt,
   licence notices into `config/licenses/` and the exact "S1-mini" by "Superwhisper" naming,
   the docs set, and the changelog.
6. **A4/C7, X1, X2, X4** — the recorded `eval/` parity gate (blocked on regenerating
   `eval/results.json`, which no longer reproduces), a CI check that `src/` imports nothing
   from `eval/`, and the Windows/macOS-unverified log.
7. **E3** — the sherpa-onnx int8 ONNX path for Cohere (~2.9 GB), now applicable and newly
   motivated by the `--int8` option peaking at 22 GiB.
8. **Cleanup** — the worktrees and branches in §2; the stray 3 GB `vt-model-dl-*` directory.

---

## 8. How to use this document

It is a snapshot, so **it will go stale** — `TODO-parity.md` §2 and §4 are the living
versions. If you learn something expensive, put it in the right place:

| Kind of thing | Where it goes |
| --- | --- |
| A task, or a decision not to re-litigate | `TODO-parity.md` §1/§4 |
| A measurement | the relevant `*-benchmark.md` |
| A correction to a plan's confident claim | the plan itself, in place, with what was actually true |
| A trap or process lesson | §5 here, or rewrite this file |

The one habit that mattered most this session: **when a plan made a confident factual claim,
check it against reality before building on it.** Five of them were wrong.
