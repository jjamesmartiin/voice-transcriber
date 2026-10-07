# Control API

Voice Transcriber can be driven from outside the terminal: start and stop
recording, change any setting, read back state and the last transcript. This
lets another program trigger dictation, and lets tests drive a **real running
engine** instead of reaching into internals.

Two halves, one vocabulary:

- the **engine** binds a Unix socket and serves commands (see
  [`src/control.py`](../src/control.py));
- **`main.py` itself is the client** — `python src/main.py <verb>`.

The same verbs also arrive from the ratatui frontend over its own socket, so the
two surfaces cannot drift apart.

---

## Quick start

```bash
# with the app already running in another terminal:
python src/main.py status      # what is it doing right now?
python src/main.py toggle      # start recording (or stop, if recording)
python src/main.py stop        # stop and transcribe
python src/main.py wait        # block until the transcript is ready
python src/main.py mute        # flip the audio cues
python src/main.py help        # every verb
```

Under Nix the verbs pass through the wrapper: `nix run . -- status`.

Running `python src/main.py` with **no verb** still launches the app. That is
deliberate: a bare invocation is the normal case, and any leading `-` is treated
as "not a control verb" so future flags stay safe.

### CLI options

| Option | Meaning |
| :--- | :--- |
| `--json` | Print the raw JSON reply instead of the human summary |
| `--socket PATH` | Target a specific instance (default: the per-user socket) |
| `-h`, `--help` | Print the CLI help plus the verb catalogue |

### Exit codes

| Code | Meaning |
| :--- | :--- |
| `0` | The engine accepted the command (`ok: true`) |
| `1` | The engine rejected it, or no instance was listening |
| `2` | Unknown verb (local usage error, nothing was sent) |

---

## Transport

| | |
| :--- | :--- |
| **Socket** | `$XDG_RUNTIME_DIR/vt-control-<uid>.sock` |
| **Override** | `VT_CONTROL_SOCKET=/path/to.sock` |
| **Fallback dir** | `tempfile.gettempdir()` when `XDG_RUNTIME_DIR` is unset |
| **Windows** | **Unavailable** — see the note below. The `<uid>` token would be `%USERNAME%` |
| **Framing** | One newline-delimited JSON request, one newline-delimited JSON reply, per connection |
| **Concurrency** | Many clients; each connection is served on its own daemon thread |
| **Request limit** | 1 MiB, then the connection is dropped |
| **Timeouts** | 5 s read/write per connection |

> **Native Windows: the control API is unavailable.** Windows 10 1803+ supports
> `AF_UNIX` at the OS level, but **stock CPython never exposes
> `socket.AF_UNIX`** ([bpo-33408](https://bugs.python.org/issue33408); the
> upstream PR is still unmerged), and `control.socket_supported()` is exactly
> `hasattr(socket, "AF_UNIX")`. On native Windows the engine therefore does not
> bind the socket — `src/main.py` records that at **debug** level only — and
> every verb fails with:
>
> ```
> ✗ AF_UNIX sockets are unavailable on this platform
> ```
>
> The ratatui frontend is disabled for the same reason
> (`tui_ratatui.tui_available()`), so native Windows falls back to the Rich TUI
> and must be driven with terminal keys. **WSL is unaffected**: the app runs on a
> Linux interpreter there, which has `AF_UNIX`. Tracked in
> [`TODO.md`](../TODO.md).

The socket is created by the engine at startup and removed on clean shutdown. It
is bound after every attribute a verb can touch exists, so commands work from the
moment it appears — including while the model is still loading.

### Isolation

A malformed, truncated, oversized or hostile request **cannot take the engine
down**. Bad input becomes an error reply:

```json
{"ok": false, "error": "missing 'cmd'"}
```

Unknown verbs, bad values and missing values are raised, logged and returned the
same way. The app itself never sees the failure.

---

## Protocol

Write one JSON object, optionally terminated by a newline. A **bare verb** is
also accepted, so hand-typed clients work:

```bash
# JSON form
printf '{"cmd":"output","value":"type_fast"}\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/vt-control-$(id -u).sock"

# bare form
printf 'toggle\n' | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/vt-control-$(id -u).sock"
```

### Request fields

| Field | Required | Meaning |
| :--- | :--- | :--- |
| `cmd` | yes | The verb. `command` is accepted as an alias. Case-insensitive; `_` and `-` are interchangeable (`set_mic` == `set-mic`) |
| `value` | per verb | The verb's argument. `name` and `mode` are accepted as aliases |
| `copy` | no | `stop` only: whether to force clipboard output (default `true`) |

### Reply shape

Every reply is a JSON object with at least `ok`:

```json
{
  "ok": true,
  "cmd": "status",
  "state": "READY",
  "recording": false,
  "device": "Primary: default",
  "model": "cohere",
  "muted": true,
  "output_mode": "type_fast",
  "number_mode": "auto",
  "punctuation_mode": "full",
  "ui_theme": "red",
  "middle_click": false,
  "last_transcription": "I deployed on NixOS using kubectl.",
  "session_words": 30,
  "session_time_saved_sec": 41.2,
  "lifetime_words": 4210,
  "lifetime_time_saved_sec": 4802.3,
  "lifetime_time_saved": "1h 20m",
  "lifetime_transcriptions": 512,
  "lifetime_sessions": 37,
  "typing_wpm": 40
}
```

Volume metrics (`last_transcription`, `session_*`, `lifetime_*`) are descriptive —
see [Dictation stats](#dictation-stats).

Failures carry `error` instead of state:

```json
{"ok": false, "cmd": "output", "error": "output needs a value"}
```

`state` is one of `READY`, `RECORDING`, `PROCESSING` (and `UNKNOWN` before the
frontend reports in). `PROCESSING` covers both "transcribing" and the initial
model load.

### Dictation stats

`status` and `wait` also report how much typing dictation has saved, computed at
`typing_wpm` (see the `wpm` verb):

| Field | Meaning |
| :--- | :--- |
| `session_words`, `session_time_saved_sec` | This engine process only; resets on restart |
| `lifetime_words`, `lifetime_time_saved_sec` | All-time, persisted across restarts |
| `lifetime_time_saved` | The same total pre-formatted (`"1h 20m"`) for humans |
| `lifetime_transcriptions` | Number of dictations folded into the totals |
| `lifetime_sessions` | Engine launches counted |

Totals live in a small JSON file (`stats.json`) in the platform data directory,
written after **every** dictation so a crash loses at most the in-flight one. The
file is created automatically on first launch (engine startup counts a session),
and the directory is created with it if it does not exist. Override the location
with `VT_STATS_FILE` (git ignores any `stats.json`, so an override into the repo
is still safe). They are *not* part of `config.yaml`, so `reset-defaults` leaves
them alone — delete the file to clear them:

```bash
python src/main.py status                              # ...ends with a one-line summary
rm "${XDG_DATA_HOME:-$HOME/.local/share}/vt/stats.json" # wipe the all-time totals
```

> **Native Windows:** the file and the `⚡ saved` badge work exactly as on
> Linux/macOS, but the command above does not — the control API needs
> `AF_UNIX`, which stock CPython does not expose on Windows (see the note at the
> top of this document). Read the totals from the badge, or from
> `%LOCALAPPDATA%\vt\stats.json`.

Human-readable CLI output is encoding-safe: on a stream that cannot encode the
`·` separator or the `✓`/`✗` markers they become `|`, `+` and `x` rather than
raising `UnicodeEncodeError`. Use `--json` when you want to parse the reply
regardless of the console's encoding.

---

## Verbs

`start`, `stop`, `toggle`, `wait`, `status`, `mics`, `set-mic`, `rescan-mics`,
`settings`,
`mic`, `theme`, `output`, `numbers`, `punctuation`, `trailing-space`,
`auto-punctuate`, `serial`, `spell`, `middle-click`, `hotkey`, `mute`, `reset-defaults`,
`reset-terminal`, `ping`, `doctor`, `help`, `quit`.

Get this catalogue programmatically — it is the machine-readable source of truth
(see [For LLM agents](#for-llm-agents)). `help --json` is served locally, so it
works even when no instance is running; a running engine returns the same spec
from its own `help` verb.

```bash
python src/main.py help --json
```

`doctor` is answered locally as well, so diagnostics work with no engine up —
`python src/main.py doctor --json` for machines, `--fix` to let it repair what it
safely can (currently: **unmute a muted default microphone**). Every other verb
is a plain socket client and fails when no instance is running.

### Recording

| Verb | Value | Notes |
| :--- | :--- | :--- |
| `start` | — | Recording starts immediately. If the model is still loading, recording proceeds and transcription waits — same as pressing the hotkey |
| `stop` | — | Schedules transcription. Pass `copy: false` to respect the configured output mode |
| `toggle` | — | `start` if idle, `stop` if recording |
| `wait` | `seconds` (default 30) | Blocks until `state != PROCESSING`, then returns status plus `timed_out` |

`start`, `stop` and `toggle` wait (up to 1 s) for the UI state to settle before
replying, so `stop` reports `PROCESSING`/`READY` rather than a stale `RECORDING`.

Transcript text is never returned by `stop` itself — output goes to the clipboard
or is typed as usual. Read it from `last_transcription` on `status`/`wait`.

### Devices

| Verb | Value | Notes |
| :--- | :--- | :--- |
| `mics` | — | Returns `devices`: a list of input device names |
| `set-mic` | name substring | First case-insensitive substring match wins; errors if nothing matches |
| `rescan-mics` | — | Re-enumerates input devices and re-resolves the selected mic. Returns `devices`, `device` and `missing` (inputs the engine still cannot see, each with the `holder` app when known). Refused while recording |

> **Why `rescan-mics` exists.** PortAudio builds its device list once, at
> startup, and *omits* any device it cannot open at that moment instead of
> marking it unavailable. A microphone another app is holding then — PipeWire
> routing the system default, a level meter, a browser — is therefore missing
> from `mics` for the life of the process, even though it is plugged in. This is
> the only verb that repairs that without a restart; it is also the settings
> modal's **Reset Microphones** item.

### Settings

Every one of these persists to `config/config.yaml`, exactly as a change made in
the settings modal would.

| Verb | Value | Accepted values |
| :--- | :--- | :--- |
| `output` | mode | `clipboard`, `type`, `type_fast` |
| `numbers` | mode | `auto`, `digits`, `words` |
| `punctuation` | mode | `full`, `no_terminal_period`, `no_punctuation`, `aesthetic_lowercase`, `gen_z` |
| `theme` | theme | `auto`, `green`, `cyan`, `blue`, `magenta`, `yellow`, `red`, `white` |
| `trailing-space` | state | `on`, `off` (omit to toggle) |
| `auto-punctuate` | state | `on`, `off` (omit to toggle) |
| `serial` | state | `on`, `off` (omit to toggle) |
| `spell` | state | `on`, `off` (omit to toggle) |
| `middle-click` | state | `on`, `off` (omit to toggle) |
| `hotkey` | command | `list` (default), `keys`, `add <chord>`, `remove <chord>`, `reset` |
| `mute` | state | `on`, `off` (omit to toggle) |
| `wpm` | words-per-minute | Integer WPM, e.g. `40`, `60`, `80` (omit to cycle presets) |

`on`/`off` also accept `true`/`false`, `1`/`0`, `yes`/`no`, `enable`/`disable`.

### Push-to-talk binds

`hotkey` (aliases `hotkeys`, `binds`) manages the chords that start dictation.
`Alt+Shift` is only the shipped default — a user can hold any number of chords,
including a remapped one or a single key. Every reply carries the resulting list
as `hotkeys`, so no follow-up call is needed:

```bash
python src/main.py hotkey --json                          # ["alt+shift"]
python src/main.py hotkey keys --json                     # every valid key name
python src/main.py hotkey "add ctrl+shift" --json         # add one
python src/main.py hotkey "add f13" --json                # single key
python src/main.py hotkey "remove alt+shift" --json       # drop one
python src/main.py hotkey reset --json                    # back to Alt+Shift
```

`hotkey keys` returns `[{name, group, aliases}, ...]` for every bindable key,
derived from the single declaration table the backends resolve through
(`src/voice_transcriber/keybinds.py`), so the list a caller sees cannot drift
from what the matcher accepts. Every reply also carries the resulting
`hotkeys` list, so no follow-up call is needed.

Chords are `+`-joined names from that table: modifiers
(`ctrl`/`leftctrl`/`rightctrl`, `alt`, `shift`, `meta` — also `super`/`win`/`cmd`),
`f1`–`f24`, letters, digits, `space`, `tab`, `esc`, `enter`,
backspace/delete/insert, arrows, `home`/`end`/`pageup`/`pagedown`, `capslock`,
`printscreen`, `scrolllock`, `pause`, `menu`. A bare modifier matches either
side; `rightctrl+shift` matches only the right one; aliases (`option`, `pgup`,
`return`, `escape`, …) resolve to the same chord. Removing the final bind is
refused (push-to-talk has to stay reachable from the keyboard), and so is adding
a chord that is already bound. Changes are persisted to `hotkeys:` in the config
file and pushed at the live hotkey backend immediately.

### Interactive modals

| Verb | Notes |
| :--- | :--- |
| `settings` | Opens the settings modal — the only configuration entry point |
| `mic` | Opens the microphone picker modal |
| `theme` (no value) | Opens the theme picker modal |

These draw on the real terminal and read keystrokes, so they are **interactive** —
they suspend the ratatui frontend while Python owns the screen, then resume.
Do not call them from a non-interactive script expecting an immediate return.
They are refused while recording.

### Lifecycle

| Verb | Notes |
| :--- | :--- |
| `reset-defaults` | Restores shipped defaults; keeps the microphone choice and the dictionary |
| `reset-terminal` | Resets terminal state and the clipboard bridge |
| `doctor` | Runs diagnostics **locally** (no engine needed): audio devices, default-microphone **mute state**, permissions, acceleration, model weights. `--fix` unmutes a muted default microphone. Exits non-zero when a check fails |
| `ping` | Returns `pid`; cheap liveness check |
| `quit` | Replies first, then shuts the engine down (~0.25 s later) |

---

## Scripting recipes

### Record, transcribe, read the text

```bash
python src/main.py start
sleep 3                                   # speak
python src/main.py stop
python src/main.py wait --json | python -c 'import json,sys; print(json.load(sys.stdin)["last_transcription"])'
```

### Wait for an instance to come up

The socket appears as soon as the engine can serve commands (before the model
finishes loading):

```bash
for _ in $(seq 1 60); do
  python src/main.py ping >/dev/null 2>&1 && break
  sleep 1
done
python src/main.py status --json
```

### Python client

```python
import sys
sys.path.insert(0, "src")
import control

reply = control.send_command("toggle")
if not reply["ok"]:
    raise SystemExit(reply["error"])

reply = control.send_command("output", value="type_fast")
print(reply["output_mode"])
```

`send_command` never raises: transport problems come back as
`{"ok": False, "error": ...}` so callers branch on `ok`.

### Query without the CLI

```bash
printf 'ping\n'   | socat - "UNIX-CONNECT:$XDG_RUNTIME_DIR/vt-control-$(id -u).sock"
printf 'status\n' | nc -U "$XDG_RUNTIME_DIR/vt-control-$(id -u).sock"
```

---

## Testing with the control API

Because the vocabulary matches the frontend, tests can exercise the whole stack
as a black box:

```python
import control, time

def test_dictation_round_trip(engine_fixture):
    assert control.send_command("start")["ok"]
    time.sleep(2)                      # feed audio
    control.send_command("stop")
    status = control.send_command("wait", value="20")
    assert status["ok"] and not status["timed_out"]
```

`tests/shared/test_control.py` pins the protocol, the isolation guarantees, the
CLI exit codes, and that every documented verb is either handled or explicitly
rejected.

---

## For LLM agents

The API is designed to be driven without reading the source.

1. **Discover the surface** — do not guess. This works with or without a
   running instance, because the catalogue is served from the local code:

   ```bash
   python src/main.py help --json
   ```

   Each verb maps to a spec:

   ```json
   {
     "output": {
       "summary": "Set how transcriptions are delivered",
       "value": "mode",
       "choices": ["clipboard", "type", "type_fast"],
       "required": true
     },
     "middle-click": {
       "summary": "Middle-mouse-button push-to-talk",
       "value": "state",
       "choices": ["on", "off"],
       "toggles": true
     }
   }
   ```

   `choices` is the closed set of accepted values, `required` means the value
   must be supplied, `toggles` means omitting it flips the current state, and
   `returns` lists extra reply keys.

2. **Always use `--json`** so you parse structured output instead of prose.

3. **Check `ok` before anything else.** On failure read `error`; do not retry
   blind. Unknown verbs never reach the engine (exit code `2`).

4. **Prefer one connection per command.** The protocol is request/response; do
   not hold a connection open between commands.

5. **Read the transcript from `status`/`wait`**, not from `stop`:

   ```bash
   python src/main.py wait --json     # includes last_transcription
   ```

6. **`stop` is asynchronous.** `state` becomes `PROCESSING` and later `READY`.
   Use `wait` rather than sleeping a fixed amount.

7. **Avoid modal verbs in automation.** `settings`, `mic` and valueless `theme`
   take over the terminal and wait for a human. Use the granular setting verbs
   instead — they do the same thing without needing a keyboard.

8. **Never write to the socket with a partial line and no newline** if you reuse
   a connection; each connection expects exactly one request.

9. **`quit` terminates the app.** Only use it to shut down an instance you
   started.

### Safe defaults for an agent

| Goal | Command |
| :--- | :--- |
| Is it running? | `python src/main.py ping --json` |
| What is it doing? | `python src/main.py status --json` |
| How much time have I saved? | `python src/main.py status` (the `lifetime_time_saved` field; Linux/WSL/macOS only — on native Windows use the badge) |
| Dictate something | `start` → wait → `stop` → `wait --json` |
| Change output | `python src/main.py output clipboard` |
| Silence the cues | `python src/main.py mute off` |
| Pick a mic | `python src/main.py mics` then `set-mic "<substring>"` |
