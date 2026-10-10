# Session summary — 2026-10-09

A review-friendly account of one long session on `voice-transcriber`: what was built, what was
fixed, what was over-built, what was deliberately left, and what needs a decision before a
release. Written to be read in that order; stop wherever you've seen enough.

**The branch:** `wip/feature-parity-2026-10` — 75 commits, 15 merges, **nothing pushed**, `main`
untouched (unpushing `main` was deliberate: `release.yml` fires on `v*` tags and a release should
not be cut by accident).

**Size:** 78 files, +10,666 / −1,360 lines, on top of `cd8e9ca`.

| area | files | + | − |
| --- | ---: | ---: | ---: |
| `src/` (product) | 12 | 1,697 | 175 |
| `tests/` | 21 | 2,813 | 68 |
| `eval/` | 11 | 2,860 | 583 |
| `docs/` | 10 | 818 | 75 |
| `config/` | 6 | 634 | 2 |
| `tui-rs/` (Rust) | 11 | 1,551 | 408 |

Current verification: `tests/shared + tests/linux + tests/wsl + tests/windows` **1684 passed,
3 skipped**; `ruff` clean; `cargo test` 63 passed; `clippy -D warnings` clean; `nix build .#vt-tui`
succeeds.

---

## 1. What landed

### Meeting mode — finished end to end
Diarization plus a speaker-labelled transcript, which was the largest genuinely unfinished
feature. Turn slicing → per-turn ASR → per-turn post-processing; settings on every Python
surface; a **live speaker map edited from the meeting screen** (owner decisions S2/S3); a
repo-local gitignored output directory as a real setting (S8); JSON and Markdown forms; and the
ratatui half (settings rows, a speaker editor, the frozen bridge contract written down in
`docs/meeting_mode.md`).

Verified on real two-voice audio: 4 turns, correct A-B-A-B assignment, every turn transcribed
verbatim.

### The on-device formatter is shippable
A registered `formatter` model spec so the model resolves through the registry instead of a
hard-coded path; a first-use prompt **that states the size before anything downloads**;
publication scripts; the S1-mini licence and NOTICE committed with provenance; README,
architecture and CHANGELOG.

Not shippable yet, for an honest reason: **no model bundle has been published**, so the
"clean machine downloads it and works offline" claim is true of the code path, not of a real
artifact.

### A diarization registry entry
Closes a genuine ship blocker found mid-session: the weights had **no download path at all** —
they had been placed by hand during an early spike — so a fresh machine could not run meeting
mode. Verified by packaging a real bundle and installing it fully offline.

### The eval baseline, regenerated and made self-describing
The recorded baseline no longer reproduced (5.10 % → 3.57 % WER). It is regenerated, and
`eval/results.json` now records its own configuration — backend, int8, number digits, block size,
git revision, and a copy-pasteable `reproduce` command. Plus `eval/report.html`: per-clip audio
player, reference, hypothesis and word-level diff, so a human can check the transcripts rather
than trust a number.

### A recorded DER for diarization
`eval/score_diarization.py` plus a committed synthetic fixture and 15 model-free tests.
Measured **DER 0.0145**, 4 turns, ordering A,B,A,B. Explicitly documented as a regression smoke
gate, **not** an accuracy claim — the real number would need AMI SDM, which is recorded as a
follow-up rather than pretended.

### A narrow `sherpa-onnx` ≥ 1.13 pin
The E3 prerequisite, verified: the int8 ONNX Cohere model loads and decodes, diarization still
works on the new version, and torch is unchanged at 2.10.0. **The E3 ASR backend itself is not
implemented** — the toolchain is capable, the feature is not.

---

## 2. What was fixed

### Defects found by an adversarial audit of our own work
Thirteen, all fixed and each pinned by a test **proven to fail when the bug is reintroduced**.
The three worth knowing about:

- **The DER assignment used the wrong reduced cost**, returning a suboptimal speaker mapping on
  431 of 3000 fuzzed matrices — which *inflates* DER. The committed fixture was optimal by luck.
- **The review report's silence-hallucination rate was inverted**, saying 1.0 where `score.py`
  said 0.0. The report had no tests at all before this.
- **The first-use download prompt called `input()` on a daemon thread**, guarded only by
  `isatty()` — and under ratatui stdin *is* a tty. The prompt drew under the TUI and the download
  blocked on keystrokes the TUI was consuming, so `formatter: on` would look inert on a fresh
  machine.

### Other real fixes
- **An installed build wrote meeting transcripts into the launch directory** (`$PWD/meetings`,
  `~/meetings`) because `find_repo_root()` returns `None` for a Nix/AppImage install.
- **The Rich speaker editor advertised `[J/K] Reorder` that could never run** — the keys were
  consumed as selection moves first. The Rust editor disagreed.
- A method was **defined twice and silently shadowed**, so editing it did nothing.
- **A render failure discarded the whole transcript** with no artifact and no error.
- Several guards **implied protections they did not provide**: a spawn tally that was never
  asserted, a recorded ordering field never enforced, three backend registries duplicated ~80
  lines, six copies of a source split that would pass for the wrong reason if a marker moved,
  and four byte-identical `fuzzy_subsequence` copies.

### Wrong things we believed and corrected
Five confident claims in the original plans were false, and several of *ours* were too:
- `sherpa-onnx` 1.13.8 **cannot load** under the locked nixpkgs (glibc 2.44 vs 2.42) — 1.13.3 is
  the working pin. Also, `python.withPackages` silently drops a package whose `pythonModule`
  isn't the environment's python.
- `test_wavs/ja.wav` in the ONNX bundle is a **corrupt 145-byte JSON error body**.
- The onnxruntime claim: I said it didn't move; it did (1.24.4 → 1.26.0).
- The digit anomaly I first attributed to ASR drift was a **state leak in our own eval harness**.

---

## 3. What was over-built — the honest section

This is the part worth your judgement, and the answer is: **partly, yes.**

- **`eval/` grew the most of any area (2,860 lines) and it is verification code.** The clearest
  case is the DER scorer: **616 lines** of precision instrumentation (breakpoint timelines, a
  collar, Hungarian matching, RTTM parsing) plus ~180 lines of tests, built to measure a
  synthetic four-turn clip that the design itself documents as a smoke gate. An auditor's
  verdict: a **~20-line ordering-and-threshold check** gives the same regression protection
  today, and the only thing justifying the full metric is that AMI SDM is the documented next
  step. The expensive half had never been validated — and was wrong.
- **Tests outweigh product code** (2,813 vs 1,697 lines). Some of that is earned: the hermeticity
  guards exist because a test really did attempt a ~3 GB download, and another really did load a
  462 MiB model and leak 42 `llama-server` processes. But a good deal is meta-testing that mainly
  protects future agents from each other — source-text parity guards, devshell version pins, an
  AST guard that no violation has ever triggered.
- **The flake work is the least justified by outcome.** An intricate toolchain change (a second
  nixpkgs input, the python wrapper rebuilt against cpython-313 bindings) for an E3 backend that
  was never written. Capability without the feature.
- **Process cost was high**: 15 merges, three worktrees of work lost to network drops, and three
  separate attempts at recording one baseline's provenance because I did not follow the one-line
  rule (commit everything else first, *then* run it).

What *was* earned, without hedging: catching the DER sign error, the inverted metric, the
install-path bug, the unreachable editor feature, the shadowed method, the TUI-blocking prompt,
and the hermeticity leaks — plus unparking 52 commits that existed on one disk and nowhere else.

---

## 4. Deliberately NOT done

- **`main` was never touched and nothing was pushed.**
- **No deletions.** The audit's "probably dead" list is untouched, by your instruction. It
  includes one item that is a genuine trade-off rather than free: 14 unpinned `_dispatch` aliases
  buy tolerance for an older `vt-tui` binary.
- **The modularity splits** (see below) were not attempted.
- **No model bundle was published.**
- **The S1-mini attribution strategy was deferred** (S11) — the licence files ship, the
  strategic "do we ship a competitor's name" question waits.

---

## 5. Decisions needed before a release

1. **The `t2.py` / `post_processor.py` splits.** `t2.py` is 4,247 lines doing config I/O,
   settings state, settings UI and two full-screen editors; `post_processor.py` is 3,433 lines
   combining six unrelated jobs **including a network SLM client**, which sits awkwardly against
   the offline-first rule. Real payoff, real risk, and the branch is already deep.
2. **The seven settings hand-mirrored** from `t2` into `post_processor`. This is the divergence
   class that already produced one bug (A6).
3. **Whether to publish a model bundle**, without which the formatter's first-use path is
   unverifiable end to end.
4. **The `long` slice regression** (1.78 % → 2.19 % WER) is attributed to chunk-boundary drift
   but **never bisected**. Everything else improved, so it deserves a look before dismissal.
5. **The deletions list**, per item.

---

## 6. How to review this

Fastest meaningful checks, in order:

```bash
# 1. The whole verification story, self-describing:
nix develop --command python -m pytest tests/shared tests/linux -q
cat eval/README.md                      # what the numbers mean and how to reproduce them
nix develop --command python eval/review_report.py   # then open eval/report.html and LISTEN

# 2. The Rust half:
nix develop --command cargo test --manifest-path tui-rs/Cargo.toml
nix build .#vt-tui --no-link --print-out-paths

# 3. The audits themselves, with per-item status:
docs/audit-ponytail.md                  # three adversarial audits + what is fixed and what is not
docs/handoff.md §0                      # the resume state
```

**Read `eval/report.html` with the audio.** It exists precisely so the transcript numbers can be
checked by ear rather than believed.

---

## 7. Known gaps — stated plainly

- **Never verified on Windows, macOS or a real WSL host.** Those gaps are logged in `docs/TODO.md`.
- **The formatter's first-use download has never run on a clean machine** (no bundle published),
  though the size prompt and the non-blocking path are tested.
- **F7 from the audit is unconfirmed**: the guardrails understand Markdown list markup, while the
  reference implementation emits HTML. If S1-mini ever emits HTML lists, correct output would be
  silently rejected. It needs a weights run to settle.
- **AMI SDM was not measured**, so the DER is a smoke gate and nothing more.
- **The E3 ONNX backend does not exist**; only its prerequisite does.
