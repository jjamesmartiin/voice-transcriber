# Security Policy

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Report it privately
through GitHub's [private vulnerability reporting][advisory] (the *Security* tab →
*Report a vulnerability*), or by email to **jjamesmartiin@gmail.com**.

Include, as far as you can:

- what the issue is and the impact you believe it has,
- the version or commit you tested (`vt --version`, or the tag),
- your OS and how the app was installed (AppImage, Nix, Windows EXE, source),
- a reproduction, if one is practical.

You can expect an acknowledgement within a few days. This is a
maintainer-run project, so please allow reasonable time before disclosing
publicly.

## Scope

Voice Transcriber runs entirely on your machine — there is no server component,
no account, and no telemetry. The threat model that matters is therefore local:

| In scope | Notes |
| :--- | :--- |
| **The control socket** | A UNIX socket in `$XDG_RUNTIME_DIR`, owner-only (`0600`), that accepts newline-delimited JSON. Anything that lets another local user issue verbs, read transcripts, or take over the engine is a vulnerability. |
| **The WSL host bridge** | `src/voice_transcriber/platform/wsl/wsl_win_hotkeys.ps1` runs on the Windows host and receives data from the guest. Command or argument injection across that boundary is in scope. |
| **Synthetic input on Linux** | The app reads `/dev/input/event*` and writes to `/dev/uinput`. A path that lets unprivileged code escalate through either is in scope. |
| **Log and state files** | Transcripts and settings are written to the per-user data dir. World-readable logs or state are in scope. |
| **The model download path** | Weights arrive over the network from a GitHub release. A missing or bypassable integrity check is in scope. |

## Out of scope

- Vulnerabilities in the upstream model weights or in PyTorch/`transformers`
  (report those upstream — though a *packaging* problem here is in scope).
- Anything that requires an attacker to already control your user account.
- The obvious-but-intended capability: **this app types what you say into the
  focused window, and reads global hotkeys.** That is the product. Misuse by
  software already running as you, or dictating something you did not mean to
  dictate, is not a vulnerability.
- Denial of service from physically disconnecting your own microphone.

## Design notes that are deliberate, not bugs

- The control socket is **not** authenticated beyond filesystem permissions.
  Any process running as you can drive the engine — this is by design, and the
  same trust boundary as your shell.
- Requests that are malformed or hostile must never take the engine down; the
  server catches them per-connection. If you find one that does, that *is* a
  bug worth reporting.
- On native Windows the control socket does not exist at all (stock CPython has
  no `socket.AF_UNIX`), so there is no local API surface there.

[advisory]: https://github.com/jjamesmartiin/voice-transcriber/security/advisories/new
