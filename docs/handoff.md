# Handoff — feature-parity session

**Purpose:** everything a fresh session needs to continue this work without
re-deriving it. Deliberately *not* a copy of the task list — that lives in
[`TODO-parity.md`](TODO-parity.md), which is the durable resume point. Read that
first, then this.

Written 2026-10-08, at a point where context was getting long.

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

---

## 2. Where things actually stand

**Git**

- `main` is at `db0767a`, working tree clean.
- **`main` is 46 commits ahead of `home/main` (gitea) — nothing has been pushed this
  session.** 34 of those 46 are this session's work. **Pushing is the single highest-value
  action remaining**: everything else here is recoverable, an unpushed history on one disk
  is not.
- Remotes: `home`/`gitea` → `gitea@git.jdm.cx:jamesm/voice-transcriber.git`,
  `github` → `git@github.com:jjamesmartiin/voice-transcriber.git`.

**Health at the time of writing**

```
1526 passed, 3 skipped   # tests/{shared,linux,wsl,windows}
ruff check src/ tests/   clean
cargo test  (tui-rs)     54 passed
cargo clippy -D warnings clean
e2e (real GGUF)          8 passed
```

**Branch / worktree inventory**

| Branch | State | Action |
| --- | --- | --- |
| `work/d4-pipeline` | **in flight** — see §3 | integrate when it reports |
| `work/e-parakeet` | merged (E) — worktree still present | `git worktree remove` |
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

## 3. In flight: `work/d4-pipeline`

The async meeting pipeline — capture → diarize → per-turn ASR → per-turn
post-processing → transcript. It is the work that makes meeting mode a feature
rather than two unconnected halves.

**The requirement it was built to**, from the user directly: *processing happens in
the background after the meeting ends, and normal dictation keeps working while it
runs* — stop a meeting, let it process, immediately go back to recording your own
notes.

**The concurrency design it was given** (established by inspection, not invented):

- `transcribe2` had **no inference lock**; `_model_lock` in `transcribe_cohere.py`
  guards only the *load*. The dictation micro-batcher already calls
  `transcribe_audio` from its own worker thread, so D4 creates a **second
  concurrent producer on one shared model**.
- The model is a module-global singleton, so sharing costs **no extra memory**
  (~2 GB stays ~2 GB). Do **not** load a second copy.
- So: a shared inference lock added at `transcribe2` (the layer every caller
  passes through), **released between turns**, and the pipeline **waits while a
  dictation is live** before starting each turn.
- Honest bound to preserve: worst-case added dictation latency is **the remainder
  of one in-flight turn's ASR**, not the whole meeting.
- Diarization is an **enhancement, not a prerequisite**: `diarize()` returns `[]`
  when models are missing, and that must still yield a single-speaker transcript.

**To integrate** (the subagent was told not to commit, so check first):

```bash
cd ~/gitprojects/voice-transcriber-d4-pipeline
git status --short          # expect uncommitted work
git add -A && git commit -F /tmp/msg.txt     # see §5 for why -F
cd ~/gitprojects/voice-transcriber
git merge --no-ff work/d4-pipeline
```

Then re-run the health block in §2 and check `off`/no-diarizer/single-speaker paths.

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
benchmark docs.

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

Full detail in `TODO-parity.md` §2. The shape of it:

1. **Push** (§2).
2. **D4** — in flight (§3).
3. **D5–D7** — the transcript artifact and speakers map, the diarization settings, and a
   recorded DER.
4. **C6, C9, C8, X3** — publishing the model bundle, licence notices and the exact
   "S1-mini" by "Superwhisper" naming, the docs set, and the changelog.
5. **A4/C7, X1, X2, X4** — the recorded `eval/` parity gate, a CI check that `src/`
   imports nothing from `eval/`, and the Windows/macOS-unverified log.
6. **Cleanup** — the worktrees and branches in §2.
