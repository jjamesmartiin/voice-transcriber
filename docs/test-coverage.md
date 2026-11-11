# Test coverage — the baseline

A dated snapshot, like `eval/results.json`: the command and the environment sit
next to the numbers so the measurement can be re-derived instead of trusted. It
exists to give the test work in [`TODO.md`](TODO.md) a target list — **a module
at 0% is a module nothing would notice breaking** — not to chase a percentage.

**Measured 2026-11-10** on this Linux host, model-free tiers only:

```bash
nix develop --command env COVERAGE_FILE=/tmp/vt-cov.data \
  coverage run --branch --source=src \
  -m pytest tests/shared tests/linux tests/windows tests/wsl tests/macos -q
nix develop --command env COVERAGE_FILE=/tmp/vt-cov.data coverage report --sort=cover
```

`.coverage` is gitignored, so pointing `COVERAGE_FILE` at `/tmp` is optional.
`coverage` lives in the dev shell only: it is a measuring instrument, never a
runtime dependency (see `flake.nix`, and the pin in
`tests/shared/test_nixpkgs_expression.py`).

## Totals

| | |
| :--- | ---: |
| Statements | 16,810 |
| Missed | 4,727 |
| Branches | 6,156 |
| Partially covered branches | 2,391 |
| **Total** | **69%** |
| Modules ≥ 70% | 44 of 60 |

## Below 70%, worst first

| Module | Cover | Stmts | Miss | Branches | Partial |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `check_devices.py` | **0%** | 8 | 8 | 2 | 2 |
| `transcribe_cohere.py` | **28%** | 333 | 228 | 128 | 106 |
| `platform/windows/audio_cues.py` | **31%** | 41 | 25 | 10 | 10 |
| `notifications.py` | **44%** | 233 | 124 | 72 | 47 |
| `platform/windows/tray.py` | **45%** | 380 | 195 | 120 | 81 |
| `dictionary.py` | **47%** | 513 | 260 | 204 | 122 |
| `t2.py` | **50%** | 2803 | 1289 | 1118 | 666 |
| `main.py` | **57%** | 1507 | 642 | 498 | 229 |
| `tui_ratatui.py` | **57%** | 682 | 261 | 320 | 169 |
| `platform/windows/overlay.py` | **62%** | 298 | 90 | 90 | 56 |
| `platform/windows/notifications.py` | **62%** | 111 | 34 | 38 | 22 |
| `hotkeys.py` | **65%** | 55 | 20 | 20 | 6 |
| `platform/linux/audio_cues.py` | **67%** | 30 | 9 | 6 | 3 |
| `transcribe_parakeet.py` | **69%** | 213 | 62 | 70 | 27 |
| `platform/windows/hotkeys.py` | **70%** | 316 | 90 | 110 | 38 |

The core is well covered and should stay that way: `post_processor` 86%,
`formatter` 93%, `control` 84%, `hal` 91%, `keybinds` 93%, `stats` 95%,
`audio_state` 96%, `backend_registry` 96%, `platform/windows/win32` 98%.

## What each gap actually means

Most of this list is **dark by construction, not untested** — read the reason
before writing a test:

| Gap | Why it is dark | Worth closing? |
| :--- | :--- | :--- |
| `transcribe_cohere.py` 28% | It needs the 3.9 GB weights, so only `tests/e2e/` reaches the real paths | Partly: the model-free half (arg parsing, backend resolution, error paths) should be covered in `tests/shared/` |
| `t2.py` 50%, `main.py` 57%, `tui_ratatui.py` 57%, `platform/windows/tray.py` 45%, `overlay.py` 62% | Rendering and event loops; the ratatui side is tested from Rust (`tui-rs/`, `cargo test` via `nix build .#vt-tui`) and the real UI is exercised by the TUI, not pytest | No — a Python test that drives a render loop mostly asserts on its own fake (see the fake-drift note in `TODO.md`) |
| `platform/windows/*` | The `windows` tier runs on Linux against fakes; no Windows host has ever run this | Real coverage needs the Windows VM listed in `TODO.md`, not more fakes |
| `notifications.py` 44% | Each platform's notifier is called through the HAL; the failure paths (`libnotify` missing, no D-Bus) are what dominate | Yes: those are cheap, hermetic, and they are what a user hits |
| `dictionary.py` 47% | The CLI paths (`list`, `remove`, `add-contextual`) are exercised by `tests/shared/test_dictionary.py`; the missing half is the error/backup branches | Yes, same reason |
| `check_devices.py` **0%** | Nothing imports it — it is a developer helper, not shipped behaviour | Either delete it or test it; 8 statements |

## Caveats a number like this hides

* **Subprocesses are not traced.** Several tests spawn `python -c …` on purpose
  (the import-isolation guards) and verify `sys.modules` in the child. Those
  lines look missed here even though they ran.
* **`tests/e2e/` is excluded** (`--source=src`, model-free tiers only). The ASR
  backend's real paths are covered there, locally, and never in CI.
* **The Windows and macOS tiers ran on Linux**, so a line covered "by" them was
  covered against a fake, not a device.
* **Branch coverage is on**, so the `Partial` column is the interesting one for
  the well-covered modules: a guard with one side untested reads as 90% here and
  as 100% under line-only coverage.
