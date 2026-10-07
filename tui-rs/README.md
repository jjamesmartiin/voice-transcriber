# vt-tui — ratatui frontend

A [ratatui](https://ratatui.rs) frontend for Voice Transcriber, wired to the
Python engine over a Unix socket. It renders the same "Style 1" inline REPL
layout as the original Rich TUI in [`../src/tui.py`](../src/tui.py):

- a live status/prompt line pinned at the bottom (ratatui inline viewport),
- transcription and event blocks pushed **above** it into terminal scrollback,
- no left/right borders, so text copies 100% cleanly in a terminal or tmux.

The Python engine stays authoritative (hotkeys, audio, ASR, clipboard, config);
`vt-tui` is a pure view + input device.

## How it runs under `nix run`

```
nix run  ->  $out/bin/vt  ->  python src/main.py
                                   |
                                   |  spawns with the real terminal (fd 0/1/2)
                                   v
                            vt-tui --connect <socket>
```

1. `src/main.py` builds a frontend via `create_tui()` (in `main.py`).
2. `src/tui_ratatui.py` (`RatatuiTui`) opens a Unix socket, spawns
   `vt-tui --connect <socket>` with the terminal fds, and forwards engine state
   as newline-delimited JSON.
3. Keypresses in the TUI come back as `{"t":"cmd",...}` messages and are
   dispatched to the exact same callbacks `main.SimpleVoiceTranscriber` already
   wires for the Rich TUI.

`nix run` builds the frontend through the flake: `flake.nix` exposes it as
`packages.<system>.vt-tui` (`nix build .#vt-tui`), and the `vt` wrapper depends
on it and exports `VT_TUI_BIN` to the built binary. So your alias just works,
with no separate build step:

```bash
alias vt='cd ~/gitprojects/voice-transcriber && nix run'
```

For frontend development, iterate with a local `cargo` build and point the app
at it instead of the flake build:

```bash
nix-shell -p cargo rustc --run 'cd tui-rs && cargo build --release'
VT_TUI_BIN=$PWD/tui-rs/target/release/vt-tui nix run
```

Override with `VT_TUI_BIN=/path/to/vt-tui`, or force the old Rich frontend with
`VT_TUI=rich`. If the binary is missing, the app falls back to Rich
automatically.

## Keys

| Key | Action |
| :-- | :-- |
| `Space` / `Enter` | Start / stop recording (tap again to stop — hands-free) |
| `s`, `S`, `,` | Settings & configuration modal — the only configuration entry point |
| `r` | Reset terminal & clipboard bridge |
| `q`, `Esc`, `Ctrl+C` | Quit |

Every setting is changed from inside the settings modal rather than from
one-key shortcuts on the main view. The modal also hosts the theme and
microphone sub-pickers, and a **Reset to Defaults** action that restores all
shipped defaults (press Enter twice to confirm). Your microphone choice and the
custom dictionary are never touched by a reset.

The microphone picker meters **every visible row at once** — one capture stream
per device — so you can see which mic is actually hearing you instead of
selecting one and hoping. The monitored set follows the list, so filtering
narrows what gets opened:

| Key | Action |
| :-- | :-- |
| `↑` / `↓` | Move the highlight |
| `Enter` / `Space` | Select the highlighted device |
| *type* | Fuzzy-filter the list (also narrows what is metered) |
| `Esc` | Cancel |

On WSL, WSLg's PulseAudio server normally exposes a single real capture source
(`RDPSource`), so expect several rows to show the *same* level — the meters are
reading what WSLg gives Linux, which is one source behind several entries. Not
confirmed on a WSL host yet; see `TODO.md`.

There is no global hotkey for settings; from outside the terminal use the
control API (`python src/main.py status`, `toggle`, `output type_fast`, …).

There are two settings paths, and they are not the same menu:

- **In the TUI**, `s` / `S` / `,` runs Rust's own modal
  (`settings_picker::run_settings_picker`) on the alternate screen. It toggles
  settings in place and hosts the theme / microphone / mode-preset sub-pickers,
  sending the resulting `{"t":"cmd",…}` messages back to the engine.
  Transcription and event blocks that arrive while it (or any sub-picker) is
  open are queued and flushed to the scrollback once it closes, so nothing is
  lost.
- **From the control API**, `python src/main.py settings` (and the `theme` /
  `mic` / `preset` verbs) calls Python's `open_*_picker()`, which **suspends**
  the TUI (`{"t":"suspend"}` / `ratatui::restore()`), lets Python draw its own
  Rich menu, then resumes (`{"t":"resume"}`). The engine queues messages while
  suspended and replays them on resume.

## IPC protocol

Newline-delimited JSON over a Unix socket (`$XDG_RUNTIME_DIR/vt-tui-$UID.sock`).

Python → Rust:

```
{"t":"cfg","mic":"...","secondary":"...","backend":"cohere","muted":true,
 "auto_type":false,"sound_theme":"proximity","ui_theme":"green"}
{"t":"header"}                       # push the banner into scrollback
{"t":"state","state":"RECORDING","sub":""}
{"t":"vu","level":0.42}                # scalar meter: recording + main VU bar
{"t":"vu","level":0.42,"levels":[{"i":4,"level":0.0},{"i":5,"level":0.44}]}
                                     # per-device levels for the mic picker rows;
                                     # absent `levels` leaves the rows alone, an
                                     # explicit empty list blanks every row
{"t":"tx","text":"...","rec":3.2,"proc":1.35,"ready":0.62,"status":"typed"}
{"t":"ev","title":"...","message":"...","level":"info"}
{"t":"suspend"} / {"t":"resume"} / {"t":"quit"}
```

Rust → Python (every command the frontend emits; regenerate with
`grep -rn 'send_cmd' src/`):

```
{"t":"cmd","cmd":"toggle_record"}          # Space / Enter
{"t":"cmd","cmd":"quit"}                   # q / Esc / Ctrl+C
{"t":"cmd","cmd":"reset_terminal"}         # r, and the settings action
{"t":"cmd","cmd":"get_devices"}            # mic picker: ask for the device list
{"t":"cmd","cmd":"start_mic_monitor","indices":[4,5]}
                                           # open a level stream per visible row
{"t":"cmd","cmd":"stop_mic_monitor"}       # mic picker: close the meter streams
{"t":"cmd","cmd":"set_device","device":"...","index":4}  # mic picker: select

# Settings modal (toggles and actions):
{"t":"cmd","cmd":"toggle_mute"}            # Sound Effects
{"t":"cmd","cmd":"toggle_trailing_space"}
{"t":"cmd","cmd":"toggle_auto_punctuate"}
{"t":"cmd","cmd":"toggle_numbers"}         # Number Conversion
{"t":"cmd","cmd":"toggle_serial_collapse"} # Serial/Codes
{"t":"cmd","cmd":"toggle_spell_command"}   # Spell Command
{"t":"cmd","cmd":"toggle_middle_click"}    # Mouse Hotkey
{"t":"cmd","cmd":"cycle_output_mode"}      # Output Delivery (clipboard|type|type_fast)
{"t":"cmd","cmd":"cycle_typing_wpm"}       # Typing Speed (cycle preset)
{"t":"cmd","cmd":"set_typing_wpm","wpm":65} # Typing Speed (explicit)
{"t":"cmd","cmd":"rescan_mics"}            # re-enumerate audio devices
{"t":"cmd","cmd":"reset_defaults"}        # restore shipped defaults

# Push-to-talk bind sub-picker:
{"t":"cmd","cmd":"hotkey_add","chord":"rightctrl+shift"}
{"t":"cmd","cmd":"hotkey_remove","chord":"alt+shift"}

# Theme / mode-preset sub-pickers:
{"t":"cmd","cmd":"set_theme","theme":"cyan"}
{"t":"cmd","cmd":"set_punctuation","mode":"aesthetic_lowercase"}
```

`start_mic_monitor` replaces the monitored set: devices missing from `indices`
are closed, devices already open are kept, so scrolling the picker does not
restart every stream. A device that refuses to open (held exclusively by
another app, or rejecting the sample rate) is skipped rather than failing the
whole picker — its row simply stays at 0%.

Set `VT_TUI_DEBUG=/tmp/vt.log` for a trace of commands, suspend/resume, and
settings-menu failures.

## Standalone mode (no engine)

The prototype still runs on its own with scripted demo data:

```bash
nix-shell -p cargo rustc --run 'cargo run'              # demo timeline
nix-shell -p cargo rustc --run 'cargo run -- --no-demo' # keyboard-driven
```

`--theme <auto|green|cyan|blue|magenta|yellow|red|white>` selects the palette.

## Resize behaviour (important)

Resizing is the sharp edge of this design, and worth understanding before
integrating:

- **ratatui 0.29 inline viewports have an immutable height.** `Terminal::resize()`
  ignores the height in the `Rect` you pass and reuses the construction-time
  `Viewport::Inline(h)` (see `terminal.rs:212`). `viewport_area`/`set_viewport_area`
  are private, so there is no in-place way to grow it.
- The frontend therefore **recreates the `Terminal` with a new `Viewport::Inline(h)`**
  when the required height changes (width or wrap count), and **clamps every draw to
  `frame.area().height`** so ratatui can never index outside the buffer. Without the
  clamp, a terminal that narrows from 120 → 60 columns panics with
  `index outside of buffer`.
- On a **horizontal shrink the terminal re-wraps existing lines**. Because an inline
  app only owns a small region at the bottom, it cannot erase the reflowed copies of
  the header/status bar that the terminal leaves above it, so you can see stale
  status lines after shrinking. This is inherent to the inline/scrollback style and
  affects inline TUIs generally; a full-screen (alternate screen) layout redraws the
  whole buffer each frame and resizes cleanly.

## Files

| File | Purpose |
| :-- | :-- |
| `src/app.rs` | State machine mirroring `VoiceTranscriberTUI` (`RunState`, metrics, theme resolution, VU decay, modal output queue) |
| `src/ui.rs` | Status line, header, transcription/event block rendering |
| `src/ipc.rs` | Socket client + wire message types |
| `src/settings_picker.rs` | The `s`/`S`/`,` settings & configuration modal (in-place toggles) |
| `src/bind_picker.rs` | Push-to-talk key bind editor (nested in the settings modal) |
| `src/mic_picker.rs` | Microphone picker with per-row live meters (nested) |
| `src/preset_picker.rs` | Punctuation/mode-preset picker (nested) |
| `src/theme_picker.rs` | UI colour-theme picker (nested) |
| `src/textfit.rs` | Width-exact fitting/centering helpers shared by the pickers |
| `src/demo.rs` | Scripted "fake engine" timeline for standalone mode |
| `src/main.rs` | Terminal setup, event loop, keyboard handling, suspend/resume |

Python side: [`../src/tui_ratatui.py`](../src/tui_ratatui.py) (the `RatatuiTui`
client) and the `create_tui()` factory in [`../src/main.py`](../src/main.py).
