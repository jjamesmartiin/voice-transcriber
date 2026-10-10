# Ponytail audit — 2026-10-09

Three adversarial audits of the `wip/feature-parity-2026-10` branch, run against the
[ponytail](https://github.com/DietrichGebert/ponytail) ruleset (MIT): "the smallest
complete change; the best code is the code never written". Each auditor was told to
work like the senior dev who just inherited the code and will be paged when it breaks,
to verify every claim by running something, and to **change nothing**.

| Audit | Area |
| --- | --- |
| `audit-eval` | the verification apparatus: the DER scorer, the review report, the guard tests |
| `audit-src` | the product source: `post_processor`, `t2`, formatter, meeting/diarization, registry |
| `audit-tui` | both frontends and the Python↔Rust bridge |

**The owner's constraint, which governs everything below:** nothing is removed without
being listed first, and **unbuilt-but-intended features are out of scope for deletion**.

---

## 1. Real defects — fixes, not removals

| # | Defect | Status |
| --- | --- | --- |
| 1 | **The DER assignment used the wrong reduced cost** (`a + u + v` instead of `a - u - v`), returning a suboptimal speaker mapping on **431 of 3000** fuzzed matrices. A suboptimal mapping invents speaker confusion, so DER was over-reported. The committed fixture was optimal by luck, so the recorded 1.45 % stands. | **FIXED** — pinned by an anti-diagonal regression case and a brute-force property test, both confirmed failing with the sign reverted |
| 2 | **The review report's silence-hallucination rate was inverted.** It counted *empty* hypotheses, so `review.json` said `1.0` where `score.py` said `0.0`. A hallucination is a *non-empty* hypothesis on a silence clip. The report had **no tests at all** before this. | **FIXED** — 4 model-free tests |
| 3 | **The first-use download prompt called `input()` on a daemon thread**, guarded only by `isatty()` — and under ratatui stdin *is* a tty. The prompt was drawn under the TUI and the download blocked on keystrokes the TUI was consuming, so `formatter: on` looked inert on a fresh machine. Found independently by two audits and a prior review. | **FIXED** — prompt confined to a synchronous context; size reported via the status handler; explicit `formatter-download` verb |
| 4 | **An installed build writes meeting transcripts into the launch CWD.** `meeting_output_dir()` resolves a relative setting against `_repo_root() or os.getcwd()`, and the shipped default `"meetings"` makes the per-user fallback unreachable. `find_repo_root()` returns `None` for a Nix/AppImage install, so transcripts land in `/tmp/meetings` or `~/meetings`. | **OPEN** |
| 5 | **The Rich speaker editor advertises `[J/K] Reorder`, which cannot run.** Uppercase `K`/`J` are consumed as selection moves earlier in the handler, so the reorder branches are unreachable and `move_speaker_name` (unit-tested, working) is never called. The Rust editor disagrees: `j`/`k` navigate, `J`/`K` reorder. | **OPEN** — found by two audits independently |
| 6 | **`_on_tui_cycle_diarization_model` is defined twice** in `main.py`, byte-identical; the second silently wins, so editing the first does nothing. | **OPEN** — found by two audits |
| 7 | **`MeetingPipeline.process` renders outside its `try`.** `_render` can raise on a bad turn value and the only net is a generic log, so a render bug loses the whole transcript with no artifact and no `error` on the result. `_write` errors *are* reported. | **OPEN** |
| 8 | **Six copies of the Rust `#[cfg(test)]` split**, none asserting the marker was found. If it is renamed, `split(...)[0]` is the whole file — and the Rust test block duplicates every pinned label, so the parity guards pass for the wrong reason. | **OPEN** |
| 9 | **`_SPAWN_ATTEMPTS` is dead state** — appended to and cleared, never asserted. The network guard's `_BLOCKED` *is* asserted; the spawn tally is not, because that guard raises synchronously. Four lines implying a protection that does not exist. | **OPEN** |
| 10 | **Two validators for `diarization_speakers` with different ranges** — `t2` restricts to 2–8, `diarize.parse_speakers` accepts any positive int, and the pipeline parses twice. | **OPEN** |
| 11 | **`app.rs` has a comment contradicting the value under it** — it says the default is the subprocess backend and "not `s1-mini`", immediately above `formatter_model: "s1-mini"`. It describes a bug, then documents the wrong conclusion. | **OPEN** |
| 12 | **`EXPECTED_ORDER` is written to JSON but the gate compares ref-derived order instead**, so editing the constant keeps every test passing while the recorded field lies. | **OPEN** |
| 13 | **Two config saves per settings change** — the setters persist and the control layer calls `save_audio_config()` again. Mirrors pre-existing house style, so it is waste rather than a regression. | **OPEN** |

---

## 2. Duplication that should be folded (behaviour-preserving)

| Item | Copies | Where |
| --- | --- | --- |
| `fuzzy_subsequence` | **4× byte-identical** | `mic_picker`, `preset_picker`, `theme_picker`, `settings_picker` |
| "send command or quit" idiom | **~40×** | 26× in `settings_picker`, plus `bind`/`mic`/`preset`/`theme`/`speaker` |
| alternate-screen run loop | **6×, already drifted** | `theme_picker` and `preset_picker` omit `terminal.clear()` the other four have |
| popup centring maths | 9 sites | all pickers |
| `move_up`/`move_down` wraparound | 4× | `bind`, `mic`, `theme`, `speaker` |
| backend registry (`BACKENDS`, `load_backend`, …) | **3×** | `transcribe2`, `formatter`, `diarize` |
| `#[cfg(test)]` source split | 6× | the parity guards (§1 item 8) |
| `cfg` field list | **5 places, hand-synced** | `tui_ratatui.py`, `ipc.rs`, `main.rs`, `app.rs` — only `speakers` is cross-checked |

**`audit-tui` estimate: ≈ -350 lines with zero behaviour change.** Note it judged
`speaker_picker.rs` (641 lines) **proportionate** — the second-smallest picker, and the
only one that mutates an ordered list rather than selecting an option.

---

## 3. Modularity — real, but riskier

- **`t2.py` is 4,247 lines** doing config I/O, settings state, settings UI and two
  full-screen editors. Config loading cannot be imported without pulling in `rich`.
  Suggested split by job: a settings-state module and a `tui_settings.py`.
- **`post_processor.py` is 3,433 lines** combining six unrelated jobs, including a
  **network SLM client** — so importing the cleaner pulls in an HTTP client, against
  `src/`'s offline-first rule. Suggested split: `slm_client.py`, `numbers.py`,
  `structure.py`, keeping `clean_transcription` as the pipeline.
- **Seven settings are hand-mirrored** from `t2` into `post_processor`, and both
  `load_audio_config` and `reset_to_defaults` must re-publish each one. This is the
  documented divergence class that already produced A6.
- **Model-id lists are declared three times** (`t2.FORMATTER_MODELS`,
  `t2.DIARIZATION_MODELS`, the registry's `model_ids`), held together only by tests.
  Deliberate — `t2` avoids importing `formatter` at module scope — but a sync burden.
- **Rust defaults are unguarded**: every guard pins labels, not values, which is how a
  stale backend default slipped through once already.

---

## 4. Probably dead — **owner's call, nothing removed yet**

| Item | What is lost |
| --- | --- |
| `change_device` branch (`tui_ratatui.py:284`) | a mic-change callback **neither frontend ever fires** |
| `on_open_preset_picker` | a callback path never fired; the verb is called directly |
| `_on_tui_toggle_autotype` alias | an alias with zero references |
| 14 unpinned `_dispatch` aliases | **tolerance for an older `vt-tui` build using a different spelling** — a real trade-off, not free |
| `ignore_overlap` / `_is_reference_overlap` | ~10 lines; mirrors md-eval's option, no caller |
| `EXPECTED_ORDER` (§1 item 12) | nothing; the ref-derived check is stricter |
| `_SPAWN_ATTEMPTS` (§1 item 9) | nothing |
| `test_der_is_the_sum_of_its_three_components` | 6 lines; near-tautological, overlaps four other tests |
| `formatter.may_run` / `diarize.may_run` | no production callers — the real gates are re-implemented in `post_processor` and `main`. **Keep or wire in; do not delete blind** |

---

## 5. Explicitly out of scope for deletion

Unbuilt or deliberately deferred, and therefore **not** over-engineering to be trimmed:
the E3 int8-ONNX backend (the toolchain landed, the feature did not), the SLM pass,
the model-bundle publication path, the AMI-SDM diarization number, the `cleanup_mode`
variants, and the `noop`/`fake` test backends.

**On the DER metric specifically:** `audit-eval` judged that a ~20-line
ordering-and-threshold check would give the same regression protection as the 616-line
scorer for today's smoke gate, and that the ~250 lines of math plus ~180 lines of tests
are removable **only if the AMI-SDM path is abandoned — which it does not recommend**.
The metric is now correct and pinned; keep it.

---

## Verdicts, as given

- `audit-tui`: *"Structurally sound and unusually well-documented; the only true defects
  are one shadowed duplicate method and one broken-and-advertised Rich key map, sitting on
  top of a large amount of mechanical Rust picker duplication that is safe to fold up."*
- `audit-src`: *"The new meeting/diarization surface is sound in shape and unusually well
  tested, but it ships one real install-path bug, one unreachable editor feature, and a
  background-download path that violates the repo's own 'never block the UI' invariant."*
- `audit-eval`: *"The DER metric is only defensible because a real AMI-SDM number is the
  documented next step… the reason to act is that the expensive half was never validated
  and is wrong."*

## The honest lesson

Three of the four real bugs were in **code written this session, by us, to verify other
code** — including 616 lines of precision instrumentation for a measurement the design
itself calls a smoke gate, whose expensive half was never validated and was wrong. Guard
proliferation gave diminishing returns: two guards are earned (a real 3 GB download and 42
leaked processes), several are preventive, and at least two silently stopped guarding.
