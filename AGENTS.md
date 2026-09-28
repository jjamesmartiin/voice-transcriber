# AGENTS.md

Guidance for coding agents and LLM tools working in this repository.

## What this is

A voice-dictation engine: global push-to-talk hotkeys, streaming VAD, a Cohere
ASR backend, an English post-processor, and real-time text injection. One shared
core (`src/`) behind a Hardware/OS Abstraction Layer, three hosts: **Linux**,
**native Windows**, and **Windows via WSL2/NixOS**.

`README.md` is the user-facing guide. `docs/architecture.md` covers internals.
`docs/control_api.md` is the programmatic interface — read it before scripting
the app. `docs/releasing.md` is the release runbook — read it before cutting a
release or touching `.github/workflows/release.yml`.

## Driving the app (do this instead of faking hotkeys)

Start recording, change settings and read results back through the control API:

```bash
python src/main.py status --json     # never needs a running instance
python src/main.py help --json       # machine-readable verb catalogue
python src/main.py start             # with an instance running
python src/main.py stop
python src/main.py wait --json       # includes last_transcription
```

`help --json` is the source of truth for the verb surface (24 verbs, with
`choices` / `required` / `toggles` per verb). It is served locally from
`src/control.py`, so it works with no engine running. Do not hardcode verb lists
from docs — read the catalogue.

Use this for tests too. `tests/shared/test_control.py` shows the pattern.

## Running tests

Tiers: `tests/shared/` (all platforms, model-free), `tests/{linux,windows,wsl}/`
(one platform each), `tests/e2e/` (needs model weights + audio, local only).

```bash
./test.sh                      # shared + this platform (auto-detects WSL)
./test.sh shared
./test.sh e2e
nix develop --command env VT_PLATFORM=linux python -m pytest tests/shared tests/linux tests/wsl tests/windows -q
nix develop --command python -m pytest tests/shared/test_control.py -q   # fastest loop
```

There is no `python` on the host PATH — use `nix develop --command python`.

The Rust frontend builds and is checked with:

```bash
nix build .#vt-tui --no-link --print-out-paths
```

## Gotchas that have actually bitten

- **CI does not publish the model weights.** `release.yml` attaches only
  `./*.AppImage`; the ~2.8 GB of Cohere weights are uploaded by hand from
  `dist/model/` after the release object exists. A release without them is not
  visibly broken — the client 404s on `releases/latest/download` and silently
  falls back to the hardcoded `FALLBACK_RELEASE_BASE` tag in
  `src/model_download.py`. See `docs/releasing.md`.

- **Nix flakes only see git-tracked files.** A new file in `src/` that is left
  untracked is **excluded** from `nix build` / `nix run` even though the working
  tree has it. That ships a build where `main.py` imports a module that does not
  exist (`ModuleNotFoundError`). Always `git add` new source files before
  building or claiming a Nix build works.
- **`result/` is stale until rebuilt.** It is a symlink to the last `nix build`;
  a binary there can predate the current source. Rebuild, don't trust it.
- **`models/` and `dist/` are gitignored.** No weights in a fresh clone; they are
  auto-downloaded on first launch (~2.8 GB down, ~4 GB on disk). No prebuilt
  Windows EXE is published — PyInstaller cannot cross-compile.
- **`config/config.yaml` is a real user config** (untracked). Tests must
  monkeypatch `t2.CONFIG_FILE`; never let a test or a manual command write the
  user's live settings. If you drive the app by hand, restore what you changed.
- **Don't `pkill -f "src/main.py"`.** The pattern also matches the shell running
  your command. Use `ps -ef | grep -F` and kill by PID.
- The user may have their own instance running. Check `ps -ef | grep -F main.py`
  before launching another engine, and prefer `VT_CONTROL_SOCKET` to isolate.

## Invariants to preserve

- **The settings modal is the only configuration entry point.** There is
  deliberately **no global hotkey** for it (a system-wide settings shortcut fires
  while the user types elsewhere). Terminal keys are exactly: `Space`/`Enter`
  record, `s`/`S`/`,` settings, `r` reset, `q`/`Esc`/`Ctrl+C` quit. `i` and `M`
  were removed on purpose — do not re-add per-frontend aliases.
- **Global hotkeys are only** `Alt+Shift` (push-to-talk), `Space` while holding
  (hands-free latch), and the middle mouse button. The latch semantics live in
  `BaseHotkeyManager.latch_release`; the WSL host bridge mirrors them via
  `LATCH_DOWN`/`LATCH_HOLD` and must withhold `HOTKEY_UP` while latched.
- **The HAL is the only place that branches on OS.** `src/platform/__init__.py`
  detects; `src/hal.py` is the façade. Never branch on `sys.platform` elsewhere.
  An unknown `VT_PLATFORM` is a hard error by design.
- **`platform` shadows the stdlib module.** `src/platform/__init__.py` has a
  shadow guard; import the façade (`hal`), not the package directly.
- **The engine is authoritative.** The ratatui frontend is a pure view + input
  device; it must not own state. Python owns hotkeys, audio, ASR, config.
- **Replies to the control API must never block the engine** and a malformed
  request must never raise into the app — `ControlServer` guarantees this.
- **Nothing may block the hotkey or UI threads** on model loading; recording
  starts immediately and transcription waits on `_model_ready_event`.

## Conventions

- `src/` has no `__init__.py` (namespace package); tests add `src/` to
  `sys.path` via `tests/conftest.py`.
- Prefer reusing the existing setters in `t2.py` (they keep the post-processor
  in sync and persist config) over assigning globals directly.
- Docs are part of the change: `README.md`, the relevant
  `platforms/<os>/README.md`, `tui-rs/README.md` and `docs/` all describe
  behaviour that tests may pin. Stale docs are treated as bugs here.
- The WSL PowerShell bridge cannot run in CI (no `powershell.exe`); pin changes
  to it structurally, as `tests/wsl/*.py` do, and note untested host behaviour in
  `TODO.md`.
