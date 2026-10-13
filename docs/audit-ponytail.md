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

## Status — updated after the cleanup waves

**All but one of the 13 defects in §1 are fixed and merged**; item 13, the duplicate config save,
is now partly fixed and the exception is explained in the table. Each fix is pinned by a test
that was proven to *fail* when the bug is reintroduced. The two that mattered most were found by
`audit-eval` in code written this same session, and both were wrong in the expensive half: the
DER assignment returned a suboptimal mapping on 431/3000 fuzzed matrices, and the review
report's silence-hallucination rate was inverted.

_Re-verified against the working tree 2026-10-13: §1 items 4–12 fixed; item 13 partly fixed
(§1 table below). §4 closed. §3 partly done and partly declined — see below._

**§2 duplication is folded.** The Rust items landed in one wave (net **-171 lines**: four
byte-identical `fuzzy_subsequence` copies, the ~40 hand-rolled send-or-quit sites, six run loops,
nine centring sites, the cursor wraparound) — which also fixed a real bug, since
`theme_picker` and `preset_picker` were missing the `terminal.clear()` the other four had. The
three backend registries landed in another (net **-80 lines** of triplicated logic), with the
user-visible error text verified character-for-character and the lazy-import property re-pinned
by its existing subprocess tests.

**§3 modularity: the two named violations are fixed, and the `t2` split is declined because its
premise was measured false.** Done 2026-10-13: the network SLM client left `post_processor.py`
for `slm_client.py` (301 lines), so importing the text cleaner no longer drags an HTTP client
into an offline-first package; and the seven settings hand-mirrored from `t2` into
`post_processor` are now published by one `apply_settings_from_config`, called once from each of
`t2`'s two snapshot paths, so a setting can no longer reach the load path and miss the reset
path (the A6 divergence class). Sizes now: `t2.py` **4,476**, `post_processor.py` **3,444**.

**Declined, with the measurement:** the audit said config loading "cannot be imported without
pulling in `rich`". It can — `import t2` loads `numpy`, `sounddevice` and `urllib.request`, and
**not** `rich`, because every `rich` import in `t2.py` is already function-local. Splitting
~4,500 lines across ~125 importers to fix a cost that does not exist is the opposite of the
rule this audit is written against, so it was not done. If `t2.py`'s size is a problem in its
own right, that is a separate decision with its own justification.

**What is genuinely still open in §3:** the model-id lists remain declared three times
(`t2.FORMATTER_MODELS`, `t2.DIARIZATION_MODELS`, and the registry's `model_ids`), held together
only by tests; and the Rust defaults are still pinned by label rather than by value, which is how
a stale backend default slipped through once already.

**A second offline-first violation turned up while pinning the first.** The new import guard
failed on its first run: the module-level dictionary auto-load reached `model_download` — and
`urllib.request` with it — through `load_merged_dictionaries`, so `post_processor` was still not
clean after the SLM split. It now loads on first use, from the entry points its own docstring
always claimed. The same class of leak in `t2` was then fixed too, which the audit never named:
the pure path helpers (`find_repo_root`, `get_data_dir`) were living in the downloader, so any
module needing a path imported an HTTP client. They moved to `voice_transcriber/paths.py` and
`import t2`, `import dictionary` and `import post_processor` now all leave no `urllib.request` or
`http.client` in `sys.modules`.

**§4 is closed except one item.** Deleted: the `_on_tui_toggle_autotype` alias, the 14 `_dispatch`
aliases (already gone by then), `ignore_overlap`/`_is_reference_overlap`. Wired in:
`formatter.may_run` is now the single statement of the formatter gate, so
`post_processor._removes_noise()` is no longer a second copy of it. Not dead after all:
`change_device` and `on_open_preset_picker` have real callers, `_SPAWN_ATTEMPTS` lives in the
test guard, and `EXPECTED_ORDER` is compared. Deleted after checking rather than on the audit's
word: `diarize.may_run`, which the audit said to "keep or wire in". It is not the same rule as the
shipped gate — `main.py` uses `get_diarization() == "on"`, while `may_run` returned True for
anything but `"off"` — and it fails the *unsafe* way: `may_run("auto")` and `may_run("2")` were
True, where `"2"` is a speaker *count*, not the toggle. `t2.normalize_diarization`'s docstring
states the opposite policy in as many words ("fail-safe off: the pass loads a model, so a typo
must not turn it on by accident"), so the helper contradicted the shipped design and would have
silently reversed it if anyone wired it in. Nothing called it. Its test is replaced by one that
pins the real policy and the normaliser, which had no test at all.

One §4 entry stopped being true and is worth correcting here: `_SPAWN_ATTEMPTS` was listed as
dead state, and it was — but the fix was to *assert* it rather than delete it, because a spawn
attempted on a background thread only surfaces as a warning. It is now a real guard.

Verified after the merges: `tests/shared + tests/linux + tests/wsl + tests/windows` **1684
passed, 3 skipped**; `ruff` clean; `cargo test` 63 passed and `clippy -D warnings` clean.

---

## 1. Real defects — fixes, not removals

| # | Defect | Status |
| --- | --- | --- |
| 1 | **The DER assignment used the wrong reduced cost** (`a + u + v` instead of `a - u - v`), returning a suboptimal speaker mapping on **431 of 3000** fuzzed matrices. A suboptimal mapping invents speaker confusion, so DER was over-reported. The committed fixture was optimal by luck, so the recorded 1.45 % stands. | **FIXED** — pinned by an anti-diagonal regression case and a brute-force property test, both confirmed failing with the sign reverted |
| 2 | **The review report's silence-hallucination rate was inverted.** It counted *empty* hypotheses, so `review.json` said `1.0` where `score.py` said `0.0`. A hallucination is a *non-empty* hypothesis on a silence clip. The report had **no tests at all** before this. | **FIXED** — 4 model-free tests |
| 3 | **The first-use download prompt called `input()` on a daemon thread**, guarded only by `isatty()` — and under ratatui stdin *is* a tty. The prompt was drawn under the TUI and the download blocked on keystrokes the TUI was consuming, so `formatter: on` looked inert on a fresh machine. Found independently by two audits and a prior review. | **FIXED** — prompt confined to a synchronous context; size reported via the status handler; explicit `formatter-download` verb |
| 4 | **An installed build writes meeting transcripts into the launch CWD.** `meeting_output_dir()` resolves a relative setting against `_repo_root() or os.getcwd()`, and the shipped default `"meetings"` makes the per-user fallback unreachable. `find_repo_root()` returns `None` for a Nix/AppImage install, so transcripts land in `/tmp/meetings` or `~/meetings`. | **FIXED** — pinned by `test_relative_output_dir_without_a_checkout_uses_the_data_dir`; with no checkout a relative setting resolves under `model_download.get_data_dir()`, not the launch CWD |
| 5 | **The Rich speaker editor advertises `[J/K] Reorder`, which cannot run.** Uppercase `K`/`J` are consumed as selection moves earlier in the handler, so the reorder branches are unreachable and `move_speaker_name` (unit-tested, working) is never called. The Rust editor disagrees: `j`/`k` navigate, `J`/`K` reorder. | **FIXED** — pinned by `test_speaker_editor_key_map_reorders_on_uppercase_jk`; lowercase `j`/`k` select, uppercase `J`/`K` call `move_speaker_name` |
| 6 | **`_on_tui_cycle_diarization_model` is defined twice** in `main.py`, byte-identical; the second silently wins, so editing the first does nothing. | **FIXED** — a single definition (`main.py:968`); a second would fail the `ruff` F811 lint gate |
| 7 | **`MeetingPipeline.process` renders outside its `try`.** `_render` can raise on a bad turn value and the only net is a generic log, so a render bug loses the whole transcript with no artifact and no `error` on the result. `_write` errors *are* reported. | **FIXED** — pinned by `test_render_failure_keeps_the_transcript_in_memory`; `_run` (and so `_render`) sits inside the try and a failure returns `_finish(..., error=...)` |
| 8 | **Six copies of the Rust `#[cfg(test)]` split**, none asserting the marker was found. If it is renamed, `split(...)[0]` is the whole file — and the Rust test block duplicates every pinned label, so the parity guards pass for the wrong reason. | **FIXED** — one helper, `tests/shared/_rust_source.py::rust_implementation`, which asserts the `#[cfg(test)]` marker exists; every Rust parity guard uses it |
| 9 | **`_SPAWN_ATTEMPTS` is dead state** — appended to and cleared, never asserted. The network guard's `_BLOCKED` *is* asserted; the spawn tally is not, because that guard raises synchronously. Four lines implying a protection that does not exist. | **FIXED** — the tally lives only in the test guard (`tests/shared/conftest.py`) and is asserted (`test_the_real_llama_server_spawn_guard_fires`); `_SPAWN_ATTEMPTS` is gone from `src/` |
| 10 | **Two validators for `diarization_speakers` with different ranges** — `t2` restricts to 2–8, `diarize.parse_speakers` accepts any positive int, and the pipeline parses twice. | **FIXED** — pinned by `test_speaker_count_validators_agree_on_the_2_to_8_range`; `diarize.parse_speakers` now degrades anything outside 2–8 to auto |
| 11 | **`app.rs` has a comment contradicting the value under it** — it says the default is the subprocess backend and "not `s1-mini`", immediately above `formatter_model: "s1-mini"`. It describes a bug, then documents the wrong conclusion. | **FIXED** — the comment now calls `s1-mini` the shipped *model* name and says it is not a backend; the value is pinned by `test_rust_app_defaults_match_the_python_shipped_defaults` |
| 12 | **`EXPECTED_ORDER` is written to JSON but the gate compares ref-derived order instead**, so editing the constant keeps every test passing while the recorded field lies. | **FIXED** — the gate itself now compares `ref_order == EXPECTED_ORDER` (`eval/score_diarization.py:744`); in-source guard, no separate test |
| 13 | **Two config saves per settings change** — the claim was that the setters persist and the control layer calls `save_audio_config()` again. Only partly true: `set_formatter`, `set_structure_mode`, `set_cleanup_mode` and friends do persist, but `set_punctuation_mode`, `set_number_digits` and the direct global assignments do not. | **PARTLY FIXED** — all 39 sites classified; the 16 that provably followed a persisting setter are gone, and the other 23 are the only save |

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
| `formatter.may_run` / `diarize.may_run` | no production callers — the real gates are re-implemented in `post_processor` and `main`. **Resolved:** `formatter.may_run` is now the gate, so the rule has one statement; `diarize.may_run` was deleted — it fails *open* and contradicts `t2.normalize_diarization`'s documented fail-safe policy |

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

---

# Second round — 2026-10-10

Seven adversarial audits of the whole repo (engine core, control/transport, HAL and the four
hotkey backends, meeting/diarize/formatter, model download, the post-processor, the Rust
frontend), run against the same ruleset: work like the senior dev who just inherited the
code and will be paged when it breaks, every finding needs a concrete reproduction, and
**change nothing**. Verified after each fix: `tests/shared + tests/linux + tests/wsl +
tests/windows` green, `ruff check src tests tools eval` clean, `cargo test` 67 passed and
`clippy -D warnings` clean.

## The eight must-fixes, all landed and pinned

| # | Was | Now | Pinned by |
| --- | --- | --- | --- |
| 1 | `save_audio_config` wrote in place, and the loader treated a file that failed to parse like a missing one: a truncated file reverted every setting, and the next save rewrote it as defaults. A scalar `hotkeys:` value raised `TypeError` and skipped every key after it. | Temp file + `os.replace` under a lock; an unreadable file is preserved as `<name>.corrupt-<ts>` and named in the log before a new one is written; `parse_binds` raises `KeybindError` for a non-iterable. | `test_config_sync.py`: truncated-file backup (asserts the original bytes survive and no `.tmp` is left) and the bad-`hotkeys` scalar |
| 2 | `centered_rect` returned the requested size even when the terminal was smaller, and the callers hand it to `Clear`, which indexes the frame buffer — a panic on a narrow/short terminal that left the terminal in raw mode. | Clamped in the one shared helper. | `modal.rs` clamp test plus narrow-terminal render tests in `theme_picker`, `preset_picker`, and both `settings_picker` prompts (all four fail with the clamp removed) |
| 3 | A contextual rule's `target` was passed to `re.sub` as a replacement *template*: `\1` echoed the matched text, and `path\end` raised `PatternError` — which the ASR caller swallows into an empty transcript. | Function replacement, so a backslash is literal text. | `test_dictionary.py::test_a_contextual_target_is_inserted_literally` |
| 4 | The `"I mean"` retraction exemption had no guard, so the greedy matcher read `"know what"` as the retracted value: `"you know what I mean right"` → `"you right"`. | Require a preposition, or values of the same known kind on both sides. Real retractions (`"to Bob I mean Alice"`) still resolve. | `test_post_processor.py::test_the_i_mean_idiom_is_not_a_retraction` |
| 5 | The dictionary file was escaped by hand: a value with a newline wrote a file that could not be parsed (and the save itself raised, leaving the corrupt file), and values YAML re-types were changed (`"007"` → `"7"`, `"yes"` → `"True"`). | `json.dumps` scalars, which always read back exactly. | `test_dictionary.py::test_saved_values_round_trip_exactly` |
| 6 | Completeness was "the required filenames exist", and the revision written into `SOURCE.json` was never read: a revision bump was a no-op for every existing user. | A recorded revision that differs counts as incomplete; no `SOURCE.json` still passes. | `test_model_download.py::test_a_stale_revision_recorded_in_source_json_is_not_complete` |
| 7 | `are_modifiers_pressed` omitted Super/Meta on Linux and Windows, so with a `meta`-based bind the engine typed while Super was down and the desktop read the letters as shortcuts. | Meta keys added to the predicate on both backends. | `tests/linux/test_hotkey_binds.py`, `tests/windows/test_hotkey_binds.py` (both fail without it) |
| 8 | `close()` built its join list before joining the capture thread, so it never joined the finalize that the capture thread starts — and the finalize's `finally` then reset the buffer and state of whatever session was recording by then. | Join the finalize after the capture join, and only clear state the finalizer still owns. | `test_meeting.py::test_close_waits_for_the_finalize_it_cannot_see_yet` (fails without it) |

## Outstanding from the second round — not fixed here

Ranked as before. Every one has a reproduction in the round-two reports; the ones with a
file cited were confirmed by running them.

**Should fix.** WSL `start()` kills every other instance's hotkey bridge
(`platform/wsl/hotkeys.py:58`). macOS `set_binds` returns `True` for letter/digit binds that
resolve to an empty key set (`platform/macos/hotkeys.py:262`). `cleanup_mode="off"` still
substitutes and deletes words (`post_processor.py:3229`). Structure mode `blocks` welds a
lowercase list into one item (`post_processor.py:3327`). Adjacent spoken numbers are fused
by the phone rules (`"we need 500 2000 more"` → `5002000`, `post_processor.py:901`).
`validate_turns` drops numpy scalars, so a numpy-based diarizer is silently ignored
(`diarize.py:247`). A short spill file truncates the recording with no error
(`meeting.py:323`). One dropped connection aborts the first-run download
(`IncompleteRead` is not retried, `model_download.py:586`) and two concurrent installs can
delete each other's weights (`:863`). `copy_to_clipboard` is an advertised setting nothing
reads (`t2.py:1087`). Reset to Defaults leaves the previous hotkeys live and reports a
backend it did not switch (`main.py:909`). A corrupt control endpoint file crashes the CLI
client (`control.py:774`) and a non-ASCII token raises instead of answering `unauthorized`
(`control.py:709`). `VT_CONTROL_SOCKET` disables the control API on Windows (`control.py:395`).
Unbounded threads and 1 MiB reads before the token check (`control.py:658`). The control
verbs `output`/`punctuation`/`theme` accept garbage silently (`main.py:1858`). The release
workflow's `windows-exe` job has no `if:` guard and `workflow_dispatch` defaults to
`v1.3.0`, so a manual run can overwrite a released version's assets
(`.github/workflows/release.yml`). `ruff` never lints `scripts/` or `platforms/`, where 48
diagnostics live (`.github/workflows/ci.yml`).

**Also.** Rich TUI `Esc` does not quit (`tui.py:748`). Quoted booleans invert the setting
(`keep_bluetooth_handsfree: "false"` is truthy, `t2.py:919`). Linux never monitors a device
that lacks both Alt and Shift, so an F13 foot pedal is dead (`platform/linux/hotkeys.py:285`).
Linux raises on an invalid bind where the other three backends fall back
(`platform/linux/hotkeys.py:44`). WSL reorders a fast tap's callbacks, which can leave
recording stuck (`platform/wsl/hotkeys.py:130`). Windows/macOS never clear `middle_pressed`
when the mode is switched off mid-hold (`platform/windows/hotkeys.py:146`). The ratatui
speaker editor overwrites the engine's list on close (`speaker_picker.rs:488`) and meeting
capture cannot be started from that frontend at all (`main.rs:628`). Enter on an empty
settings search closes the modal (`settings_picker.rs:960`).

**Lean.** Dead code: `post_processor.process_slm_llm_stream_concat`,
`get_contextual_rules`, the casing block that runs before the dictionary it protects
(`:2343`); `formatter.ASSISTANT_PREFIX`; `meeting.SPILL_MINUTE_CHOICES`;
`keybinds.vk_codes`/`canonical_pynput_name`/`bind_hit` (~55 lines whose docstring claims the
pynput backends use them — they do not, and `bind_hit` is wrong for a bare `meta`);
`platform/wsl/bridge_server.py` plus its launcher, which points at files that do not exist;
`t2.COPY_TO_CLIPBOARD`; ~120 lines of write-only fields in `tui-rs`. Duplication: the six
picker state machines (~350 lines Rust) and the Windows/macOS hotkey backends, which are
line-for-line copies that have already drifted. Dependencies: `datasets` (only `eval/`
imports it) and `pytest-timeout` (no marker, no `--timeout` flag anywhere).

**Explicitly out of scope, unchanged:** the `t2.py`/`post_processor.py` modularity split,
the seven hand-mirrored settings, and the six copies of the Rust `#[cfg(test)]` split — all
still waiting on an owner decision from the first round.

---

# Third pass — 2026-10-10 (the optional list, worked)

The eight must-fixes above were followed by the remaining findings, in four groups, all
landed and verified together: **1740 passed, 3 skipped** (shared + linux + wsl + windows +
macos), `ruff check src tests tools eval scripts platforms` clean, `cargo test` green with
`clippy -D warnings` clean, `shellcheck` clean.

## Wrong output in the default configuration

| Was | Now | Pinned by |
| --- | --- | --- |
| `cleanup_mode="off"` still rewrote and deleted words: the AI-mishearing and "addressing" repairs were the only two steps in the noise block missing the `_removes_noise()` gate their neighbours have | gated like 0a/0c, so `off` is verbatim | `test_cleanup_modes.py::test_the_homophone_repairs_are_gated_by_cleanup_mode` |
| Adjacent quantities were welded by the phone rules (`"we need 500 2000 more"` → `5002000`) | the 3+4 and 3+3+4 patterns now require a call phrase (`call`/`dial`/`number is`/`ext`…), so `call 555 1212` still collapses and two bare quantities do not | `test_phone_group_does_not_over_collapse` (5 cases) |
| Structure `blocks` welded a lowercase list into one bullet: the sentence-boundary repair deleted the periods the list stage needs | the boundary repair is skipped when a layout is in force, and both stages now resolve the mode through one helper | `test_structure_blocks.py::test_lowercase_bullet_cues_become_a_list_in_blocks_mode` |
| Spoken IP was dead in the default `auto` number mode (`10 dot 0 dot 0 dot 0 slash 24` → words) | `dot`/`slash` are cardinal guards, so the digits survive to the path pass | `test_post_processor.py::test_spoken_ip_survives_the_number_pass` |

## The cheap one-liners

Quoted booleans no longer invert (7 raw `config.get()` loads now go through
`_normalize_bool`). Rich TUI `Esc` quits, without letting an arrow key quit (the loop peeks
for a following byte first). `Enter` on an empty settings search returns `Continue` instead
of closing the modal. The ratatui speaker editor no longer overwrites the engine's list on
close. `middle_pressed` is cleared when mouse mode is switched off mid-hold, on Windows and
macOS. `t2.COPY_TO_CLIPBOARD` and the `copy_to_clipboard` config key are gone — the setting
was advertised in three example configs and the README and read by nothing (the engine's
per-dictation Ctrl override in `main.py` is a different mechanism and stays).

## Robustness

`_http_get` retries `http.client.HTTPException` (so `IncompleteRead` no longer aborts a
2.8 GB first run) and treats a short body as retryable. The install swap takes a lock,
re-checks completeness before `rmtree`, and — deliberately — *proceeds* rather than failing
when a lock outlives its owner, because the re-check is the real protection and a hard
failure would make a killed installer's lock permanent (the lock is only ever removed by
its owner). A short spill file now marks the meeting `error` as incomplete while still
emitting the partial transcript. In `control.py`: the endpoint port is validated where it is
read, the token is compared as UTF-8 bytes, `VT_CONTROL_SOCKET` no longer disables the API
on a host without `AF_UNIX`, live connections are capped at 32, and a `wait` value is capped
at an hour (documented in `docs/control_api.md`).

## Housekeeping

Removed: `post_processor.process_slm_llm_stream_concat` (55 lines),
`get_contextual_rules`, `formatter.ASSISTANT_PREFIX` (and its test),
`meeting.SPILL_MINUTE_CHOICES` (a third copy of the spill list), `tui.copy_to_clipboard`
(write-only), the `copy_to_clipboard` key, `pytest-timeout` (nothing used it) and `datasets`
from the runtime dependency lists (only `eval/` imports it; it stays in the dev extras and
the flake dev shells). `ruff` now lints `scripts/` and `platforms/` in CI, and the 48
diagnostics it found there are fixed. The release workflow's `windows-exe` job has the same
`if:` gate as the AppImage job, and `workflow_dispatch` requires an explicit `tag_name`
instead of defaulting to `v1.3.0` — which could attach an unreleased build to a shipped
version's assets. Two false claims in `docs/control_api.md` (that Windows cannot use the
control API, and that a WSL client finds a Windows engine's endpoint automatically) are
corrected.

## Two second-round findings were wrong — they would have deleted live behaviour

Both were caught by checking before deleting, and both are worth recording because they
change how the "lean" list should be read:

1. **The dictionary-casing block is load-bearing** (`post_processor.py:2384`). It was
   reported as unable to affect anything because `normalize_mid_sentence_casing` runs before
   the dictionary pass. But it does not act on that pass: it *adds* each capitalised
   dictionary target to the persistent `TECHNICAL_ACRONYMS_AND_PROPER_NOUNS` set, so the
   replacement word survives mid-sentence lowercasing when it appears in the transcript on
   its own. Verified: with the registration, `"we use Gitea daily"` keeps `Gitea`; with it
   removed, the same input becomes `gitea`. Kept.
2. **`keybinds.bind_hit` is not broken and is not unused.** The report said a bare `meta`
   failed to match; verified, `bind_hit(parse_bind("meta"), ["meta"]) is True`. It is the
   single written statement of the matching contract, and `TestBindMatching` is the only
   test that pins that contract once rather than three times per backend. Kept — the actual
   defect was the docstrings claiming the pynput backends *call* it when they each grew their
   own matcher, and those two comments are now corrected.

Corrected. The Rust front end lost `App::vu_peak`, `App::cycle_theme`, `App::cycle_punctuation`,
`App::secondary_device`, `App::sound_theme`, `Level::default_color`, `Theme::next`,
`ui::status_lines`/`status_line`/`status_line_for` and `SettingKind::AutoPunctuate`. **Three
of those had to be put back**: `secondary_device`, `sound_theme` and their `Wire::Cfg`
entries are pinned by `tests/shared/test_ratatui_cfg_contract.py`, which asserts (a) that
every key the engine emits in `cfg` is declared in the Rust wire and (b) that `App::new`
initialises `sound_theme` with the same value as `t2.SOUND_THEME`. Both guards exist to
catch Python/Rust drift, so the fields stay — a write-only field that a parity guard covers
is not dead code, it is an accepted key. The other six deletions stay deleted.

## Left for an owner decision
`platform/wsl/bridge_server.py` plus `platforms/wsl/run_wsl_bridge.ps1`: the launcher points
at `src/wsl_bridge.py` / `src/wsl_bridge_host.py`, which do not exist in this tree, and the
server has no caller in `src/` (only a fake daemon in `tests/e2e/` speaks its protocol). It
is an unauthenticated `127.0.0.1:50555` command executor with a blocking accept loop, so
neither deleting it nor repointing the launcher can be exercised without a Windows+WSL host.
Recorded in `docs/TODO.md` instead of acted on. Still open from the first round, unchanged:
the `t2.py`/`post_processor.py` split, the seven hand-mirrored settings, the six Rust picker
state machines, the Windows/macOS hotkey backend copies, and the six `#[cfg(test)]` splits.
