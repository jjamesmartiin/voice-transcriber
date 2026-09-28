# Plan: a Java (JVM) fork of voice-transcriber

Status: **plan only — no code written, nothing committed to this approach.**
Audience: whoever picks this up. Read `docs/architecture.md` first for how the
Python engine is put together.

## 1. Goal and non-goals

**Goal.** A JVM implementation of the engine that a user can install on Linux,
native Windows and WSL2 and drive exactly as they do today: global push-to-talk,
hands-free latch, streaming VAD, Cohere ASR, English post-processing, real-time
text injection. No Python runtime on the end user's machine.

**Non-goals (deliberate).**

- Not rewriting the Rust frontend. `tui-rs/` is 4.6k lines and stays.
- Not changing the model, the weights, or the config/dictionary formats.
- Not changing the control API surface. It is a published contract
  (`docs/control_api.md`, 24 verbs) that scripts and agents already depend on.
- Not a feature race with the Python engine. Parity first, then diverge.

## 2. What we get for free (do not rebuild these)

This is the single most important section. Three things are reusable as-is and
each removes weeks of work.

### 2.1 The `vt-tui` socket protocol — reuse the Rust TUI unchanged

`vt-tui` is a **pure view + input device** (see the invariant in `AGENTS.md`); it
owns no state and talks to the engine over a Unix socket
(`$XDG_RUNTIME_DIR/vt-tui-<uid>.sock`). A Java engine that speaks that protocol
keeps the ratatui frontend working verbatim — 4.6k lines of Rust and its entire
test suite carried over for free.

**Requirement:** reverse-engineer and pin the `vt-tui` wire protocol as a
documented contract *before* porting anything, and add a conformance test
against the real binary. It is currently only documented in prose
(`tui-rs/README.md`).

### 2.2 The control API — keeps docs, scripts and agents working

Newline-delimited JSON over `$XDG_RUNTIME_DIR/vt-control-<uid>.sock`
(override `VT_CONTROL_SOCKET`), 24 verbs, one request/reply per connection.

Java 16+ provides `java.net.UnixDomainSocketAddress` +
`SocketChannel.open(StandardProtocolFamily.UNIX)`, so this ports with no JNI.

Two payoffs beyond compatibility:

- **It makes differential testing possible.** The same scenario can be driven
  against the Python engine and the Java engine and the JSON diffed. §7 leans on
  this heavily — it is far better than transliterating 9k lines of pytest.
- **`help --json` is self-describing**, so a conformance test can assert the
  Java verb catalogue matches the Python one field-by-field.

### 2.3 The weights bundle — a straight re-use of the GitHub assets

`src/model_download.py` resolves weights by **model revision**, not by app
release: assets live on a revision-derived tag (`model-cohere-<rev12>`) with a
`SHA256SUMS` manifest and byte-range `.partN.xz` parts. A Java port re-implements
the *client* (HTTP + SHA-256 + xz + tar) and downloads the **same bytes**. No
re-upload, no re-packaging, no second copy of 2.8 GB.

The invariant to preserve, because it was expensive to learn:

- Publish parts **first**, the manifest **last** — a manifest is what makes a
  bundle discoverable.
- Confirm each part is actually fetchable (HEAD) before trusting a manifest; a
  mid-upload bundle must never shadow a complete one.
- Never resolve weights through `/releases/latest`; that tag belongs to the AppImage.

Java libraries: `org.tukaani:xz` (xz), `commons-compress` (tar), plus
`java.net.http.HttpClient`. Keep the same `VT_MODEL_DIR` /
`VT_AUTO_DOWNLOAD_MODEL` / `VT_MODEL_RELEASE_BASE` knobs and write the same
`SOURCE.json` provenance file.

## 3. The critical path: ONNX export

Everything else in this plan is a port — mechanical, estimable, low-variance.
**This section is not.** It is research with a real chance of failure, and it
must be resolved before any serious porting starts.

### 3.1 What we are up against

From `models/cohere/`:

| Fact | Consequence |
| :--- | :--- |
| `modeling_cohere_asr.py` is **1,533 lines of custom code**, registered via `auto_map` | HuggingFace `optimum`'s built-in exporters **will not** handle it. The export must be hand-built against the module's own classes. |
| Conformer encoder (`ConformerEncoder`, `ConvSubsampling`, `RelPositionalEncoding`, `RelPositionMultiHeadAttention`) | Relative positional encoding uses index arithmetic and needs care under dynamic sequence length. This is the classic Conformer attention; exportable, but not free. |
| `MaskedConvSequential` + `batch_norm` conv subsampling with length masking | Variable-length audio must either be bucketed to fixed shapes or exported with `dynamic_axes` and the masking logic traced correctly. |
| Autoregressive decoder with **KV cache**, `cache_implementation="static"` (`_get_static_cache_len`) | The decode loop is Python `generate()`. Exporting "the model" as one graph is not achievable; the realistic shape is **two graphs + a Java decode loop** (§3.2). |
| `config.json`: `strategy: beam`, `beam_size: 1`, `max_generation_delta: 50` | Beam size 1 means **greedy argmax** — a Java loop is tractable. `max_generation_delta` is a stopping rule to reproduce exactly. |
| Head is `TokenClassifierHead` with `log_softmax` tied to the token embedding | Decoder output is log-probabilities; decode is argmax over the vocab. |
| `feat_in: 128`, `d_model: 1280`, `n_heads: 8` | A 128-bin log-mel frontend. Must be reimplemented in Java and matched numerically. |
| `tokenizer.json` is present | Use `ai.djl.huggingface:tokenizers` (DJL's HF tokenizer bindings) rather than reimplementing BPE. Verify the custom `tokenization_cohere_asr.py` post-processing (special-token skipping, trimming) is matched. |
| `split_audio_chunks_energy`, `get_chunk_separator(language)` | Energy-based chunking and **language-specific joining** (CJK does not use spaces). Pure logic, but it changes output text, so port it exactly. |
| `decode_worker_fn` uses `multiprocessing` | A Python-ism. In Java, decode in-process. |

### 3.2 The export shape

```
audio ──► [Java log-mel frontend] ──► encoder.onnx ──► [Java greedy decode loop]
                                          │                    │
                                          └──► decoder.onnx ◄──┘   (with past KV)
```

- `encoder.onnx` — features → encoder hidden states. `dynamic_axes` on batch and
  time. Verify against the Python encoder on fixed fixtures.
- `decoder.onnx` — one step: `(input_ids, past_kv...) → (logits, present_kv...)`.
  Exported with a fixed maximum cache length to match `static` caching, with the
  Java side managing `past_kv` buffers as ONNX Runtime tensors.
- The **decode loop lives in Java** (argmax, special-token stopping,
  `max_generation_delta`), because that is where the Python code's behaviour
  actually lives.

### 3.3 Phase 0 — the go/no-go spike, time-boxed

**Time-box: 3 weeks.** Do this before committing to the rest. Three
independently verifiable steps, in increasing order of risk:

| # | Step | Pass criterion |
| :--- | :--- | :--- |
| 0.1 | Port the log-mel feature extractor to Java from `processing_cohere_asr.py` | Max absolute difference vs. the Python extractor **< 1e-4** on real audio fixtures at several lengths |
| 0.2 | Export `encoder.onnx`, load in ONNX Runtime Java | Encoder output matches Python within **1e-3** max abs diff on the same fixtures |
| 0.3 | Export `decoder.onnx` and implement greedy decode in Java | **Identical transcripts** to the Python engine on the e2e audio fixtures, and it must survive chunked (multi-chunk) inputs |

**Kill criteria — be honest, and stop if hit.** If, by the end of the time-box,
step 0.3 cannot produce matching transcripts, and the gap is not explained by a
specific fixable defect, then **abandon in-process inference**. The fallback is
the Python ASR sidecar: the JVM owns everything (hotkeys, audio, VAD, config,
control API, injection, UI) and spawns a small Python worker for inference over
local IPC. That still removes Python from the user experience, costs far less,
and can be reached from the same codebase — design the ASR boundary as an
interface (`AsrEngine`) so the decision is swappable, not a rewrite.

Specific things that will bite, in likelihood order:

1. Relative-positional-encoding index arithmetic under dynamic length.
2. KV-cache export with a static cache: shape juggling between ONNX and the
   Java loop, plus the `_align_decoder_attention_mask` behaviour.
3. Conv-subsampling masking under variable-length batching — the masked conv
   path is easy to trace *wrong* (it silently produces plausible-but-different
   outputs rather than erroring), so 0.2's tolerance check matters.
4. Tokenizer/post-processing parity producing text that is *nearly* right
   ("nearly right" is the expensive failure mode — it passes manual eyeballing).

## 4. Java stack

| Concern | Choice | Notes |
| :--- | :--- | :--- |
| Inference | `com.microsoft.onnxruntime:onnxruntime` | CPU first. The Python engine runs CPU PyTorch; ONNX CPU is a fair target and usually faster. Add the CUDA EP later behind a flag. |
| Tokenizer | `ai.djl.huggingface:tokenizers` | Loads the existing `tokenizer.json`. |
| Native access | **JNA** | evdev/uinput, `SendInput`/`GetAsyncKeyState`, PortAudio, clipboard. |
| Audio I/O | JNA → **PortAudio** | `javax.sound.sampled` cannot do the device enumeration/selection UX this app needs. |
| Unix sockets | `java.net.UnixDomainSocketAddress` (JDK 16+) | Control API + `vt-tui` protocol, no JNI. |
| Config | SnakeYAML or Jackson YAML | Must round-trip `config/config.yaml` unchanged. |
| Archive | `org.tukaani:xz` + `commons-compress` | Model bundle parts. |
| TUI | **Reuse the Rust `vt-tui`** | Fallback if the protocol cannot be pinned: JLine 3 or Lanterna. |
| Build | Gradle, `jlink` + `jpackage` | Ship a runtime image per platform. Avoid GraalVM `native-image` initially: ONNX Runtime + JNA + reflection make it a project in itself. |
| Tests | JUnit 5 | Plus the differential harness in §7. |

**Target JDK 21 LTS** — Unix domain sockets, records, pattern matching, virtual
threads (useful for socket client handling).

## 5. Port map and effort

Line counts are the real Python sizes, so the relative effort is honest.

| Module (Python) | Lines | Java effort | Risk | Notes |
| :--- | ---: | :--- | :--- | :--- |
| `control.py` | 542 | Low | Low | Near-mechanical; Unix socket + JSON. Keep the verb catalogue byte-identical via `help --json`. |
| `post_processor.py` | 2,168 | Medium | Low | Pure text logic. Largest single port, but no FFI. Highest test-coverage-per-line in the repo — port against those cases. |
| `dictionary.py` | 450 | Low | Low | Trie + YAML. |
| `t2.py` | 2,556 | Medium-high | Medium | The engine/config hub. Entangled with audio device selection; split it deliberately rather than mirroring the globals. |
| `micro_batcher.py` | 481 | Medium | Medium | VAD + batching. Reads real audio characteristics; needs fixture-based parity. |
| `model_download.py` | 459 | Low | Low | §2.3. |
| `notifications.py` | 426 | Low | Low | Platform-specific chimes/notifications. |
| `transcribe_cohere.py` | 513 | **High** | **High** | §3. |
| `tui.py` (Rich) | 578 | *Skip* | — | Use `vt-tui`; a Rich equivalent is not worth it. |
| `tui_ratatui.py` | 572 | Medium | Low | Becomes a socket server for the `vt-tui` protocol. |
| `hal.py` + `platform/*` | 2,940 | **High** | **High** | §6. |

## 6. Platform layer — the second-hardest part

`src/platform/linux/hotkeys.py` alone is **995 lines**: evdev grabs, uinput
virtual devices to re-emit events, a `_mouse_grab_failed` blacklist, Wayland
specifics, and warnings-not-raises degradation. This is where "it compiles" and
"it works on the user's desktop" diverge, and it cannot be validated in CI.

Decisions to make early:

- **Windows has no control API, and this is the fork's chance to fix it.** The
  Python engine depends on `socket.AF_UNIX` (`hasattr` gate in `control.py`), and
  **stock CPython on Windows never exposes it** (bpo-33408). Java does not
  support AF_UNIX on Windows either, so a faithful port inherits the same gap.
  Since this is a fork, prefer **TCP on 127.0.0.1 with a per-user port and a
  token file**, which fixes WSL+Windows parity instead of inheriting a known
  limitation. This is the one place the fork should deliberately diverge on day
  one — and it should be recorded in `TODO.md` either way.
- **Global hotkeys**: JNA to evdev/uinput on Linux; JNA to `RegisterHotKey` /
  low-level hooks on Windows. Do **not** use `RegisterHotKey` for push-to-talk —
  it cannot observe key-up reliably for a modifier-pair hold. The Python engine
  learned this; the latch semantics (`BaseHotkeyManager.latch_release`, and the
  WSL `LATCH_DOWN`/`LATCH_HOLD`/`HOTKEY_UP` contract) are the specification.
- **Text injection**: keep the existing strategies
  (`type_fast` / clipboard-paste) and their failure modes; the settings and the
  control API verbs already expose the choice.

## 7. Verification strategy — differential, not transliterated

The 9k lines of pytest are a **specification**, not code to rewrite. Do not port
them line by line. Instead:

1. **Differential harness (highest value).** Drive identical scenarios against
   the Python engine and the Java engine through the control API and diff the
   JSON envelopes. Covers config semantics, verb behaviour, post-processing and
   defaults cheaply, and keeps working as both implementations evolve.
2. **Verb-catalogue conformance.** Assert the Java `help --json` matches the
   Python one (24 verbs, same `choices` / `required` / `toggles`). One test,
   catches most surface drift.
3. **ASR parity.** Fixed audio fixtures (reuse `tests/e2e/`): compare transcripts
   (WER/CER against the Python reference) and encoder outputs (tolerance, §3.3).
   A "nearly right" transcript must fail loudly — pin exact strings, not vibes.
4. **`vt-tui` protocol conformance.** Run the real Rust binary against the Java
   socket server and assert the exchange.
5. **HAL contract tests.** `tests/shared/test_hal_contract.py` is written as a
   contract; re-express those assertions in JUnit against the Java HAL.

The Windows/WSL behaviour that CI cannot reach (the PowerShell bridge, latch
semantics, `AF_UNIX` degradation) should be pinned structurally, exactly as
`tests/wsl/*` does today, and flagged as untested host behaviour in `TODO.md`.

## 8. Phases

Effort assumes one experienced developer. Ranges, not promises.

| Phase | Content | Effort | Gate |
| :--- | :--- | :--- | :--- |
| **0. Spike** | §3.3 | 3 weeks (time-boxed) | **GO/NO-GO** on in-process ONNX |
| **1. Contracts** | Document + pin the `vt-tui` protocol; freeze the control API surface; decide the Windows transport | 1–2 weeks | Both protocols have executable specs |
| **2. Pure core** | control API server, config, dictionary, post-processor, model download, `SOURCE.json` | 5–8 weeks | Differential harness green on non-audio scenarios |
| **3. ASR** | Java log-mel, ONNX enc/dec, decode loop, tokenizer, chunking/joining | 6–10 weeks | Transcript parity on the e2e fixtures |
| **4. Audio + HAL (Linux)** | PortAudio capture, VAD/batcher, evdev/uinput hotkeys, injection, clipboard, chimes | 4–6 weeks | Works on a real desktop, Wayland + X11 |
| **5. Windows + WSL** | `SendInput` hotkeys/injection, clipboard, WSL bridge, chosen transport | 6–9 weeks | Real Windows + WSL2 boots |
| **6. Parity + packaging** | Full conformance sweep, `jpackage` installers, docs, release pipeline | 4–6 weeks | Side-by-side with the Python app |

**Total: roughly 6–9 months** for one strong developer to reach parity, with the
ASR spike (Phase 0) as the dominant risk. Phases 2 and 4 can overlap; Phase 5
cannot start before Phase 1's transport decision.

## 9. Risk register

| Risk | Likelihood | Impact | Mitigation |
| :--- | :--- | :--- | :--- |
| ONNX export of the custom Conformer/decoder is not faithfully reproducible | **Medium-high** | Kills pure-JVM ASR | Phase 0 time-box; `AsrEngine` interface; sidecar fallback with the same Java core |
| "Nearly right" ASR (parity 95%, not 100%) | High | Subtle, user-visible regressions | Tolerance-based numeric checks **and** exact-transcript fixtures; never accept eyeballing |
| Linux hotkeys under Wayland/compositor variance | High | Feature is unusable for some users | Port the existing degradation behaviour (warn, don't crash); test on a real desktop, not CI |
| Weights/CDN assumptions drift | Low | First-run download breaks | Reuse §2.3 exactly, including the HEAD pre-check and parts-first ordering |
| Scope creep into rewriting `vt-tui` | Medium | Months of needless work | §2.1 is a hard rule: the Rust frontend is the frontend |
| GraalVM/native-image temptation | Medium | Schedule sink | Ship a jlink/jpackage runtime image; revisit only if distribution demands it |

## 10. Decisions needed before Phase 1

1. **Acceptance bar:** must the Java engine be *behaviourally identical* (same
   transcripts, same JSON, same config bytes), or is "equivalent UX" enough?
   Everything downstream depends on this.
2. **Windows control transport:** TCP-on-loopback (recommended, fixes the known
   `AF_UNIX` gap) or inherit the limitation?
3. **Is the fork long-lived alongside the Python engine, or a replacement?**
   Differentially testing against Python is only possible while Python exists —
   which argues for keeping both alive through Phases 0–6.
4. **Distribution:** `jpackage` per-platform installers, or a bundled runtime
   image? This determines the model-bundle integration (bundled vs. downloaded).
5. **GPU:** CPU-only first is strongly recommended; the Python engine is CPU-only,
   so GPU parity is not required for v1.

## 11. First two weeks, concretely

1. Export the encoder to ONNX and diff it against Python on three audio fixtures
   (2s, 10s, 45s) — the longest one exercises the chunking path.
2. Port the log-mel frontend and prove numeric parity (§3.3 step 0.1). Do this
   *first*: if the Java features differ, every later comparison is noise.
3. Prototype the decoder export with a **fixed-size static cache** and a trivial
   Java greedy loop; transcribe 2s of audio end to end.
4. Write down the `vt-tui` wire protocol from `tui_ratatui.py` + `tui-rs/` while
   reading both, and check it against the real binary.

If step 3 yields the wrong text at the end of week 2, escalate the go/no-go
decision immediately rather than spending the remaining time-box optimistically.
