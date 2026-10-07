# Voice Transcriber — Blog Post & Public Launch Readiness Plan

This document outlines the end-to-end plan to take the `voice-transcriber` repository from an active development codebase to a polished, public-ready open source release suitable for a blog post, Hacker News, Reddit (`r/linux`, `r/rust`, `r/selfhosted`), and social media announcement.

---

## Phase 1: Repository Hygiene & Architecture Cleanup

Clear out transient work, archive internal planning documents, and ensure visitors see a polished production repository rather than an active sprint branch.

- [ ] **Archive internal planning documents**:
  - Move root `plan.md` (cross-platform convergence plan) → `docs/archive/plan-crossplatform-convergence.md`.
  - Move `docs/java-fork-plan.md` (exploratory JVM fork RFC) → `docs/archive/java-fork-plan.md`.
  - Update references to `java-fork-plan.md` in `README.md` and docs to point to `docs/archive/`.
- [ ] **Verify git status & remotes**:
  - Ensure all commits are pushed to `github/main` and `home/main` (when network allows).
  - Verify working tree is clean.
- [ ] **CI Quality Gate Hardening**:
  - Add `nix build .#vt-tui` to `.github/workflows/ci.yml` (in the Linux CI job) so the Rust Ratatui frontend is compiled and validated on every push/PR.

---

## Phase 2: Visual Assets & Interactive Demo Generation

Modern developer tools (especially TUI applications) require immediate visual proof. Visitors decide whether to stay within 5–10 seconds.

- [ ] **Create assets directory**: `docs/assets/` (or `assets/`) for demo GIFs, screenshots, and diagrams.
- [ ] **Generate Terminal Demo Recording (Hero GIF)**:
  - Run `vt-tui` in scripted demo mode (`--demo`) or capture a live transcription loop.
  - Record via `vhs` or `asciinema` + `agg`.
  - Export to an optimized GIF / WebP / SVG (`docs/assets/vt-demo.gif` or `.svg`).
  - Demonstrate: live push-to-talk recording, VU metering, Cohere ASR transcription, verbal retraction parsing (*"no wait, make that..."*), and dynamic dictionary substitution.
- [ ] **Capture UI Screenshots**:
  - `docs/assets/tui-main.png`: Inline REPL layout with status line and divider metadata.
  - `docs/assets/settings-modal.png`: Interactive configuration overlay with fuzzy search.
  - `docs/assets/mic-picker.png`: Live simultaneous multi-device VU meter picker.
- [ ] **System Architecture Diagram**:
  - Render a clean structural diagram (ASCII / SVG) illustrating:
    - Audio Capture & Streaming VAD → Cohere Transcribe → Micro-batcher
    - Microsecond Post-Processor (~16 µs) with Trie Dictionary & Homophone Guards
    - IPC Socket → Ratatui TUI (Rust)
    - Hardware Abstraction Layer (HAL) → Synthetic Keystrokes & Clipboard across Wayland, X11, Win32, WSL2, macOS.

---

## Phase 3: README & Above-the-Fold Overhaul

Structure `README.md` so the value proposition and quickstart are immediately visible, pushing dense OS requirement matrices down.

- [ ] **Add Status & Release Badges**:
  - Release: `v1.2.1`
  - CI Status: GitHub Actions passing
  - License: MIT / Apache-2.0
  - Supported Platforms: Linux (Wayland/X11), Windows, WSL2, macOS
- [ ] **Hero Section & Value Proposition**:
  - Punchy 2-sentence hook: *What is Voice Transcriber and why does it exist?*
  - Embed the **Hero Demo GIF** directly below the title.
  - Highlight the 4 core differentiators:
    1. **100% Offline & Private**: Zero cloud API tokens, subscriptions, or telemetry.
    2. **Push-to-Talk + Hands-Free Space Latch**: Global hotkeys anywhere on the desktop.
    3. **Microsecond Post-Processing (~16 µs)**: Verbal retractions, ordinals, numbers, and personal developer dictionaries.
    4. **Direct Keystroke Injection**: Types naturally into any focused window (browsers, IDEs, terminals).
- [ ] **30-Second Quickstart (Above the Fold)**:
  - Linux: Download `vt-x86_64.AppImage` + run (or `nix run .`).
  - Windows: Download `VoiceTranscriber-windows-x86_64.zip` + extract + run `VoiceTranscriber.exe`.
  - macOS / Nix: `nix run .` or `./setup.sh` && `./run.sh`.
- [ ] **Restructure Deep Reference Material**:
  - Relocate detailed OS requirements and kernel configuration tables cleanly beneath Quickstart and Core Features.
  - Ensure the built-in diagnostic `./run.sh doctor` / `run.bat doctor` is prominently featured for troubleshooting permissions and muted mics.

---

## Phase 4: GitHub Repository Presentation & Metadata

Optimize repository metadata for discoverability and social sharing.

- [ ] **Repository Description**:
  - Update tagline:
    > *"Offline, ultra-low-latency voice dictation with global push-to-talk, Ratatui TUI, and instant text injection across Linux, Windows, WSL & macOS."*
- [ ] **Topics / Tags**:
  - Add: `speech-to-text`, `voice-dictation`, `transcription`, `ratatui`, `rust`, `python`, `nix`, `wayland`, `offline`, `cohere`.
- [ ] **Social Preview Image (OpenGraph)**:
  - Generate a 1280×640 branded social card (featuring the Ratatui TUI screenshot, logo, and core features) for Twitter / Reddit / LinkedIn embeds.

---

## Phase 5: Technical Blog Post Outline & Narrative

Draft the companion blog post highlighting the hard engineering challenges and architectural choices that make Voice Transcriber unique.

### Blog Post Structure

1. **The Hook: The Search for the "Invisible" Voice Dictation Engine**
   - The friction with cloud dictation: latency, subscription lock-in, data privacy risks.
   - The friction with raw local Whisper: high resource usage on CPU, hallucination loops on silence ("Thank you for watching!"), lack of system-wide push-to-talk.
   - The goal: An engine as responsive as typing, available everywhere in the OS, completely offline.

2. **The Acoustic Engine: Why Cohere Transcribe?**
   - Choosing Cohere's acoustic model (`cohere-transcribe-03-2026`) over standard Whisper architectures.
   - Inference efficiency on CPU, low RTF (Real-Time Factor), and resistance to hallucinating on background ambient noise.
   - Multi-lingual acoustic capability with an English-optimized pipeline.

3. **Sub-Millisecond Language Post-Processing (~16 µs)**
   - Why raw ASR transcripts are unusable for direct typing (stutters, "uh/um", unformatted numbers, verbatim mistakes).
   - The Wispr Flow post-processor pipeline:
     - **Verbal Retraction Engine**: Handling *"remind me Tuesday no wait make that Wednesday"* → *"Remind me Wednesday."*
     - **Ordinal & Date Parsing**: Spoken dates converted to ordinals (*"October twentieth"* → *"October 20th"*).
     - **Trie-Compacted Personal Dictionary**: Dual-tier architecture (`dictionary.yaml` common + `dictionary.local.yaml` gitignored private) matching in microseconds.
     - **Context-Aware Homophone Disambiguation**: Trigger words and guards (*"push to get tea"* → *"Gitea"* vs *"cup of tea"*).

4. **The Reality of Cross-Platform Audio: Hunting OS Gremlins**
   - **The Muted Microphone Bug**: PipeWire sources that report healthy status, open cleanly, and record pure zeros. Building `doctor --fix` to detect and repair volume state via `wpctl`.
   - **PortAudio Device Lockups**: When another application locks a USB mic at startup, PortAudio silently drops it. Designing the in-process **Reset Microphones** (`rescan-mics`) workflow.
   - **WirePlumber Bluetooth Headsets**: Preserving and restoring `bluetooth.autoswitch-to-headset-profile` on process exit.
   - **Simultaneous Multi-Mic VU Metering**: Opening concurrent audio streams for all visible input devices in Rust so users can see which mic is picking up signal.

5. **Cross-Platform Input Injection Without Hacks**
   - The Linux challenge: Global evdev hotkeys, `uinput` virtual devices, and typing under Wayland (`ydotool` / `wl-copy`) vs X11 (`xdotool` / `xclip`).
   - The Windows challenge: Native Win32 `SendInput` handling Unicode and emoji cleanly.
   - The WSL2 boundary: Bridging Windows host hotkeys and clipboard paste into a Linux guest via PowerShell socket IPC.
   - The Push-to-Talk + Hands-Free Space Latch state machine.

6. **Architecture: Python Engine + Rust Ratatui Frontend**
   - Keeping the Python core headless and authoritative (audio, VAD, model inference, configuration).
   - Driving the UI via a Unix socket IPC protocol with Ratatui for an inline, zero-border REPL experience with full scrollback copy-paste support.

7. **Conclusion & Trying It Out**
   - AppImage, Standalone Windows ZIP, and Nix flake one-liners.
   - Future roadmap (CUDA AppImages, macOS packaging, multi-language post-processing).

---

## Execution Order
1. **Archive internal planning documents** (`plan.md`, `java-fork-plan.md`).
2. **Harden CI** (`ci.yml` Rust build step).
3. **Generate Visual Assets** (Hero GIF & screenshots).
4. **Overhaul `README.md`** (Hero, Quickstart, Badges, Visuals).
5. **Draft Blog Post Article** (`docs/blog_post.md`).
