# Contributing to Voice Transcriber

Thanks for taking the time to help. This document is the practical guide;
[`AGENTS.md`](AGENTS.md) holds the deeper architectural invariants, and
[`docs/architecture.md`](docs/architecture.md) explains how the pieces fit
together. If a change and this file disagree, the file is wrong — please fix it
in the same pull request.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## The shape of the project

One shared core (`src/`) behind a Hardware/OS Abstraction Layer, with four hosts:

| Host | Notes |
| :--- | :--- |
| **Linux** (Wayland / X11) | `evdev` hotkeys + `uinput` synthetic input |
| **Windows** (native) | `pynput` hotkeys + Win32 `SendInput` |
| **Windows via WSL2** | The Linux engine, with a PowerShell bridge on the Windows host |
| **macOS** | `pynput` + `pbcopy`/AppleScript |

Everything cross-platform is shared. Only four things differ per OS, and they all
live in `src/voice_transcriber/platform/<os>/`:

- **hotkeys** — global push-to-talk
- **clipboard / typing** — text injection
- **audio cues** — the start/stop sounds
- **notifications** — desktop toasts

> **The HAL is the only place that branches on the OS.** `platform/__init__.py`
> detects; `hal.py` is the façade. Do not write `sys.platform` checks anywhere
> else.

## Getting set up

You need [Nix](https://nixos.org) with flakes enabled (recommended), or
Python 3.10+ and the platform dependencies.

```bash
git clone https://github.com/jjamesmartiin/voice-transcriber
cd voice-transcriber
./scripts/setup.sh          # venv + dependencies + the model weights (~2.8 GB, once)
./scripts/run.sh            # launch
```

The model is ~2.8 GB and downloads on first launch. It is not in the repo
(`models/` is gitignored), and neither is `config/config.yaml` — that file is
your **real, live user config**.

> **There is no `python` on the host PATH under the Nix workflow.** Use
> `nix develop --command python …`, or `nix run . -- …`.

## Running the tests

Tests are tiered. The shared tier is hermetic and model-free, so it runs
anywhere in seconds — that is the loop you want while developing.

```bash
./scripts/test.sh                                  # shared + this platform (auto-detects WSL)
./scripts/test.sh shared                           # shared only — fastest real signal
./scripts/test.sh e2e                              # needs the model + audio hardware, local only

# The same thing explicitly, which is what CI runs:
nix develop --command python -m pytest tests/shared tests/linux -q
```

| Tier | Directory | Needs the model? | Runs where |
| :--- | :--- | :--- | :--- |
| **Shared** | `tests/shared/` | No | Every OS, every push |
| **Platform** | `tests/linux/`, `tests/windows/`, `tests/wsl/`, `tests/macos/` | No | That OS only, every push |
| **End-to-end** | `tests/e2e/` | Yes | Local only, never CI |

CI runs the shared tier plus each platform tier on Linux, macOS, Windows and a
simulated WSL job, and builds the Rust frontend
(`.github/workflows/ci.yml`). Please make sure `./scripts/test.sh` is green before
opening a pull request.

### The Rust frontend

The ratatui TUI lives in `tui-rs/` and is built by the flake:

```bash
nix build .#vt-tui --no-link --print-out-paths
```

`doCheck = 1` means this also runs the crate's `cargo test`.

## Driving the app without faking anything

For anything beyond unit tests, drive the running engine through the
[Control API](docs/control_api.md) rather than simulating keystrokes:

```bash
python src/main.py start            # begin recording
python src/main.py stop
python src/main.py wait --json      # includes last_transcription
python src/main.py status --json
python src/main.py help --json      # the machine-readable verb catalogue
```

`help --json` is the source of truth for the verb surface — read it rather than
trusting a list in a document. `tests/shared/test_control.py` shows the pattern.

> This does not work on **native Windows**: stock CPython there has no
> `socket.AF_UNIX`, so the engine never binds the control socket. Use the
> terminal keys instead. Linux, WSL and macOS are unaffected.

## Style

- **Python** is PEP 8 with type hints where they help. `ruff` is configured in
  `pyproject.toml` and gated in CI:

  ```bash
  nix develop --command ruff check src tests tools eval
  ```

  `ruff format` is deliberately **not** enforced. It would rewrite most of this
  tree — the long lines here are mostly hand-aligned tables and message strings
  — which is a large diff with no behaviour change. Run it on a file you are
  already rewriting if you like; nothing will ask you to.

- **Rust** is plain `rustfmt`; `nix build .#vt-tui` runs the crate's `cargo test`.
- **Shell** is `bash` with `set -euo pipefail`, gated by `shellcheck`:

  ```bash
  nix develop --command shellcheck --severity=warning $(git ls-files '*.sh')
  ```
- Prefer reusing an existing setter in `t2.py` over assigning a global — the
  setters keep the post-processor in sync and persist config.
- Keep user-facing messages honest and specific. If a feature is unverified on a
  platform, say so in the docs and in `docs/TODO.md` rather than implying it works.

## Invariants worth knowing before you touch things

These are load-bearing and have been broken by accident before:

- **The settings modal is the only configuration entry point.** There is
  deliberately no global hotkey for it — a system-wide settings shortcut fires
  while the user is typing in another application. Terminal keys are exactly
  `Space`/`Enter` (record), `s`/`S`/`,` (settings), `r` (reset), `q`/`Esc`/`Ctrl+C`
  (quit). Do not add per-frontend aliases.
- **The engine is authoritative.** The ratatui frontend is a pure view + input
  device; it must not own state. Python owns hotkeys, audio, ASR and config.
- **Replies on the control API must never block the engine**, and a malformed
  request must never raise into the app.
- **Nothing may block the hotkey or UI threads on model loading.** Recording
  starts immediately; transcription waits on `_model_ready_event`.
- **Binds are user data, not code.** The key vocabulary and the per-platform code
  tables live in exactly one place (`src/voice_transcriber/keybinds.py`), so the
  backends cannot drift.

## Gotchas that have actually bitten people

- **Nix flakes only see git-tracked files.** A new file in `src/` that you leave
  untracked is *excluded* from `nix build` / `nix run` — which ships a build that
  fails at import. `git add` new source files before claiming a Nix build works.
- **`result/` is stale until rebuilt.** It is a symlink to the last `nix build`;
  the binary there can predate your change.
- **Never let a test or a manual command write `config/config.yaml`.** It is the
  maintainer's (and any user's) live settings. Tests must monkeypatch
  `t2.CONFIG_FILE`.
- **Don't `pkill -f "src/main.py"`** — the pattern also matches the shell running
  your command. Find the PID and kill that, and prefer `VT_CONTROL_SOCKET` to
  isolate a test instance from a running one.

## Commits and pull requests

Commit subjects follow [Conventional Commits](https://www.conventionalcommits.org):

```
feat(hotkeys): user-configurable push-to-talk binds
fix(windows): stop clipping the start of push-to-talk recordings
docs(readme): document the control API
```

Common scopes: `hotkeys`, `audio`, `tui`, `windows`, `wsl`, `linux`, `macos`,
`dictionary`, `post-processor`, `control`, `settings`, `model`, `stats`,
`doctor`, `release`, `docs`, `ci`.

A good pull request:

1. **Explains the problem**, not just the diff — what broke, or what was missing.
2. **Says how you verified it.** "`./scripts/test.sh` passes" is fine for a trivial
   change; a behaviour change should say what you actually ran or exercised.
3. **Be honest about what you could not test.** If you have no Windows machine,
   say so. An unverified claim is worse than a known gap.
4. **Updates the docs.** `README.md`, the relevant `platforms/<os>/README.md`,
   `tui-rs/README.md` and `docs/` all describe behaviour that tests may pin.
   Stale docs are treated as bugs here.
5. **Keeps the diff focused.** Unrelated cleanups belong in their own PR.

## Reporting bugs

Open an [issue](https://github.com/jjamesmartiin/voice-transcriber/issues). The
bug template asks for the things that actually determine the answer — platform,
how you installed it, and the output of the built-in diagnostic:

```bash
./scripts/run.sh doctor     # Linux / macOS
scripts\run.bat doctor      # Windows
```

`doctor` checks the microphone, permissions, hardware acceleration and the model
cache, and it is the fastest way for us to see what your machine thinks is
happening. Include its output if you can.

**Security problems do not go in the issue tracker** — see
[SECURITY.md](SECURITY.md).
