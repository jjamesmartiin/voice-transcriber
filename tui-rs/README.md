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
| `Space` / `Enter` | Start / stop recording |
| `M`, `i` | Audio device / settings menu (Python takes the terminal back) |
| `m` | Toggle sound effects |
| `c` | Toggle auto-type vs clipboard |
| `n` | Toggle number-to-digits |
| `t` | Cycle UI theme |
| `r` | Reset terminal & clipboard bridge |
| `q`, `Esc`, `Ctrl+C` | Quit |

The settings menu is opened by **suspending** the TUI (`ratatui::restore()`),
letting Python draw its own Rich menu, then resuming. Messages the engine emits
while suspended are queued and replayed on resume, so nothing is lost.

## IPC protocol

Newline-delimited JSON over a Unix socket (`$XDG_RUNTIME_DIR/vt-tui-$UID.sock`).

Python → Rust:

```
{"t":"cfg","mic":"...","secondary":"...","backend":"cohere","muted":true,
 "auto_type":false,"sound_theme":"proximity","ui_theme":"green"}
{"t":"header"}                       # push the banner into scrollback
{"t":"state","state":"RECORDING","sub":""}
{"t":"vu","level":0.42}
{"t":"tx","text":"...","rec":3.2,"proc":1.35,"ready":0.62,"status":"typed"}
{"t":"ev","title":"...","message":"...","level":"info"}
{"t":"suspend"} / {"t":"resume"} / {"t":"quit"}
```

Rust → Python:

```
{"t":"cmd","cmd":"toggle_record"}    # and: change_device, toggle_mute,
                                     # toggle_autotype, toggle_numbers,
                                     # cycle_theme, reset_terminal, quit
```

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
| `src/app.rs` | State machine mirroring `VoiceTranscriberTUI` (`RunState`, metrics, theme resolution) |
| `src/ui.rs` | Status line, header, transcription/event block rendering |
| `src/ipc.rs` | Socket client + wire message types |
| `src/demo.rs` | Scripted "fake engine" timeline for standalone mode |
| `src/main.rs` | Terminal setup, event loop, keyboard handling, suspend/resume |

Python side: [`../src/tui_ratatui.py`](../src/tui_ratatui.py) (the `RatatuiTui`
client) and the `create_tui()` factory in [`../src/main.py`](../src/main.py).
