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

- **List formatting (`structure_mode`).** Spoken enumerations become real
  lists: "I can list them like: Thing one. Thing two. Thing three." now comes out
  as a bulleted list, and spoken "new line" / "new paragraph" / "bullet point"
  become the layout they name. Three modes — `off` (default), `inline` (markers
  only, never a newline) and `blocks` (real bullets, for pasting) — set from the
  settings modal in either frontend, the `structure` control verb, or the config
  file. Typed output is automatically downgraded from `blocks` to `inline`,
  because a newline is an Enter keypress in whatever window has focus.
- **Microphone pauses are no longer thrown away.** The micro-batcher cuts a chunk
  in silence (a real pause) or at an energy trough (a weaker one) and used to
  lose that boundary when stitching. The boundary now travels with the text as an
  in-band control character: in `blocks` mode a clean silence cut is a paragraph
  break and a forced cut is a line break, while `off` and `inline` flatten both to
  a space, so the default output is byte-identical to before.
- **`cleanup_mode`: three levels of cleaning.** `off` keeps every word (no
  hallucination stripping, no filler removal, no correction rewriting — verbatim),
  `artifacts` removes noise only (a `"Thank you."` hallucinated from a one-second
  clip, filler, stutters, trailing mutterings) while leaving anything you corrected
  verbatim, and `full` — the default, and what shipped before — also resolves
  self-corrections. Settable from the settings modal in either frontend, the
  `cleanup` control verb, or the config file. Number/date formatting, the
  dictionary, casing and the punctuation preset are separate settings and are not
  gated by it. See [`docs/cleanup_modes.md`](docs/cleanup_modes.md).

### Fixed

- **A clipboard tool can no longer hang a transcription.** The Linux sink ran
  `wl-copy` (or `xclip`) through `subprocess.run` with no timeout, and `wl-copy`
  keeps *serving the selection* instead of forking away on some setups
  (wl-clipboard 2.3.0 blocks indefinitely in a headless-ish Wayland session). The
  copy path now gives the tool a short leash and treats "still holding the
  selection" as a successful copy, because the payload has been handed over. The
  same unbounded call lived in the e2e suite's own sink, where it hung
  `pytest tests/e2e` on this machine.

- **Verbal self-corrections no longer corrupt the sentence.** The demo the README
  and blog advertise — "remind me tuesday no wait make that wednesday …" — came
  out as "remind make that wednesday …", losing "me tuesday" and leaving the
  marker "make that" in the text. Chained markers now resolve as one retraction,
  the words around a retracted value are preserved, and a bare "no" ("Tuesday,
  no, Wednesday") is supported but only when both values are the same kind of
  thing, so "please make that happen" and "there is no Wednesday meeting" are
  left alone.
- **Enumerations survive.** "Thing one. Thing two. Thing three." was rewritten to
  "Thing one. Thing two thing three." because the cardinals `two/three/four/five`
  were listed as dangling prepositions; "One. Two. Three." became "One. 23.".
- **A list cue keeps its capital.** The colon in "I can list them like: Thing
  one" no longer lowercases the first item.
- **Spoken quotation marks are rewrapped.** "he said quote hello unquote" becomes
  `he said "hello"`; a lone "quote" is never touched.

## [1.3.1] — 2026-10-08

### Changed

- **Repository layout / developer experience.** The five verb entry points moved
  out of the repo root into `scripts/`: `./scripts/setup.sh`, `run.sh`, `test.sh`,
  `build.sh`, `clean.sh`, plus the Windows `.bat`/`.ps1` equivalents (including
  `run_wsl.bat` / `setup_wsl.bat`). `TODO.md` moved to `docs/TODO.md` and
  `icon.ico` to `packaging/icon.ico`. The repo root now opens on the README
  instead of ~40 loose files.
- **Dependencies consolidated into `pyproject.toml`.** The root
  `requirements.txt`, `requirements-dev.txt` and `pytest.ini` were removed; the
  dependency list already lived in `pyproject.toml`. POSIX setups now install the
  project (`pip install -e ".[dev]"`), while Windows keeps its offline
  `platforms/windows/requirements*.txt` for the bundle build.

### Fixed

- **Hero demo GIF regenerated from the real UI.** The previous `vt-demo.gif` had
  drifted into a hand-drawn mock (badge chips, a fictitious title bar) that
  matched nothing in the app. It is now rendered from the actual ratatui layout in
  `tui-rs/src/ui.rs`, with a build-time assertion that no line is clipped.

## [1.3.0] — 2026-10-07

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
- **The control API now works on native Windows.** Windows 10+ supports `AF_UNIX`,
  but stock CPython never exposes `socket.AF_UNIX` (bpo-33408), so the engine now falls
  back to a loopback TCP transport: `127.0.0.1` only, an OS-assigned port, a per-run token
  required on every request, and host/port/token published to a `0600` per-user endpoint
  file. The verbs and reply shapes are unchanged. The ratatui frontend remains Unix-only,
  so Windows keeps the Rich TUI.
- **Pluggable backend registry.** The `model_backend` config option is now read,
  validated against a single backend registry, persisted, and reported clearly if you name a
  backend that does not exist. `transcribe2.set_backend()` now properly switches backends
  with zero overhead on startup.
- **Launch visual assets & interactive showcase.** Added a hero demo GIF, terminal-UI and
  settings renders, a system-architecture diagram, and a 1280x640 social card.
  The README now showcases a 30-second quickstart, quantitative benchmarks, and formatting mode presets.

### Fixed

- **The Rich TUI crashed on startup.** `_sync_tui_state` always passed `hotkeys=` to the
  frontend, but only the ratatui TUI accepted it — so every launch that used the Rich TUI
  died with `TypeError: unexpected keyword argument 'hotkeys'` before the model began
  loading. That is the default frontend on native Windows, and it is also the no-TTY
  fallback. Found by running a built AppImage, not by a test: every CI job that reaches the
  call site picks the ratatui frontend. There is now a test asserting the two frontends
  stay interchangeable, plus one that runs the real call site against the Rich TUI.
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
- **One verb = one script:** `./scripts/setup.sh`, `./scripts/run.sh`, `./scripts/test.sh`, `./scripts/build.sh`
  and `./scripts/clean.sh` (plus Windows `.bat`/`.ps1` equivalents) dispatch to a single
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

[Unreleased]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.3.1...HEAD
[1.3.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.3...v1.1.0
[1.0.3]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/jjamesmartiin/voice-transcriber/releases/tag/v1.0.1
