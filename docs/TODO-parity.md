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

_Snapshot: 2026-10-08 (later than the first revision of this file — see the note below)._

Everything below is committed on `main` and the working tree is clean.

| Tree / branch | State |
| --- | --- |
| `~/gitprojects/voice-transcriber` (main) | merged and clean; **nothing pushed** — main is 17 commits ahead of the remote |
| `work/c2-e2e`, `work/d3-sherpa`, `work/d2-meeting` | merged into main; worktrees removed, branches kept as a safety net |
| `~/gitprojects/voice-transcriber-fmt` (`feat/structured-formatting`) | **obsolete** — main contains a strictly larger reimplementation; see A1 |
| `~/gitprojects/voice-transcriber-models` (`refactor/model-registry`) | merged into main; see B1–B6 |
| `work/e-parakeet` (`~/gitprojects/voice-transcriber-e-parakeet`) | **in flight** — workstream E |

**The two warnings below are resolved, and kept only because they are the reason this file
exists at all.** §2 and §3 were rewritten: they used to be a "ready to start" / "blocked on
network" split written when nothing had been done, and they had drifted into contradicting
§4 — listing finished work as pending and finished downloads as blocked. If those sections
ever disagree with §4 again, **§4 wins**.

### Two things that would have been lost on a reboot (both now safe)

1. **`feat/structured-formatting` had diverged from `main`, with both editing
   `post_processor.py`.** Resolved in A1: it is not a merge at all — main carries an
   independent, strictly larger implementation, byte-identical `micro_batcher.py`, and a fix
   the branch still lacks. The branch still exists locally and is safe to delete once someone
   has re-verified that, ideally *after* pushing it somewhere.
2. **The earlier caution that the `model-registry` worktree "contains no work" is obsolete.**
   Workstream B landed, was independently verified hermetic (1163 tests, no network), and is
   merged.

### Docs committed in this session

| File | What |
| --- | --- |
| `docs/plan-parity-roadmap.md` | entry point: workstreams, order, non-negotiables |
| `docs/plan-diarization.md` | meeting-mode diarization, sherpa-onnx, alternatives assessed |
| `docs/plan-on-device-formatter.md` | S1-mini formatter, swappable backend, exact contract |
| `docs/formatter-benchmark.md` | measured: latency, RSS, determinism, parity deviations |
| `docs/diarization-benchmark.md` | measured: RTF, turns, speaker counts, memory |
| `docs/meeting_mode.md` | capture lifecycle, settings, spill behaviour |
| `docs/asr-bakeoff.md` | workstream E's verdict (in flight) |

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
| D10 | **The legacy SLM pass stays as it is — deliberately not retired.** It is the "bring your own model server" escape hatch (HTTP to `VT_VLLM_URL`, off by default, opt-in via `VT_ENABLE_SLM=1`), and it may be worth improving later. It is a *different mechanism* from the formatter, not an older version of it. | decided 2026-10-08 |

### 1a. A known interaction, not a bug

The SLM pass and the formatter can both run, and neither knows about the other. With
`VT_ENABLE_SLM=1` **and** a formatter backend enabled, one utterance goes through **two LLM
passes**: the SLM pass at step 0 (HTTP to an external server), then the deterministic cleanup,
then the formatter at step 14b (local model, its own guardrails). Each validates against the
other's output and the user pays for two models.

That is a **consequence of D10**, not an oversight, and it is left in place: the SLM pass needs
an env var *and* a self-hosted vLLM server, so nobody reaches this state by accident. If it
ever becomes confusing, the options are to make the two mutually exclusive, or to warn in
`status`. Recorded here so the next person to see two rewrites in one transcript knows it is
expected.

---

## 2. What is actually left — grouped by what it blocks

Rewritten 2026-10-08. The previous version split by "needs network", which stopped being the
useful axis the moment the downloads were made. **If this section ever disagrees with §4, §4
wins** — it drifted into contradiction once already.

### 2a. Needed before meeting mode works end to end

Capture exists (D2) and diarization exists (D3), but nothing joins them up yet.

- [ ] **D4** — turn slicing → per-turn ASR → per-turn post-processing. **The keystone of
      workstream D.** `diarize.py` returns `Turn`s and `meeting.py` has `on_audio` as the
      hand-off seam, so this is the wiring plus the per-turn ASR fan-out.
- [ ] **D5** — the output artifact + a speakers map (plain text, optional JSON/Markdown,
      renamable labels). Needs the output-location decision from §7.
- [ ] **D6** — settings (`diarization`, `diarization_speakers`, `diarization_model`) across
      every surface, with `off` byte-identical.
- [ ] **D7** — an `eval/` entry and a recorded **DER**, next to the ASR numbers.

### 2b. Needed before this can ship

- [ ] **C6** — publish the model bundle + the first-use download prompt that states the size;
      this is also where the model registry gains its `formatter` spec, which is currently the
      one thing `llama_server._model_path()` reaches past.
- [ ] **C9** — licence obligations: the licence + `NOTICE` into `config/licenses/`, and the
      exact model naming (**"S1-mini" by "Superwhisper"**) on every surface that names it.
- [ ] **C8** — the documentation set: spec doc, README requirements table, `architecture.md`,
      `TODO.md` notes.
- [ ] **X3** — `CHANGELOG.md` and `README.md` per workstream at merge time.
- [ ] **Push.** Nothing is on any remote; main is 17 commits ahead. The remotes are
      `gitea@git.jdm.cx:jamesm/voice-transcriber.git` and
      `git@github.com:jjamesmartiin/voice-transcriber.git`.

### 2c. Quality gates

- [ ] **A4** / **C7** — the `eval/` formatting slice, including the four Wispr reference
      fixtures, so parity is a regression gate rather than a one-off comparison. The e2e suite
      already asserts the fixtures against the live model; this is the recorded-manifest half.
- [ ] **X1** — CI: assert `src/` imports nothing from `eval/`, keeping the product offline-first.
- [ ] **X2** — do not let the `torch`/`onnxruntime` decision land in two workstreams at once.
- [ ] **X4** — anything only verifiable on a real Windows/macOS host gets a `docs/TODO.md` entry.
- [ ] **E4** — feed E's verdict back into D (does §3's "diarize first" design survive?).

### 2d. Known, deliberately deferred

- [ ] **A6** — `reset_to_defaults()` does not push the punctuation preset it changes, so it
      only takes effect next launch; and its target deliberately differs from the first-run
      default. Fixing it exposed order-dependent leakage in several fixtures, so it was
      reverted and recorded rather than chased. See A6 for the detail.
- [ ] **E3** — the sherpa-onnx int8 ONNX path for Cohere (~2.9 GB), **only if E2 says keep
      Cohere**. It would drop the `torch` dependency (~0.7–1.1 GB installed).

### 2e. Cleanup, whenever convenient

- Delete `feat/structured-formatting` and `refactor/model-registry` locally — both are
  obsolete or merged — **after pushing them somewhere** if you want the history.
- Remove the `voice-transcriber-fmt` and `voice-transcriber-models` worktrees, and the
  `work/*` branches once the merges have settled.

---

## 4. Workstream checklists

> §3 is deliberately retired: it used to be "blocked on network", which stopped being a
> useful split once the weights were fetched, so its contents moved into §2. The numbering is
> deliberately **not** renumbered — `§4A`, `§5`, `§6` and friends are cross-referenced from
> this file and from the plan documents.

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
- [x] **A5** **`process_serial_numbers()` merged words regardless of `cleanup_mode`.**
      **FIXED in `a2c91b8`.** `cleanup_mode: off` is documented as "every word that was said,
      verbatim", but the serial/Roman-numeral collapse is not gated by it and was *joining*
      tokens, so it rewrote text instead of only reformatting it:

      | input | before | after |
      | --- | --- | --- |
      | `I I I think it works.` | `III think it works.` | unchanged |
      | `a a a think` | `AAA think` | unchanged (`a a a think.` - the full stop is the un-gated punctuation preset) |
      | `so I I I mean` | `so III mean.` | unchanged |

      **Root cause:** `_is_valid_untriggered_serial()` Case 3 read *any* run of single letters
      as an initialism. English has single-letter *words* ("a", "I", and the vocative "O"), so
      a run made entirely of them is prose. The existing guard knew this but covered only the
      2-token case - `I I` was safe, `I I I` was not. `O O O` was safe **by accident**: "o"
      means zero in `_DIGIT_WORDS`, so it was exempted as a digit run by an earlier guard.

      **Fix:** generalise the guard - a run is prose only when *every* token is one of those
      words, so `A B I`/`I B M`/`B B B` still collapse. A trigger noun still forces a collapse
      ("serial number A A A" -> "AAA"), because the triggered path never consults this function.

      **Why not gate it behind `cleanup_mode`** (the other candidate, and the first suggestion):
      `cleanup_modes.md` §2 deliberately excludes serial/number/casing from the gate, because
      `off` promises "nothing deleted, nothing rewritten as a correction" while those settings
      change how text *looks* - `off` with `number_digits: digits` still writes `25`.
      **Be precise about what a gate would have done, though: it would have hidden the symptom.**
      The weld is observable *only* at `off` - above it the stutter pass runs first, reduces
      `I I I` to `I I`, and the old 2-token guard then catches it. Measured over a 16-sentence
      corpus in `artifacts` and `full`: **zero** cases where the weld was visible. That is the
      real argument for fixing the detector instead: the only thing hiding the bug above `off`
      is an accident of pass ordering plus that narrow guard, so a gate would fix what users see
      while leaving the class of bug in place. Reorder the passes, or hit a run the stutter pass
      does not match, and it returns.

      **Verified by differential comparison** against the previous implementation over a
      44-sentence corpus x 3 cleanup modes: **132 comparisons, 4 changed**, all at `off` and all
      of them this bug. Prose, NATO dictation, real codes and the `artifacts`/`full` outputs are
      byte-identical. Known cost, pinned in a test rather than hidden: an all-`{a,i,o}` run no
      longer collapses (`A I O` stays as-is), which is the ambiguous set by definition.
- [x] **A2** Resolve the branch's open question: setting value naming —
      `off | inline | blocks` (current) vs `off | bullets | full`.
      **DECIDED: `off | inline | blocks` stay canonical**; `bullets`/`full`/`lists` remain
      accepted *input* aliases, so nobody is locked out. Two concrete reasons:
      * **`full` is already taken** — it is the canonical *cleanup* mode and the same settings
        list renders it as a `[FULL]` badge, so reusing it would put two identical badges on
        two unrelated axes in one modal.
      * **The dangerous axis is transport, not appearance** — `inline` never emits a newline,
        `blocks` does, and a newline is an `Enter` keypress in whatever window has focus.
        "bullets" describes the look but hides the hazard.

      **Fixed a real drift while resolving it.** The two frontends disagreed on the
      `blocks`-while-typing row, and the Python one advertised a formatting that was not in
      force: it read `"Bullets on their own lines (typed text stays inline)"` while Rust
      correctly read `"Typed text stays inline"`. Root cause: the label logic was trapped as a
      closure inside the interactive `select_settings_picker()`, so it was untestable and
      nothing could catch the divergence. Extracted to `t2.structure_setting_state()` and
      pinned by both a parametrised test and a **source-level parity guard** against
      `tui-rs/src/settings_picker.rs` (mirroring the one in `test_mode_presets.py`). Rust was
      right, so Python was brought to it. `cargo test`: 51 passed.
- [x] **A3** Resolve the paragraph-break threshold — is a ≥700 ms pause a paragraph on its own,
      or only after a structure cue? **DECIDED: a pause is a paragraph on its own — no cue, and
      no larger millisecond threshold — but only at a clause boundary. Implemented.**

      The question assumed a temporal axis, and measurement says that is the wrong axis. The
      batcher cuts at `min_silence_sec = 0.25` and reports only the *kind* of cut, not a
      duration; and 700 ms is no more a paragraph than 250 ms is when the pause lands
      mid-clause. It did land mid-clause, and `blocks` mode emitted a blank line there:

      | input | before | after |
      | --- | --- | --- |
      | `I was going to the [pause] store and then home.` | `...the\n\nstore...` | `...the store...` |
      | `First we plan, [pause] then we ship.` | `plan,\n\nthen` | `plan, then` |
      | `so the plan is [pause] we ship on Friday.` | `is\n\nwe` | `is we` |

      What separates a paragraph from a breath is **linguistic**: whether the pause follows a
      finished sentence or a content word. So the kind-based rule (`hard` → `\n\n`, `soft` →
      `\n`) now applies only when `_pause_is_a_boundary()` says the text before it ends a
      clause; straight after a comma/semicolon/colon or a function word it joins with a space,
      exactly as `inline` does.

      **This was tuned against the pinned list tests rather than guessed.** A blanket
      "must end in terminal punctuation" rule looked right but would have regressed the
      realistic list case — `milk [pause] eggs [pause] bread` has no internal full stops and
      currently becomes paragraphs. Every pre-existing pause test happened to use
      sentence-final text (`"Thing one." + pause`), which is exactly why the mid-clause bug
      survived: the fixtures were unrepresentative. Both shapes are now pinned.

      `off` and `inline` are provably untouched — they flatten every pause to a space, pinned
      by a test. 1197 passed, 2 skipped; ruff clean; `cargo test` 51 passed.
- [ ] **A6** *(new, found while doing C5)* **"Reset to Defaults" does not take effect until the
      next launch, and its punctuation target differs from the first-run default.**
      `t2.reset_to_defaults()` assigns `PUNCTUATION_MODE` but never pushes it to the
      post-processor, which keeps its own copy - so the running app carries on with the old
      preset while `save_audio_config()` has already written the new one to disk. Restart and
      the preset changes, which is exactly the kind of delayed-action surprise that is hard to
      attribute.

      Compounding it: `DEFAULT_SETTINGS['PUNCTUATION_MODE']` is `"no_punctuation"` while the
      module global, `config/config.yaml` and `docs/mode_presets.md` (canonical id `full`, badge
      `[DEFAULT]`, aliases `default`/`standard`) all say the shipped default is `full`. That
      difference is **deliberate** - `test_settings_menu.py::
      test_shipped_defaults_are_the_intended_baseline` says "The reset target is a product
      decision, not an incidental value" - so the reset target and the first-run default are
      intentionally different values. Worth a second look on its own merits, but not to be
      "fixed" by matching them up.

      **Why this was not fixed here:** adding the missing push is arguably more correct, and it
      was tried - but it is a behaviour change outside the formatter workstream, and it exposed
      order-dependent leakage in several tests that mutate settings globals
      (`test_settings_menu.py`, `test_time_saved.py`) without restoring the renderer's copies.
      Chasing that destabilised the suite for no C5 benefit, so it was reverted and recorded
      instead. *Done = the reset pushes every setting it changes, one place owns the two
      defaults, and the leaking fixtures restore what they touch.*
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
- [x] **C2** **M2 — the end-to-end proof.** **DONE.** All four §4 fixtures now run through the
      real code path against the real GGUF. `tests/e2e/test_formatter_e2e.py` skips cleanly
      when the weights are absent; `docs/formatter-benchmark.md` holds the measurements.

      Measured warm (12 threads, Ryzen AI 9 HX PRO 370): pipeline wall **135.6 ms**
      (retraction), 225.6 ms (list), 235.0 ms (email), 441.7 ms (long) — **9–29 % of the
      1.5 s SLA**, well inside the §9 budget, and the 3 s internal timeout was never
      approached. Cold start spawn→healthy 0.776 s. Peak RSS 150.9 MiB. Byte-identical
      across 3 runs.

      **The run's value was what it broke.** It found two bugs that were discarding
      *correct* model answers (fixed in `ceb42d0`):
      * `_canonical_tokens()` glued letters to digits, so the input "seven PM"
        canonicalised to `[7, pm]` and the model's "7pm" to `[7pm]` — guardrail 2 called the
        model's own number normalisation invented content and the pipeline fell back.
      * `_render_items()` never stripped a marker the text already carried, so a list the
        formatter had already produced came out `- - Milk for the cake`.

      Worth remembering for future guardrail work: **a rejection is indistinguishable from
      the formatter being switched off** — both return the cleaned-but-unformatted
      transcript. That is why the bugs looked like "the model did nothing" rather than
      like failures, and why the e2e suite now asserts formatted ≠ unformatted.

      Also fixed here: `llama_server._model_path()` had **no fallback to the conventional
      install path**, so a model that *was* installed stayed unfindable and `formatter: on`
      did nothing without `VT_FORMATTER_MODEL_PATH` — the same class of bug as the backend
      default. The registry accessors raise `KeyError` for an unregistered name, so each
      lookup is isolated or the missing spec aborts the path that works.

      **The plan's model coordinates were wrong** (corrected here on 2026-10-08, verified
      against the Hub API):

      | Plan says | Reality |
      | --- | --- |
      | `superwhisper/s1-mini`, `revision="v1"` | The repo has **no `v1` tag** — refs are `main` only, and `resolve/v1/config.json` returns **404**. Pin by commit sha instead. |
      | "a 462 MiB Q4 GGUF" in that repo | That repo ships **no GGUF at all** — only `model.safetensors` (~1.4 GiB, bf16, Qwen3 0.6B). The quant lives in a **separate repo**, `superwhisper/s1-mini-GGUF`. |

      The corrected, verified coordinates — the "462 MiB" figure itself was right:

      ```
      repo    : superwhisper/s1-mini-GGUF
      revision: 34add00a48a2e5d24e5a4ee5405a99620a3a240c   (branch main; no tags exist)
      file    : s1-mini-q4_k_m.gguf
      size    : 484,219,808 bytes = 461.8 MiB
      sha256  : 3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634
      ```

      Also present: `s1-mini-f16.gguf` (1,509,347,232 bytes), plus `LICENSE`/`NOTICE`. Both
      are downloaded to `~/.local/share/vt/models/formatter/`; the quant's sha256 was verified
      independently against the Hub's LFS oid, along with the magic (`GGUF`) and header
      version (**3** — the version the plan pins as the contract).
- [x] **C10** **Parity deviations — RESOLVED.** Both were fixed with deterministic rules rather
      than imitation, and **all four §4 fixtures now match the reference output** (8/8 e2e).

      The investigation inverted the original framing, which is the part worth keeping:

      * **The app already had a convention and the model was the outlier.**
        `tests/shared/test_post_processor.py` pins `convert_number_words_to_digits("five pm") ==
        "5 PM"`, and `grep -E "[0-9](am|pm)" src/` found **nothing** - the compact `7pm` form
        existed nowhere in the codebase until the formatter produced it. So this was not a
        Wispr-matching preference: the formatter's output was **internally inconsistent with the
        deterministic path**, i.e. the same app writing the same time two ways. Fixed with
        `post_processor.normalize_clock_spacing()`, which must run **after** the formatter (the
        first attempt put it before and it never saw the model's output).
      * **The list-casing call was originally wrong, and worth being explicit about.**
        Sentence-casing bullet items is conventional for a *displayed list of independent
        sentences*; it is **not** conventional for colon-introduced fragments, which are
        grammatically part of the introducer ("I want to grab three things: milk for the cake,
        eggs for breakfast, and white bread"). Wispr's lowercase was the better form and ours
        was the outlier. Fixed with `formatter._restore_list_item_casing()`, which conforms an
        item's first letter to how the transcript had it - so proper nouns survive, because only
        words the transcript already had in lowercase are touched.

      Both rules are deterministic, live in `tests/shared` as well as the e2e suite, and are
      independent of the model.
- [x] **C3** `formatters/llama_server.py` — the **shipped** backend: spawn/attach, process
      lifecycle, kill-on-timeout. **DONE in `6299947`** — 34 tests, driven by a stub server
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
      *The latency half of the exit criteria — "a warm server answers within the §9 budget" —
      is now **measured**, by C2: 135.6 ms (retraction) to 441.7 ms (long), i.e. 9–29 % of the
      1.5 s SLA. Exit criteria met.*
- [x] **C4** `formatters/s1_mini.py` — in-process fallback, baselined against C3.
      **DROPPED (decided 2026-10-08), superseding M3b.** The in-process runtime loses on
      every axis that matters here, and the plan's own §6.1 finding is why:
      * **Portability vs speed is a forced choice, and it loses either way.** Under Nix the
        in-process build is baseline SSE2 — portable but potentially 13× slower, because the
        `llama-cpp-python` bindings cannot load llama.cpp's dynamic CPU variants (upstream
        #2069). A `pip` build enables AVX2 and **SIGILLs on pre-AVX2 hardware**. The
        subprocess gets `cpuArchDynamicDispatch` from `pkgs.llama-cpp`, so **one artifact is
        both portable and fast**.
      * **A native extension per Python version, per platform**, versus zero coupling — a
        `llama-server` binary works regardless of our Python.
      * **No pinning treadmill.** The plan wanted `llama-cpp-python==0.3.23` pinned because it
        pins a vendored llama.cpp commit; a server binary needs no such pin.
      * **Worse cancellation.** In-process has no `timeout=` kwarg, so it needs a
        `StoppingCriteria` plus worker-thread abandonment, and a stuck thread cannot be
        reclaimed. The subprocess is `terminate()` then `kill()`.
      * **A much slower devShell**, forever, for every developer — it compiles from source.

      **What is actually given up is small and is recorded rather than glossed:** the M3b
      head-to-head measurement is never made (but the subprocess measures 135–442 ms against a
      1.5 s budget, so the outcome was not in doubt), and **macOS packaging gets harder** —
      `llama-cpp-python` ships macOS wheels while a macOS `llama-server` must be found or
      built. Linux and Windows are both covered (`pkgs.llama-cpp`; llama.cpp's prebuilt
      `llama-server.exe` zips). If macOS ever becomes a release blocker, this is the one
      argument to revisit, and it is a packaging argument rather than a capability one.

      **The cleanup this forced is the part that mattered.** With C4 gone, `s1-mini` had to
      stop being a *backend*: it was a selectable runtime resolving to a module that would
      never exist, which is exactly the bug class the default itself had — you could select
      it and the formatter would silently do nothing. So the two axes were separated: the
      **backend** registry holds only runtimes that exist (`llama-server`, plus `noop` for
      tests), and `formatter_model` now names a **model**. Checking that also caught the Rust
      `App` still defaulting to the old value (`dbf71e9`), which the C5 parity guard missed
      because it pins label strings, not defaults.
- [x] **C5** Settings end-to-end (`formatter`, `formatter_model`, `formatter_style`,
      `formatter_context`; `structure_mode` supplies the third control axis). **DONE.**
      Surface count, all wired: `config/config.yaml` + the documented example, `t2.py`
      (globals, `DEFAULT_SETTINGS`, load/save, `VT_FORMATTER*` env overrides, getters/setters,
      cycles, `_push_formatter_settings()`), the integration point in
      `post_processor.clean_speech_transcription()` (after deterministic cleanup, before the
      structure stage), 4 control verbs + `status`, `main.py` (verb dispatch, cycle callbacks,
      status fields), both Python TUIs, and the ratatui modal.

      **The `off` guarantee is pinned by the test that matters:** with `formatter: off` the
      backend is never consulted, *and* the same input demonstrably changes when it is on - so
      the test cannot pass on a broken no-op. `cleanup_mode: off` is a second hard gate
      (it promises nothing is deleted; the formatter's whole job is deleting), and
      `get_effective_formatter()` reports that distinction so an inert `formatter: on` is
      visible in `status` rather than looking like a broken feature.

      Two findings along the way:
      * **Guardrail 3b added.** `plan-on-device-formatter.md` §7.3 contradicted itself -
        "list markup is stripped to prose" vs "not something to silently strip". The
        clarification wins: with `structure: prose` requested, list markup in the answer is now
        a **validation failure** (fall back to the unformatted text), not silent stripping -
        stripping would hide a prompt that no longer matches the trained format.
      * **The Rust settings box cost nothing to parallelise** because the two halves touch
        disjoint files, so a subagent took the ratatui side against a frozen label contract.
        It reformatted five unrelated files to satisfy a crate-wide `cargo fmt --check`; that
        criterion was mine and was wrong (`cargo fmt` is *not* enforced here - it is only a dev
        tool in the flake), so the churn was reverted. Rust is held to Python by a source-level
        parity guard now, like the structure row.
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
- [x] **D2** Meeting capture lifecycle: start/stop verbs, long capture, **temp-file spill
      past a threshold**, progress state. **DONE.** New `src/voice_transcriber/meeting.py`:
      a `MeetingState` machine (idle → recording → processing), an `AudioSpillBuffer` holding
      a hot in-memory tail while spilling to a temp file past the threshold, and a
      `MeetingSession` owning its own stop/capture/finalise threads — it deliberately never
      reuses `t2.stop_recording`, so a long capture cannot disturb push-to-talk. `on_audio`
      is the D4 hand-off seam and `on_change` the TUI ticker seam.

      **Spill threshold: 10 minutes**, in the platform temp dir, always deleted. Arithmetic
      is in-module: 16 000 frames/s × 4 B = 64 000 B/s, so 10 min ≈ 38.4 MB and an hour
      ≈ 230.4 MB — the whole point being to keep the resident tail bounded rather than
      accumulate an hour.

      **Decisions taken** (the plan left these open in §12): start/stop are the control-verb
      contract with a settings-modal action in the Rich TUI; `status` reports `meeting`,
      `meeting_setting`, `meeting_state`, `meeting_elapsed_s`, `meeting_progress` and
      `meeting_spill_minutes`; and the percentage is a *batch-phase* metric (0 while
      recording) because a live capture has no known total, with elapsed time conveying
      progress meanwhile. D4 drives the percentage from the diarizer callback.

      **Known gap, deliberately accepted:** the ratatui frontend mirrors the two settings
      rows and renders the meeting state, but has no capture *action* row — a ratatui user
      starts via the control API. Recorded in `docs/meeting_mode.md` rather than papered over.
- [x] **D3** Real `diarizers/sherpa_onnx.py` + a spike on real weights. **DONE.**

      Implements the D1 contract with a lazy cached import (the no-`sherpa_onnx`-at-import
      property is pinned by a subprocess test and still holds), one engine cached per config,
      `num_speakers=None → num_clusters=-1`, thresholds forwarded and no merging of our own,
      and the caller's `progress` handed to `process()` through a one-line adapter because the
      library requires an `int` return while the contract callback returns `None`. Input is
      resampled to `engine.sample_rate` rather than assuming 16 kHz.

      **Measured** on a synthetic two-voice 16 kHz clip (20.653 s), sherpa-onnx 1.12.25:
      **RTF 0.145** with a known speaker count and **0.296** with auto-detection — both inside
      §7's ≤0.3 target. Peak RSS ~260 MB. Boundaries matched all 4 synthetic turns with the
      expected A,B,A,B ordering. On a monologue, auto found a **single** speaker, so
      `should_label()` stays off — the failure mode §11 names as most likely to annoy users.

      **Plan correction, verified against the installed 1.12.25:** `window_shift_ratio` does
      **not** exist on `OfflineSpeakerSegmentationPyannoteModelConfig` — passing it raises
      `TypeError` — and result items carry no `.overlap`, so that field is always `False`.
      §4.1's example code is wrong on both counts and has been corrected there.
      The obsolete D1 assertion that `load_backend("sherpa-onnx") is None` was split into
      "an absent backend still degrades to `None`" (using names that stay absent after the
      merge) and "the shipped backend exists and satisfies the contract".
- [ ] **D4** Turn slicing → per-turn ASR → per-turn post-processing.
      *Done = a two-voice E2E asserts correct turn count and ordering, not just words.*
- [ ] **D5** Output artifact + speakers map. *Done = a real meeting produces a readable
      labelled transcript.*
- [ ] **D6** Settings (`diarization`, `diarization_speakers`, `diarization_model`).
      *Done = `off` byte-identical; single-speaker `auto` adds no label; both TUIs.*
- [ ] **D7** Eval entry + DER recorded next to the ASR numbers; `docs/TODO.md` verification
      notes for unverified platforms.

### 4E. ASR bake-off — Parakeet vs Cohere

Detail: [`asr-bakeoff.md`](asr-bakeoff.md). **DONE.** Verdict: **keep Cohere as the default.**

| | Cohere default | Cohere `--int8` | Parakeet |
| --- | --- | --- | --- |
| WER / CER / exact | **3.35 / 1.40 / 71.1 %** | 3.57 / 1.42 / 69.7 % | 9.52 / 6.84 / 50.7 % |
| RTF (Σdur/Σlat) | 7.35× | 13.48× | **36.44×** |
| peak RSS | 4.69 GiB | **22.15 GiB** | **1.57 GiB** |
| silence hallucination | 0/12 | 0/12 | 0/12 |

Parakeet is 5× faster and 3× smaller and still loses, because it is ~1.9× the errors on the
*fair* comparison (whole-clip, batcher bypassed: 2.65 % vs 4.93 %) and Cohere wins **every**
slice. Measured on the real 154-clip set, all three configs, fresh.

- [x] **E1** Parakeet TDT 0.6B v3 added as a second `transcribe2.BACKENDS` entry.
      `DEFAULT_BACKEND` stays `cohere`. 19 model-free tests, model-free by construction —
      no `sherpa_onnx` at import, pinned by a subprocess test.
- [x] **E2** The bake-off, with **all three axes measured together** as the plan required —
      accuracy *and* RTF *and* peak RSS. The plan's warning was well placed: accuracy alone
      would have missed that Parakeet is 5× faster and 3× smaller in memory, which is the
      whole reason the question was worth asking.
      *The comparison is unfair to Parakeet as measured, and the doc says so:* its pipeline
      WER is ~2× its whole-clip WER because the micro-batcher's energy-cut chunks make the
      TDT decoder emit zero tokens for some segments — proven by feeding the same five
      chunks to both backends (Cohere transcribes all five, Parakeet empties three). So the
      honest headline is the whole-clip pair, and Parakeet would need a `micro_batcher`
      change to be judged fairly. It still loses.
- [ ] **E3** *(now applicable — Cohere is kept)* Evaluate the **sherpa-onnx int8 ONNX** path
      for Cohere: it drops the `torch` dependency (~0.7–1 GiB installed) and unifies
      ASR+VAD+punctuation+diarization on one runtime. **Newly motivated by E2:** Cohere's
      *existing* `--int8` option (torch dynamic quantisation) peaks at **22.15 GiB**, so the
      ONNX path is now the plausible way to get int8 at all — but note that is a different
      mechanism from what the flag does today. ~2.9 GB download.
- [x] **E4** **§3's "diarize first, then transcribe each turn" design survives.** Cohere
      stays the default and has no timestamps at all, so nothing changes. Worth recording
      that the question was answered the other way too: Parakeet has **no native word
      timestamps** (every `words`/`segment_*` field is empty), only token
      `timestamps`/`durations` from which word boundaries are *reconstructible* from
      space-prefixed word-pieces. So even on the backend that would have changed the design,
      the answer would have been "usable, not native" rather than a clean yes.

**Two findings from E that are not about Parakeet at all:**

1. **`eval/results.json` is stale.** Re-running its exact config gives **3.57 %** WER, not
   the recorded 5.10 %; the refs are identical but **85 of 154** hypotheses differ, so the app
   changed underneath the baseline (post-processor/chunker drift since `b7f9ad4`). The app
   improving is good news; a recorded baseline that no longer reproduces is not, because
   A4/C7 want to gate regressions against it. **It needs regenerating.**
2. **Silence did not hallucinate.** The plan warned sherpa-onnx needed its own silence guard
   because the decoder hallucinated on silence — that did not reproduce (0/12, and the raw
   decoder gives 0/12 too, so the guard is not what produces the zero). Worth knowing before
   anyone builds machinery for a problem that is not there.

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
