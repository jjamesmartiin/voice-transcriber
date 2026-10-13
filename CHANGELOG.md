# Changelog

All notable changes to Voice Transcriber are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are grouped as Added / Changed / Fixed / Security.

The model weights are versioned **separately** from the app, on a
model-revision-derived tag (`model-<name>-<rev>` for `cohere`, `formatter` and
`diarization`). They are republished only when the underlying model revision
changes — never per app release — so a new app version normally reuses the
existing bundle. See [`docs/releasing.md`](docs/releasing.md).

## [Unreleased]

## [1.4.3] — 2026-10-12

### Fixed

- **A hotkey rebind from the control API (or the settings modal) no longer reaches
  nothing on a running engine.** `t2.set_hotkey_binds` — and the middle-click and
  sound-theme setters — propagate through the `hotkeys` shim's registered
  manager, but only `hotkeys.create_global_hotkeys` ever registered one. The
  engine builds its manager straight from the HAL, so the registration stayed
  empty and every one of those setters silently did nothing on that path.
  `hotkeys.register_manager()` / `release_manager()` are now the one named home
  for that, called by `init_hotkeys()` and teardown, and the release is
  identity-scoped — an older manager's teardown can no longer clear the live
  one's registration (which is how restarting hotkeys disabled propagation for
  the rest of the session).
- **Turning mouse mode off now stops monitoring the mice, not just the grab.**
  The pointing devices stayed in the watch list, so their events kept flowing
  through the mouse path with the feature disabled.
- **A replayed middle click no longer stalls every other device for 25 ms.** The
  gap between the press and release halves of a replayed click — some toolkits
  ignore a click whose halves land in the same millisecond — was a
  `time.sleep()` on the reader thread, which is the thread that reads every other
  device *and* detects `Alt+Shift` and middle-click. Each replayed click delayed
  all of them. The release half is now deferred to a deadline the event loop
  already wakes for, on the same thread, so forwards stay ordered (a press that
  would overtake a pending release flushes it first) and nothing blocks.
- **The left+right chord's swallow is now tracked per device.** One pointer's
  button release cleared a flag another pointer was still relying on.

## [1.4.2] — 2026-10-12

### Added

- **Windows gets a single-file installer.** Every release now attaches
  `VoiceTranscriber-windows-x86_64-setup.exe` next to the portable zip: one file
  to download and run, installing per-user into `%LOCALAPPDATA%\VoiceTranscriber`
  with a Start Menu shortcut, no UAC prompt and no admin rights. It wraps the same
  PyInstaller bundle the zip carries, so the two behave identically once running.
  Uninstalling removes the program only — the downloaded weights (~4 GB) and your
  settings under `%APPDATA%\vt` are left alone, as is a `models\` directory you
  placed in the install folder by hand. It is unsigned, so SmartScreen shows
  "unknown publisher" until *More info → Run anyway*.
- **Every app release states what it needs.** A generated `REQUIREMENTS.txt`
  names the exact bundles that build resolves — each model bundle's tag, base URL,
  `SHA256SUMS` manifest, revision and per-file digest, plus the pinned
  `llama-server` — so mirroring a release or installing it airgapped no longer
  means reading the source to find out which tags to fetch. It is rendered from
  the same declarations the client fetches with, so it cannot disagree with them.

### Changed

- **The releases page now carries one row per app version plus the bundle tags.**
  App releases are the disposable ones and the pre-releases hosting weights and
  the formatter backend are not: a bundle tag *is* the runtime download URL for
  every shipped app, so deleting one breaks first-run installation on installs
  already out there. Superseded app releases (`v1.2.1`, `v1.3.0`, `v1.3.1`) were
  pruned under that rule, with their git tags kept so this file's compare links
  still resolve. `docs/releasing.md` records the rule and the digests of what was
  removed.
- **The Windows release job fails loudly if the installer cannot be built**,
  rather than publishing a release whose primary Windows download is missing.

### Fixed

- **The `[1.4.1]` link in this file was never defined**, so that section rendered
  as a broken reference.
- **A stale `docs/releasing.md` claim about `v1.1.0` is corrected.** It stated
  that the `v1.1.0` weight assets were still published and must not be deleted;
  `v1.1.0`, `v1.1.1` and `v1.2.0` have in fact been gone for some time, so any
  surviving install of those versions can no longer fetch weights and has to be
  pointed at `model-cohere-499888924f5f` or given the files via `VT_MODEL_DIR`.

## [1.4.1] — 2026-10-11

### Added

- **The formatter backend can be fetched on demand instead of dead-ending.** With
  the formatter enabled and no `llama-server` present, the app previously had
  nothing to offer. The first-use prompt and the `formatter-download` verb now
  install a pinned, SHA-256-verified build from our own pre-release
  (`tool-llama-server-b11429`) into the app's data directory, stating the size
  (18.5 MiB) before anything downloads. `VT_FORMATTER_BINARY` remains the
  bring-your-own escape hatch and is still searched first; `PATH` is still last.
  A build is accepted only as a *set* — the executable plus the DLLs it imports —
  because the upstream Windows `llama-server.exe` is a ~9 KB stub that resolves
  without its siblings and then fails to start. `doctor` reports what resolved,
  from where, and which members are missing; so does the error message.
  This fetch is **Windows x64 only**, matching the pinned artifact: on Linux and
  macOS nothing is downloaded, and the message and `doctor` point at the package
  manager (`pkgs.llama-cpp`) or `VT_FORMATTER_BINARY` instead.
- **A meeting capture can be given an opt-in ceiling, `VT_MEETING_MAX_SECONDS`**
  (seconds, ≥60). Unset is unchanged: no timer, no log line. When set, a
  control-API-started meeting is stopped at the deadline with the reason logged,
  cancelled by a normal `meeting-stop`, and generation-guarded so a timer from one
  meeting can never stop the next.
- **`speakers` and `hotkey` values are capped at 4096 characters** by the control
  API, rejected with the limit in the message rather than truncated, so a same-user
  client cannot make the engine store or parse a ~1 MiB string.

### Fixed

- **Config and dictionary writes are atomic, durable, backed up and owner-only.**
  Both writers use a same-directory `mkstemp` temp (0600, pid+random), `fsync`
  before `os.replace`, and create their directories 0700 — so a crash or a second
  engine instance can no longer truncate the user's config or race the old fixed
  `config.yaml.tmp`. An unparseable dictionary is preserved as `.corrupt-*` and
  reported instead of making every later CLI `add`/`remove` raise, and a zero-byte
  config is treated as damaged rather than silently overwritten with defaults.
- **The Windows build no longer takes `llama-server.exe` from `PATH`.** The
  artifact was decided by the build machine's environment — and on Windows an
  empty or `.` `PATH` entry means the current directory — so it is now resolved
  only from `VT_LLAMA_SERVER_ASSET` or a fixed path, with `VT_LLAMA_SERVER_SHA256`
  verified when supplied, a hard failure on a set-but-unusable override, and a
  loud warning instead of a silent skip when nothing is pinned.
- **The eval scorers redact machine paths in the generator**, rather than the
  committed JSON/HTML being hand-edited, and `_safe_relpath`'s fallback is a
  placeholder instead of the absolute path it used to return — which was exactly
  the Windows cross-drive case where the redaction silently failed.
- **The first dictation after a cold start is no longer silently unformatted.**
  The elapsed-time backstop in `formatter.format_text` counted the backend's
  startup — process spawn and weights load, seconds for a 462 MiB GGUF — against
  the 3 s request budget, so it threw away a correct rewrite and returned the
  original text. It now warms the backend *outside* that clock and re-checks
  availability first, so a warm that failed cannot cost a second full spawn
  attempt. Pinned by
  `tests/shared/test_formatter.py::test_guardrail_6_a_cold_backend_is_not_charged_for_its_own_startup`,
  which failed against the previous code.
- **The formatter no longer calls a healthy server dead when the response body
  arrives in its own TCP segment** — the reader stopped at the end of the
  headers, so `/health` looked unhealthy and the formatter silently never came
  up. `timeout_s` is also a whole-request budget now instead of an idle timeout
  (a peer that kept sending reset it forever), responses are capped at 4 MiB, and
  a chunked response says so instead of surfacing as a JSON parse error.
- **IPv6 works in attach mode.** `http://[::1]:port` was unreachable — the
  transport was IPv4-only and the `Host:` header unbracketed. An IP literal is
  still dialled without a resolver; a *name* now resolves across both families
  instead of IPv4 only.

### Security

> A full audit of the 2026-10-10/11 security work — what was verified, what was
> pre-existing that it missed, and what is deliberately left open — is in
> [`docs/audit-2026-10-11.md`](docs/audit-2026-10-11.md).

- **The optional LLM polish pass can no longer POST your transcript to another
  host by default.** `VT_VLLM_URL` was used as-is, so a copied config or a typo
  aimed at a remote box sent the dictation there over the network — in the clear
  for `http://`. It now refuses anything that is not `http(s)://` on this machine
  unless `VT_VLLM_ALLOW_REMOTE=1` is set, decided without a resolver, and *before*
  any connection (the `/v1/models` discovery probe included); the refusal names the
  flag and the pipeline still falls back to the ASR text. Loopback needs no
  configuration. A remote `https://` endpoint is allowed, because `urllib` speaks
  TLS — unlike the formatter's hand-rolled transport, which stays `http://` only.
  Pinned by `tests/shared/test_slm_loopback.py`.
- **The optional LLM polish pass can no longer hand your transcript to a proxy.**
  `urllib.request` reads `http_proxy` / `HTTPS_PROXY` / macOS `_scproxy`, none of
  which can tell `localhost:8000` from the open internet, so on a machine with a
  proxy configured the dictation was forwarded to it — while the app went on
  reporting that it talks only to `127.0.0.1`. It now uses a proxy-free opener.
  Pinned by `tests/shared/test_slm_loopback.py`, which stands up both a local
  endpoint and a proxy and asserts only the endpoint is used. Its model-name
  discovery also queried a hardcoded port 8000, so it silently ignored
  `VT_VLLM_URL`; it now derives the models URL from it.
- **Attaching to an `https://` formatter URL is refused rather than silently
  posted in cleartext to port 80** — there is no TLS in this transport, and
  `urlparse` leaves the port unset for `https`. `available()` reports such a URL
  as unavailable too.
- **The formatter now talks to loopback and nothing else.** A
  `VT_FORMATTER_SERVER_URL` naming another machine is refused unless
  `VT_FORMATTER_ALLOW_REMOTE=1` is set, so a copied config or a typo cannot
  stream your transcripts to another host in the clear. Turning it on is plain
  `http://`, so tunnel it.
- **A local process that takes the formatter's port is caught, *if* it takes it
  exclusively.** The port is now picked at random instead of sequentially, and a
  healthy answer is not trusted until the child has had time to attempt its own
  bind and is still running — which catches a squatter whose bind made
  `llama-server` exit. It does **not** catch one that sets `SO_REUSEPORT` and
  shares the port: cpp-httplib sets that option itself, so the child's bind
  succeeds and the child stays alive. This is a local trust boundary rather than
  authentication, and a process on the same machine can intercept the
  transcript; [`docs/TODO.md`](docs/TODO.md) records the verified extent of it
  and the AF_UNIX fix.
- **The ASR model loader no longer searches the launch directory.** `load_model`
  accepted `<cwd>/models/cohere` as a local snapshot on the strength of a
  `model.safetensors` alone and then loaded it with `trust_remote_code=True`, so a
  `config.json` plus a `modeling_*.py` dropped next to wherever the app was
  started from executed as the user. The search is anchored to the frozen bundle,
  the checkout and the per-user data dir now; `transcribe_cohere._candidate_model_dirs()`
  is pinned by `tests/shared/test_audit_regressions.py`.
- **The Cohere `config.json` and processor configs are in the digest map.** The
  four `.py` modules were pinned but the JSON that *names* them was not, and
  `config.json` carries the `auto_map` that `trust_remote_code=True` resolves — a
  rewritten `config.json` could point at any module without changing a pinned
  hash. All three are now pinned, verified byte-identical to the HF snapshot the
  pinned revision names.
- **Model-bundle tar extraction refuses symlink and hardlink members.** The
  no-`data_filter` fallback (Python 3.10.0–3.11.3) validated only `member.name`,
  so a symlink member pointing outside the destination followed by a regular
  member written through it escaped the guard entirely (CVE-2007-4559). Only
  plain files and directories are extracted now; the same check covers both the
  local-bundle and the split-part installers.
- **The dictionary loader no longer reads dictionary files from the working
  directory.** `post_processor.load_merged_dictionaries` began its candidate
  lists with CWD-relative paths, so a `config/dictionary.yaml` in the directory
  the app was launched from silently overrode the user's substitutions — and, in a
  checkout, shadowed the file the dictionary CLI writes. Every candidate is
  anchored to the checkout root or the per-user data dir, matching what
  `t2.get_config_file` already did.
- **The control API's TCP endpoint file is written by rename, not truncation.**
  It lives in the shared temp dir, so `os.open(..., O_CREAT | O_TRUNC)` followed
  whatever a local attacker had put at that name: a symlink meant an arbitrary
  victim file was truncated and overwritten, and a pre-created world-readable
  file exposed the 256-bit token until the `chmod`. A private `mkstemp` plus
  `os.replace` swaps the name instead. The reader also rejects a JSON `true` port
  and a non-string host.

## [1.4.0] — 2026-10-11

### Added

- **The on-device formatter's first-use prompt states the model size before it
  downloads.** Enabling the formatter (`formatter: on`) and dictating once is
  wired to fetch the ~462 MiB S1-mini Q4 GGUF through the same verified split-xz
  path the ASR uses, on a background thread, so dictation is never blocked. The
  prompt names the model exactly as its licence requires - **"S1-mini" by
  "Superwhisper"** (Apache-2.0 plus an additional naming term; the full text and
  provenance are in `config/licenses/`). Both the formatter (`model-formatter-34add00a48a2`)
  and meeting-mode diarization (`model-diarization-24615ee884c8`, with the VoxCeleb
  English ERes2Net embedding) are published as verified GitHub Release bundles.
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
- **Meeting mode: who spoke, and where the transcript goes.** Meeting capture
  (off by default) is a long, non-injecting recording transcribed in the
  background when you stop it. Three diarization settings label the speakers
  (`diarization: on`; `off` never loads the model and transcribes as one
  speaker), hint the expected count (`diarization_speakers: auto` or `2`–`8`),
  and choose the model bundle (`diarization_model`). All three are on the settings
  modal in both Python frontends, the control API and the config file. The
  finished transcript is written to a repo-local, gitignored `<repo>/meetings/`
  by default (`meeting_output_dir`; `VT_MEETING_OUTPUT_DIR` wins) as plain text,
  one JSON object per turn, or a Markdown document (`meeting_output_format`).
  Speaker names are an ordered, editable list on the meeting screen — per-meeting
  transcript metadata rather than app configuration — and the same map is
  scriptable through the `speakers` control verb.

### Changed

- **sherpa-onnx is pinned to ≥ 1.13.3 so Cohere Transcribe's ONNX path can load.**
  The devshell shipped 1.12.25, which has no Cohere support at all. A second,
  narrow nixpkgs input supplies only `sherpa-onnx` 1.13.3 (the newest build against
  the locked glibc 2.42), so a full nixpkgs bump — which would move `torch` — is
  not needed: `torch` stays 2.10.0. This makes the toolchain *capable* of the int8
  ONNX path; the ASR backend that would use it is **not** implemented. The
  override also takes `sherpa-onnx`'s own bundled `onnxruntime` from that input
  (1.26.0, replacing the 1.24.4 the older `sherpa-onnx` used) — one library, one
  owner, shared by the ONNX path and diarization, so the two cannot diverge.
- **Parakeet TDT 0.6B v3 is selectable, but Cohere stays the default.** The
  bake-off is recorded in [`docs/asr-bakeoff.md`](docs/asr-bakeoff.md): Parakeet is
  ~5× faster and ~3× smaller in memory, but loses every accuracy slice (2.65 % vs
  4.93 % whole-clip WER; 3.35 % vs 9.52 % through the shipped pipeline). Set
  `model_backend: parakeet` in the config file to try it.

### Fixed

- **"Reset to Defaults" now takes effect immediately.** It re-published only some
  of the settings the post-processor keeps its own copy of, so the punctuation
  preset kept the old value until the next launch even though the config file had
  already been written. Reset now re-publishes every runtime copy (punctuation,
  serial, spell, number, structure, cleanup, formatter) in the same order a config
  load does.
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

[Unreleased]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.4.3...HEAD
[1.4.3]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.4.2...v1.4.3
[1.4.2]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.4.1...v1.4.2
[1.4.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.3.1...v1.4.0
[1.3.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.3.0...v1.3.1
[1.3.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.2.1...v1.3.0
[1.2.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.2.0...v1.2.1
[1.2.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.3...v1.1.0
[1.0.3]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/jjamesmartiin/voice-transcriber/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/jjamesmartiin/voice-transcriber/releases/tag/v1.0.1
