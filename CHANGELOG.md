# Changelog

All notable changes to Voice Transcriber are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are grouped as Added / Changed / Fixed / Security.

The model weights are versioned **separately** from the app, on a
revision-derived tag (`model-cohere-<rev>`). They are republished only when the
underlying model revision changes — never per app release — so a new app version
normally reuses the existing bundle. See
[`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

### Added

- **User-configurable push-to-talk binds.** The record trigger is no longer
  hardcoded to `Alt+Shift`: bind any number of chords from one OS-neutral,
  single-source-of-truth key table, across Linux, native Windows, WSL and macOS.
  Manage them with the new `hotkey` control verb
  (`hotkey add ctrl+shift`, `hotkey remove alt+shift`, `hotkey keys`, `hotkey
  reset`) or the new **Settings → Push-to-Talk Keys** picker in the ratatui
  frontend. A backend that cannot honour a change reports so rather than
  silently keeping the old chord.
- **Standalone Windows release.** `VoiceTranscriber-windows-x86_64.zip` is
  attached to releases — a self-contained bundle needing no Python install. It
  prompts to download the model on first launch, or reads a local
  `models\cohere\` folder.
- **`model_backend` actually works.** The config option is now read (it was never read),
  validated against a single backend registry, persisted, and reported clearly if you name a
  backend that does not exist. `transcribe2.set_backend()` was a no-op that silently kept
  Cohere. Cohere remains the only backend; adding another is one registry entry plus a
  module. a hero demo, terminal-UI and
  settings renders, a system-architecture diagram, and a 1280x640 social card.
  The README now shows the terminal UI and the architecture diagram.

### Fixed

- **macOS: `doctor` crashed when checking Accessibility permissions.**
  `check_hotkeys_and_permissions` did `from platform.macos.hotkeys import ...`,
  an absolute import that resolves to the *stdlib* `platform` module ("'platform'
  is not a package"). It is now loaded through the HAL, like every other backend
  access. Linux never hit it because the branch is `darwin`-only.
- **Three settings in the config file were silently ignored.** `language`,
  `wait_for_model_on_startup` and `enable_slm` are module globals, but the
  config loader assigned to them without declaring them `global`, so it wrote to
  throwaway locals. `language: fr` did nothing, and saving the config then wrote
  `language: en` back over the file.
- **`reset_terminal()` raised `UnboundLocalError` on every call** and was
  swallowed by its enclosing `except`, so the terminal/clipboard reset did
  nothing. It used `sys.platform` before a later `import sys` made `sys` local.
- **The TUI's output-mode cycle invented modes the engine does not have**
  (`paste`, `paste_terminal`), showing a setting that did not exist and then
  snapping back.
- **A transcription could be lost while a settings/mic/theme modal was open** in
  the ratatui frontend, and the block counter could advance for a block that was
  never shown. Modals now buffer pending output.
- **The notification overlay could break or be injected into.** It built a
  Python script by string interpolation and ran it with `python -c`; a `"` in
  the text broke it. Values are now emitted as `repr()` literals. Its list of
  child processes was also shared between threads without a lock.
- `AGENTS.md` claimed a specific verb count for the control API while the same
  paragraph said not to hardcode it. The count is gone.

### Changed

- **The platform table now says what is actually proven.** Linux is marked
  *Verified* (run end-to-end on real hardware, daily); Windows, WSL2 and macOS are
  marked *Supported, unit-tested*. A short "what verified means here" section
  states per platform what is and is not exercised — including that the WSL
  PowerShell bridge has never executed, because CI has no `powershell.exe` — and
  the same note sits at the top of the Windows, WSL and macOS guides. It also says
  what to send in an issue, so a first-run failure is treated as a bug in the claim
  rather than as user error.
- **A linter now runs in CI.** `ruff` (configured in `pyproject.toml`) and
  `shellcheck` gate the Linux job and ship in the dev shell, and the dev-shell
  banner moved to `stderr` so `nix develop --command ... --json` produces clean
  output. `ruff format` is deliberately not enforced.
- Internal planning documents moved to `docs/archive/` (including the launch
  and readiness plan, which is a record of how this was validated rather than a
  user-facing document).
- CI now builds the Rust frontend (`nix build .#vt-tui`) in the Linux job. The
  crate is Unix-only and nothing else compiled it, so a broken frontend build or
  a failed `vu` parse previously stayed invisible until release.
- Documentation: added `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  this changelog, issue/PR templates, and a companion blog post
  (`docs/blog_post.md`).

## [1.2.1] — 2026-10-05

Platform unification, package namespacing, and audio resilience.

### Added

- **Reset Microphones** — recover a microphone that was plugged in later or was
  held by another application at startup, without restarting the app
  (settings modal, or the `rescan-mics` control verb).
- **Muted-microphone guard** — a muted PipeWire source opens cleanly, reports
  sane channels and then records pure silence (the *"no audio input detected"*
  symptom). `doctor` now detects it, `doctor --fix` repairs it, and the app warns
  on startup.
- **Live multi-microphone VU metering** — the mic picker meters every visible
  device at once, so you can see which one is actually hearing you.
- **Multi-tier personal dictionary** — a shipped `config/dictionary.yaml` plus a
  gitignored `config/dictionary.local.yaml`, with hot reloading.
- **Time-saved statistics** — persistent all-time totals, typing-WPM estimation
  and TUI badges.
- **Interactive preset switcher** — a dedicated modal for dictation styles.
- **WPM configuration** in the settings modal.

### Changed

- **Package namespacing:** the codebase now lives under
  `src/voice_transcriber/`, which eliminates the stdlib `platform` shadowing for
  good; the previous flat module paths remain as backward-compatible shims.
- **One verb = one script:** `./setup.sh`, `./run.sh`, `./test.sh`, `./build.sh`
  and `./clean.sh` (plus Windows `.bat`/`.ps1` equivalents) dispatch to a single
  shared runner, `tools/vt_dev.py`.
- Live kernel modifier resolution on Linux, and spoken-date formatting.

### Fixed

- Bluetooth headsets: the WirePlumber hands-free autoswitch setting is now
  snapshotted and restored on exit instead of being permanently edited.
- Spoken dates now format as ordinals (`"October twentieth"` → `"October 20th"`).

### Security

- Hardened IPC socket and log-file permissions, sanitized PowerShell bridge
  invocation, and removed a blind `pkill` from the tooling.

## [1.2.0] — 2026-09-28

### Added

- **Out-of-process Control API** — drive the engine over a UNIX socket
  (`start`, `stop`, `toggle`, `wait`, `status`, `mics`, `set-mic`, settings
  verbs), with `main.py` itself as the client. Settings deliberately remain
  terminal-only.
- **Cross-platform mouse mode** — middle-click push-to-talk, and (Linux)
  left+right → `Enter` plus tap-through middle click.
- **Post-processor:** three number modes (`auto`/`digits`/`words`), serial
  numbers / NATO / spell-command formatting, and zero synonyms.
- **Context-aware homophone disambiguation** with triggers and guards.
- **Dictionary CLI** (`src/dictionary.py`) for add/remove/list/test/path.
- **Reset to Defaults**, and preset-style previews in the settings modal.
- Per-platform requirements docs, a Control API reference, and an agent guide.

### Changed

- The settings modal is now the single configuration entry point.
- Mouse push-to-talk is off by default.

### Fixed

- Windows: execution-policy and `MAX_PATH` errors in the setup scripts.
- Windows CRLF double-enter in keystroke injection.

## [1.1.1] — 2026-09-26

### Fixed

- Reduced post-release latency by ~320 ms (fast tokenizer decode, responsive mic
  stop, FPU flush).
- Added a model-asset download fallback.

## [1.1.0] — 2026-09-26

### Added

- Windows, WSL and Linux parity, with a 1:1 TUI across platforms.
- Interactive microphone picker with a live VU meter, plus settings and theme
  pickers.
- Tiered tests (`shared` / platform / `e2e`) and a cross-platform CI matrix.
- Fast auto-type mode via Win32 `SendInput`.

### Changed

- Removed the Hugging Face token requirement from the runtime.

### Fixed

- Windows: recording startup delay, a recording crash, clipped push-to-talk
  starts, and unreliable auto-typing.

## [1.0.3] — 2026-09-09

### Added

- Repo-first model installation with `SOURCE.json` provenance.

## [1.0.2] — 2026-09-08

### Added

- Secret-free release workflow; the model is fetched from GitHub release assets
  so no Hugging Face account is needed.
- Non-blocking model load — recording starts immediately.

## [1.0.1] — 2026-09-08

First tagged release: offline Cohere ASR, global `Alt+Shift` push-to-talk with a
hands-free latch, streaming VAD, the English post-processor, and text injection
on Linux.

[Unreleased]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.2.1...HEAD
[1.2.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.3...v1.1.0
[1.0.3]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/jjamesmartiin/voice-transcriber/releases/tag/v1.0.1
