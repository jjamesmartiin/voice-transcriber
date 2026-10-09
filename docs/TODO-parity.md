# TODO — feature parity with cloud dictation

**Purpose of this file: durability.** It is the resume point. If the laptop reboots, the
network dies, or this work is shelved for a month while something else happens, everything
needed to pick it back up is here or one link away.

- **Where to start:** §0 (state), then §2/§3 depending on whether you have network.
- **Why each thing is the way it is:** the four `docs/plan-*.md` files. This file is the
  *actions and state*; the plans are the *reasoning*. Do not duplicate reasoning here — it
  belongs in one place and rots when copied.
- `[ ]` = open · `[x]` = done · `[~]` = in progress · `[!]` = blocked
- Every task names what "done" means. A task without an acceptance criterion is a wish.

---

## 0. Where things stand

_Snapshot: 2026-10-08._

| Tree / branch | State |
| --- | --- |
| `~/gitprojects/voice-transcriber` (main) | `beeca42` · **uncommitted**: the three parity docs (§ below) |
| `~/gitprojects/voice-transcriber-fmt` (`feat/structured-formatting`) | `c359da3`, **9 commits ahead of main, NOT merged** |
| `~/gitprojects/voice-transcriber-models` (`refactor/model-registry`) | **`1fec412` — workstream B DONE, unpushed.** Independently verified: 1163 tests pass with no network. Four cosmetic output deviations + one release-artifact regression logged in §4B. |

### ⚠️ Two things that would have been lost on a reboot

1. **`feat/structured-formatting` has diverged from `main`, and both edited
   `post_processor.py`.** The branch carries the structure stage, pause boundaries, spoken
   lists and the retraction fixes (`bd30610`, `00747f3`, `92cd5d6`, `eb05c64`, `f79c3db`).
   Meanwhile main gained *related* post-processor work (`424a1d8` three cleanup modes,
   `ec12b1c` apology-in-a-correction, `beeca42` clipboard). **The merge is not trivial and
   must not be done blind** — see §4A.
2. **The `model-registry` worktree was created but contains no work.** If workstream B is
   reported as "in progress" anywhere, that is wrong: the branch is at main's HEAD. The
   acceptance criteria are preserved verbatim in §4B so the task can be re-run from scratch.

### Docs added in this session (uncommitted on main)

| File | What |
| --- | --- |
| `docs/plan-parity-roadmap.md` | entry point: workstreams, order, non-negotiables |
| `docs/plan-diarization.md` | meeting-mode diarization, sherpa-onnx, alternatives assessed |
| `docs/plan-on-device-formatter.md` | S1-mini formatter, swappable backend, exact contract |
| `docs/plan-structured-formatting.md` | pre-existing; the structure stage (implemented on the `-fmt` branch) |

**First commit action:** commit these four documents.

---

## 1. Decisions already made — do not re-litigate

Reconsidering any of these is allowed, but it needs new evidence, not a fresh opinion.

| # | Decision | Detail |
| --- | --- | --- |
| D1 | Diarization ships as a **Meeting Mode** (press record, minutes-to-hours, get a labelled transcript), **not** an inline dictation toggle | `plan-diarization.md` §1 |
| D2 | **sherpa-onnx** is the diarization default (pyannote is HF-gated; DiariZen is CC-BY-NC) | `plan-diarization.md` §4.1–4.3 |
| D3 | Cohere-with-diarization projects are **prototypes, not the default** — adopting one replaces the ASR itself | `plan-diarization.md` §4.4 |
| D4 | The formatter ships **`superwhisper/s1-mini`** (462 MiB Q4 GGUF), attribution debt accepted temporarily | `plan-on-device-formatter.md` §5.1 |
| D5 | Everything model-backed is **swappable via a backend registry**, mirroring `transcribe2.BACKENDS` | formatter §5.2, diarization §5.1 |
| D6 | Formatter runtime is the **`llama-server` subprocess**, not in-process — because the bindings cannot do runtime CPU dispatch | formatter §6.1 |
| D7 | Typed output **never** injects `Enter`; `blocks` downgrades to `inline` when typing | `plan-structured-formatting.md` §5 |
| D8 | Every feature is a toggle, `off` is byte-identical to today, and `help --json` is the verb catalogue | `plan-structured-formatting.md` §11 |
| D9 | The ≤1.5 s release-to-clipboard SLA applies to the **default** configuration; the formatter is the one documented opt-in exemption | formatter §9 |

---

## 2. Ready to start now — no network required

Everything here is code, tests, docs and refactoring. None of it downloads a model.

- [ ] **A1** — Reconcile `feat/structured-formatting` with `main` (§4A)
- [ ] **B1–B6** — Workstream B, the `ModelSpec` registry (§4B) — **the shared prerequisite**
- [ ] **C1** — `formatter.py` skeleton + `fake` backend + the seven guardrails as tests
- [ ] **D1** — Define the `diarize.py` backend contract + a `fake` diarizer, no models
- [ ] **E1** — Write the Parakeet backend module (it can be written and unit-tested before any
      weights exist; only the *run* needs the download)
- [ ] **X1** — CI: assert `src/` imports nothing from `eval/` (§5)

## 3. Blocked on network — do not start on a hotspot

Each of these pulls weights. Sizes are known, so they are cheap to schedule later.

- [!] **C2** — `llama-server` + the 462 MiB S1-mini GGUF → the M2 end-to-end proof
- [!] **D3** — sherpa-onnx diarization models (**~40 MB total**) → the D3 spike
- [!] **E2** — Parakeet TDT 0.6B v3 (**~490 MB**) → the ASR bake-off
- [!] **E3** — Cohere int8 ONNX (**~2.9 GB**) → only if E2 says keep Cohere

---

## 4. Workstream checklists

### 4A. Structured formatting & error correction

Detail: [`plan-structured-formatting.md`](plan-structured-formatting.md), specs in
[`formatting.md`](formatting.md) and [`cleanup_modes.md`](cleanup_modes.md).
Mostly implemented; **the open work is integration, not features.**

- [x] **A1** ~~Merge `feat/structured-formatting` into `main`.~~ **RESOLVED 2026-10-08: no merge is
      needed — the branch is obsolete. Retire it; do not merge it.**

      The plan assumed "both trees changed `post_processor.py`, so read both diffs". They did,
      but they are not two halves of one feature — they are **two independent implementations
      of the same features**, and main's is a strict superset. Evidence:

      | Check | Result |
      | --- | --- |
      | `micro_batcher.py`, `tests/shared/test_structure_blocks.py`, `tests/shared/test_micro_batcher_fast.py` | **byte-identical** across the two branches — fmt's mic-pause work and its structure tests are already on main |
      | Feature markers on main | `process_verbal_retractions`, `process_structure_blocks`, `STRUCTURE_MODE`, `process_spoken_quotes`, `normalize_cleanup_mode`, `find_repo_root` — **all present** |
      | Functions present in fmt but not main | **none** |
      | Functions present in main but not fmt | 5 — the whole cleanup-mode system (`normalize/set/get_cleanup_mode`, `_removes_noise`, `_resolves_corrections`) |
      | `docs/plan-structured-formatting.md`, `docs/plan-diarization.md`, `tests/e2e/test_power_cpu.py` | all **tracked on main** |
      | Full-tree diff `main..fmt` | **70 insertions, 949 deletions** — every substantive line is main being *ahead* |

      Behaviourally confirmed by running both `post_processor.py` revisions side by side over 15
      inputs x 3 cleanup modes (45 comparisons, 32 identical):
      * main resolves every correction fmt does, at `full`;
      * main **fixes** a bug fmt still has: fmt turns `"Let's meet Tuesday, no sorry Wednesday."`
        into `"Let's sorry Wednesday."`, which is exactly what main's `ec12b1c` fixed;
      * the only fmt-only behaviour is that it resolves corrections *unconditionally*, with no
        cleanup modes — strictly worse than main's three-mode design.

      `git cherry` is useless here and worth knowing: it reports all 9 commits as "+" because
      main reimplemented the features with different patches, so identical *patches* were never
      applied. File-level and behaviour-level comparison is what settles it, not patch-ids.

      **Not done by this task:** deleting the branch. It is unpushed, and "obsolete" is a strong
      enough claim that it should be re-verified and pushed somewhere first, not acted on
      locally. Nothing is lost by leaving it.
- [ ] **A5** *(new, found while investigating A1)* **`process_serial_numbers()` ignores
      `cleanup_mode` and merges words.** `cleanup_mode: off` is documented as "every word that
      was said, verbatim", but the serial/Roman-numeral collapse is not gated by it, so it
      rewrites text *by joining tokens*:

      | input | `off` output | correct |
      | --- | --- | --- |
      | `I I I think it works.` | `III think it works.` | unchanged |
      | `a a a think` | `AAA think` | unchanged |
      | `1 1 1 works` | `111 works` | unchanged (numbers stay in `digits` mode) |

      Requires 3+ consecutive identical single letters, so it mostly hits disfluent speech —
      which is precisely this app's input. **Pre-existing, not a regression:** reproducible on
      the merge-base `0656d57`, on `main` and on `feat/structured-formatting`, so neither branch
      caused it. It contradicts `cleanup_modes.md` §4 invariant 2, and `a a a` -> `AAA` also
      changes case. Needs a decision: gate serial collapse behind `cleanup_mode`, or require a
      cue before collapsing. *Done = `off` provably deletes nothing for repeated single letters.*
- [ ] **A2** Resolve the branch's open question: setting value naming —
      `off | inline | blocks` (current) vs `off | bullets | full`. *Done = one name chosen,
      aliases documented, both TUIs agree.*
- [ ] **A3** Resolve the paragraph-break threshold: is a ≥700 ms pause a paragraph on its
      own, or only after a structure cue? *Done = a written decision + a pinned test.*
- [ ] **A4** Extend `eval/` with a `formatting` slice: input transcript → expected output,
      **including the four Wispr reference fixtures** from formatter §4.
      *Done = the parity gate is a regression test, not a one-off comparison.*

### 4B. Model registry — `refactor/model-registry`

Detail: [`plan-diarization.md`](plan-diarization.md) §6.1.
**Shared prerequisite for C and D. Pure refactor; Cohere behaviour must be byte-identical.**

**DONE in `1fec412`** on `~/gitprojects/voice-transcriber-models` (unpushed).
Verified independently: `1163 passed, 2 skipped` in 36 s inside `unshare -rn` (no network),
and the legacy constant/API surface diffed **byte-identical** against `main`.

**Deviations from "byte-identical" — the subagent claimed none, but there are four.** All are
cosmetic and none is parsed by code or pinned by a test, but the claim was wrong:

| Where | Before | After |
| --- | --- | --- |
| local-bundle install | `Installing Cohere model from local bundle` | `Installing Cohere Transcribe model from…` |
| download start | `Downloading Cohere model parts from…` | `Downloading Cohere Transcribe model parts from…` |
| manual-install hint | `(Required: model.safetensors, config.json, tokenizer.json, etc.)` | `(Required: config.json, model.safetensors, tokenizer.json, modeling_cohere_asr.py, processor_config.json)` |
| release `NOTICE` | `Cohere Transcribe 2B` / `the Apache License, Version 2.0` | **FIXED** in `b16d600` (was `Cohere Transcribe` / `Apache-2.0`) |

The `✓`/`✕`/`ready` lines are correctly preserved via `spec.name.capitalize()` → `"Cohere"`.
Accept the three console strings as an intentional normalisation. The `NOTICE` regression is
**FIXED in `b16d600`** — and review found a **second** deviation the subagent's report missed:
the license line dropped from `"the Apache License, Version 2.0"` to the SPDX id `"Apache-2.0"`,
which is worse than the missing "2B". Both are now pinned by
`test_cohere_notice_is_pinned_to_the_published_wording`, using new `notice_title`/`license_title`
fields that carry the formal wording while `display_name`/`license_name` stay short.

- [x] **B1** Add a frozen `ModelSpec` dataclass + `MODELS: dict[str, ModelSpec]` in
      `model_download.py`, Cohere as entry one.
- [x] **B2** Parameterise the API: `get_spec(name)`, `models_dir(name)`,
      `is_model_complete(name)`, `ensure_model(name, ...)`,
      `install_from_local_bundle(name, ...)`. Keep `cohere_models_dir`,
      `ensure_local_cohere`, `is_local_model_complete` as **thin deprecated aliases** so no
      other module changes in this commit.
- [x] **B3** Make verification **format-agnostic** — `_assemble_parts` verifies the spec's
      `digests` map and must not assume `model.safetensors` exists (a GGUF and an ONNX spec
      must both work).
- [x] **B4** `__main__` gains `--model NAME`; `--dest`/`--verify-only`/`--from` respect it.
- [x] **B5** Generalise `scripts/prepare_model_release.py` and
      `scripts/publish_model_bundle.sh` to a model name **keeping the parts-first,
      `SHA256SUMS`-last ordering identical** (that ordering is load-bearing).
- [x] **B6** Tests: keep all 21 existing tests meaningful (relax only assertions that encode
      "there is exactly one model"); add a second spec resolving from a **fake local bundle**
      in a tmp dir with **no network**.

*Done = `nix develop --command python -m pytest tests/shared -q` green; the Cohere resolution
order, env-var semantics, provenance file and user-facing errors unchanged; every relaxed pin
justified in the commit message.*

### 4C. On-device formatter

Detail: [`plan-on-device-formatter.md`](plan-on-device-formatter.md).

- [x] **C0** Model chosen (S1-mini) + runtime chosen (`llama-server`) — D4, D6
- [x] **C1** `formatter.py` + `BACKENDS` registry + `noop`/`fake` backends, and the guardrails
      as tests. **DONE in `29ebea3`** — 48 model-free tests; the two Wispr parity fixtures are
      asserted to **pass** every guardrail, so an over-strict validator is caught too.
      Two plan gaps resolved while implementing:
      * guardrail 1 is called out as catching *summarisation*, but a length **ceiling** cannot —
        a summary is shorter than its input. Added a loose retention floor (**guardrail 1b**,
        `_MIN_RETENTION = 0.4`) so the claim is actually true. The retraction fixture drops ~27%, so
        only gross summarisation trips it.
      * guardrail 2 treated numbered list **markers** as invented content ("First, unpack the
        boxes." -> "1. Unpack the boxes." was rejected). Markers are now stripped before the
        content comparison: the marker is structure, not a word.
      `s1-mini` / `llama-server` are registered but their modules land in C2/C3;
      `load_backend` returns `None` for them, which is the fail-safe.
- [!] **C2** **M2 — the end-to-end proof.** Run the GGUF behind `llama-server` and point the
      **existing** `VT_VLLM_URL` client at it. *Done = the four §4 fixtures produce
      non-empty, sane output.* Needs the 462 MiB download.
- [~] **C3** `formatters/llama_server.py` — the **shipped** backend: spawn/attach, process
      lifecycle, kill-on-timeout. **CODE DONE in `6299947`** — 34 tests, driven by a stub server
      that speaks just enough of the OpenAI API, so the real subprocess path and the real
      `urllib` client run offline with only the *model* faked. Three findings worth keeping:
      * **A production bug caught before shipping:** `stderr=PIPE` with no reader deadlocks the
        child once llama-server fills the ~64 KiB pipe buffer. Symptom would have been a
        formatter that hangs after a while for no visible reason. A daemon drain thread now
        feeds a bounded deque, which doubles as the stderr tail shown when startup fails.
      * **A failed spawn arms a 60 s cooldown**, or a broken install pays for a process spawn
        on every utterance.
      * **Attach mode must never kill.** `VT_FORMATTER_SERVER_URL` means the server is not
        ours; timeout terminates only a process we spawned. Pinned by a test each.
      *The latency half of the exit criteria needs the 462 MiB GGUF and is folded into C2.*
- [ ] **C4** `formatters/s1_mini.py` — in-process fallback, baselined against C3.
      *Done = a measured throughput comparison; that measurement picks the documented default.*
- [ ] **C5** Settings end-to-end (`formatter`, `formatter_model`, `formatter_style`,
      `formatter_context`; `structure_mode` supplies the third control axis). *Done = `off`
      byte-identical, both TUIs, `help --json`, configured-vs-effective in `status`.*
- [ ] **C6** Publish the model bundle + first-use download prompt stating the size.
      *Done = a clean machine can enable the formatter and then work offline.*
- [ ] **C7** `eval/` parity gate + latency numbers per hardware tier (A4 overlaps).
- [ ] **C8** Docs: spec doc, README requirements table, `architecture.md`, `TODO.md` notes.
- [ ] **C9** Licence obligations (§5.5): commit the licence + NOTICE to `config/licenses/`,
      and use the exact name **"S1-mini" by "Superwhisper"** wherever the model is named.

### 4D. Diarization — meeting mode

Detail: [`plan-diarization.md`](plan-diarization.md).

- [x] **D0** Product decision (meeting mode) + stack decision (sherpa-onnx) — D1, D2, D3
- [x] **D1** `diarize.py` backend contract (`available`/`warm`/`diarize`) + `fake` backend.
      **DONE in `2038aec`** — 64 model-free tests; no `sherpa_onnx` import at module import
      (pinned by a subprocess test, since an in-process check can be fooled by another test).
      Three plan gaps resolved while implementing:
      * **§9 and §10 still described the pre-decision plan.** §9 listed `merge_turns()` as
        unit-tested while §5.1 forbids it; §10 made D1 the `ModelSpec` generalisation, which is
        already shipped as workstream **B**. Both corrected in `plan-diarization.md`, and §5.1's
        `cohere-ft` entry (pointing at a §4.6 that does not exist) was dropped for what was
        actually built.
      * **`diarize()` catches `Exception`, not `BaseException`.** A capture runs for twenty
        minutes; swallowing `KeyboardInterrupt` would make it unkillable. Pinned by a test —
        the opposite of the X5 `NetworkAccessBlocked` choice, and for the opposite reason.
      * **Failure returns `[]`.** "Disabled" and "failed" are deliberately indistinguishable,
        because the required behaviour is identical: one speaker, one transcript.
      The fake backend is also a fault injector (`SCRIPT`/`FAILURE`/`AVAILABLE`/`DELAY_S`), so
      the fail-safe paths are tested against behaviour, not against a mock's assumptions.
- [ ] **D2** Meeting capture lifecycle: start/stop verbs, long capture, **temp-file spill
      past a threshold**, progress state. *Done = a 10-minute recording completes without
      exhausting memory and `status` reports progress.*
- [!] **D3** Spike: real sherpa-onnx weights on a two-voice clip. *Done = a turn list plus a
      measured RTF.* (~40 MB download.) **Settle workstream E first** — if the ASR gains
      native timestamps, §3's whole design changes.
- [ ] **D4** Turn slicing → per-turn ASR → per-turn post-processing.
      *Done = a two-voice E2E asserts correct turn count and ordering, not just words.*
- [ ] **D5** Output artifact + speakers map. *Done = a real meeting produces a readable
      labelled transcript.*
- [ ] **D6** Settings (`diarization`, `diarization_speakers`, `diarization_model`).
      *Done = `off` byte-identical; single-speaker `auto` adds no label; both TUIs.*
- [ ] **D7** Eval entry + DER recorded next to the ASR numbers; `docs/TODO.md` verification
      notes for unverified platforms.

### 4E. ASR bake-off — Parakeet vs Cohere

Detail: [`plan-parity-roadmap.md`](plan-parity-roadmap.md) §3.1.
**Cheapest way to change the app's future: one backend module + one eval run.**

- [ ] **E1** Add Parakeet TDT 0.6B v3 as a third entry in `transcribe2.BACKENDS`.
      *Done = the module and its tests exist and pass with no weights present.*
- [!] **E2** Run `eval/score.py --json` and compare against `eval/results.json`.
      **Measure WER/CER *and* wall-clock RTF *and* peak RSS together** — accuracy alone
      repeats the mistake the Cohere-vs-whisper comparison deliberately avoided.
      *Done = a written verdict with all three numbers.* (~490 MB download.)
- [!] **E3** If Cohere is kept: evaluate the **sherpa-onnx int8 ONNX** path, which drops the
      `torch` dependency entirely (~0.7–1 GB installed) and unifies ASR+VAD+punctuation+
      diarization on one runtime, with no change to the transcript.
- [ ] **E4** Feed the outcome back into 4D — specifically whether §3's "diarize first, then
      transcribe each turn" survives.

---

## 5. Cross-cutting

- [ ] **X1** CI: assert `src/` imports nothing from `eval/` (keeps the product offline-first).
- [ ] **X2** Do **not** let the `torch`/`onnxruntime` decision land in two workstreams
      independently — E3 and D3 interact (sherpa-onnx could serve both).
- [ ] **X3** `CHANGELOG.md` + `README.md` per workstream, at merge time.
- [ ] **X4** Anything only verifiable on a real Windows/macOS host gets a `docs/TODO.md`
      entry naming exactly what is unverified — that is this repo's convention and it is
      what makes the log trustworthy.
- [x] **X5** ⚠️ **`tests/shared` was not hermetic — a test attempted a real ~4 GB download.**
      **FIXED in `1fec412`.** Root cause was **not** the fallback test — that was my first guess
      and it was wrong. The leak was
      `test_micro_batcher_fast.py::TestMicroBatchingEngine::test_micro_batcher_buffer_splitting`:
      it is a **`unittest.TestCase`**, so the function-scoped pytest `fake_asr` fixture never reached
      it. It ran the real backend → `micro_batcher._worker_loop` → `transcribe2.transcribe_audio`
      → `transcribe_cohere.get_model` → `ensure_local_cohere()` with no `base_url` → the real
      `DEFAULT_RELEASE_BASE` → download. The worker's `except Exception` printed a warning and
      carried on, so pytest emitted only a `PytestUnhandledThreadExceptionWarning` — **not a
      failure**. Observed live 2026-10-08: 111 MB pulled from `cdn-185-199-111-133.github.com`
      at ~600 kbps over 88 minutes before it was killed. **Not hotspot-specific** — it fires on
      any machine with internet.
      *Fix = a new `tests/shared/conftest.py` autouse fixture raising `NetworkAccessBlocked` (a
      `BaseException`, so installer `except Exception` handlers cannot swallow it) on any
      non-loopback `connect`/`connect_ex`/`create_connection`, plus a teardown check that turns a
      background-thread leak into a hard test failure. Loopback and AF_UNIX stay allowed for the
      control-API tests.*
      **Residual gaps:** `socket.getaddrinfo` is not guarded (DNS could still leak), and asyncio
      connects go through the loop's own `sock_connect`, bypassing the guard.

---

## 6. Findings that were expensive to establish — do not re-derive

Reference material, not tasks. Each took a research pass or a live experiment.

| Finding | Where |
| --- | --- |
| Wispr Flow's *measured* behaviour: retraction is model-side, lists come back as `<ol><li>` **markup**, output is deterministic, formatting is content-driven not tab-driven, no context travels mid-session, server time 0.09–0.21 s | `pi-browser-benchmark/results/WISPR-FLOW-REPORT.md` |
| **S1-mini's exact contract** — the mandatory system prompt, the `[Styling: …][Structure: …][Context: …]` control line, **`enable_thinking=False` is required or you get a blank result**, greedy only, `max_new_tokens = 1.3 × input + 32`, ≤1000 tokens, empty string is a *valid* result | formatter §5.3 |
| **`llama-cpp-python` cannot do runtime CPU dispatch** (bindings never expose `ggml_backend_load_all`, upstream #2069) → the Nix in-process artifact is **baseline SSE2**, and nixpkgs notes AVX2 can be **13× faster** | formatter §6.1 |
| PyInstaller traps: **no official hook**; the default `lib*.so` glob **misses** `lib*.so.0`/`.so.0.x.y` (bundle dies with `cannot open shared object file: libggml.so.0`); macOS must ship **per-arch, not universal** | formatter §6.3 |
| sherpa-onnx exact API + **built-in progress callback**; the embedding release tag is misspelled **`speaker-recongition-models`**; `threshold` is inverted (smaller = *more* speakers); the library owns turn merging | diarization §4.1 |
| **Cohere Transcribe has no timestamps** (its own docs *and* the sherpa implementation source), and sherpa had to add its own **silence guard** because it hallucinates on silence | diarization §3, roadmap §3.1 |
| **Parakeet TDT 0.6B v3**: 487 MB int8, CC-BY-4.0, **native word timestamps**, ~36× realtime CPU, WER 6.32 vs Cohere's 5.42 | roadmap §3.1 |
| Measured gaps vs Wispr in the current post-processor: **unknown proper nouns are lowercased** (`Michelle`→`michelle`), `six PM, actually no, seven PM` does **not** resolve, comma-separated enumerations produce **no** list | formatter §3 |

---

## 7. Open questions needing a human decision

None of these block §2. They block a *release*.

1. **S1-mini attribution** — the licence requires crediting **"S1-mini" by "Superwhisper"**, a
   competitor, in our UI. Accepted temporarily (D4). When do we revisit, and is the exit a
   self-fine-tune on Qwen3.5-2B using `eval/`?
2. **Diarization label style** — `[Speaker 1]`, `Speaker 1:`, or user-assigned names?
3. **Meeting-mode output location** — a configured directory, or alongside a chosen file?
4. **Should the formatter default on** for users with sufficient RAM? (Currently: no.)
5. **Workstream E outcome** — keep Cohere, or switch to Parakeet? This changes C and D.
