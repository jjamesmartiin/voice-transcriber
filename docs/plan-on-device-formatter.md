# Plan: On-Device Formatter (local SLM/LLM pass)

**Status:** proposal, ready to implement · written 2026-10-08
**Owner:** James
**Depends on:** the model registry in [`docs/plan-diarization.md`](plan-diarization.md) §6.1
(shared workstream — do that first)
**Related:** [`docs/plan-structured-formatting.md`](plan-structured-formatting.md),
[`docs/formatting.md`](formatting.md), [`docs/cleanup_modes.md`](cleanup_modes.md)

---

## 1. Goal, in one sentence

Close the remaining quality gap to commercial cloud dictation **without anything leaving
the machine**: a bundled small language model that turns a raw ASR transcript into clean
written text — filler and false-start removal, self-correction resolution, correct
capitalisation including **unknown proper nouns**, punctuation, and list/email structure.

## 2. Requirements and non-requirements (as decided)

| | |
| --- | --- |
| **Must** | Be 100 % on-device. No network call, ever. No telemetry. No account. |
| **Must** | Be **optional and off by default**, switchable, and never silently degrade the non-formatter path. |
| **Must** | Never invent content. A hallucinated sentence is worse than an unformatted one. |
| **Must** | Be **nearly identical to what Wispr Flow produces** on the reference fixtures (§4) — measured, not asserted. |
| **Must** | Publish **minimum system requirements** and degrade gracefully below them. |
| **May** | Miss the ≤1.5 s release-to-clipboard SLA — **only for this feature**, explicitly, and only when it is enabled. |
| **Must not** | Become a hard dependency of the dictation path. With the formatter off, the engine is byte-identical to today. |
| **Must not** | Break `cleanup_mode: off` ("nothing is deleted") or the typing-safety rule (never inject `Enter`). |

## 3. Why this is needed — measured, not asserted

Run against the current post-processor on `main` (`skip_slm=True`), with the same utterances
that were played into the Wispr Flow demo. Left column is what Wispr literally returned over
its websocket; right column is this app today.

| Case | Wispr Flow (measured) | Voice Transcriber (measured) | Gap |
| --- | --- | --- | --- |
| Self-correction | `Hey Michelle, meet me at my apartment lobby at 7 pm.` | `Hey michelle, meet me at my apartment lobby at six PM, actually no, seven PM.` | **retraction unresolved, number unformatted** |
| Proper nouns | `Hi Nora, … Best, Jacob` | `Hi nora, … Best, jacob.` | **unknown proper nouns lowercased** |
| Email layout | `Hi Nora,\n\nI am looking forward to…\n\nBest,\nJacob` | `Hi nora, … Best, jacob.` | no paragraph structure |
| List | `<ol><li>milk for the cake</li>…</ol>` | flat line | comma enumerations undetected |

**The casing gap is the one rules cannot close.** Casing today is a whitelist
(`TECHNICAL_ACRONYMS_AND_PROPER_NOUNS`). `Michelle`, `Nora` and `Jacob` are not in it, so
they are lowercased — and no regex can know that "nora" should be "Nora". That is a
language-model judgement, and it is the clearest justification for this workstream.

Two of the four gaps (retraction, list) are being closed by the deterministic work in
`docs/plan-structured-formatting.md`; the formatter must not duplicate that, it must
**extend** it to the cases rules cannot reach.

## 4. The acceptance criterion: "nearly identical to Wispr Flow"

This has to be falsifiable or it is marketing. Definition:

> For each fixture in the reference set, the app's final rendered output must equal Wispr
> Flow's output after (a) normalising layout representation — their `<ol>`/`<li>` markup is
> equivalent to our `- ` bullets or numbered items — and (b) collapsing whitespace.

The reference set is the four utterances already exercised against the demo, with Wispr's
exact recorded outputs as the golden file (regenerate with the TTS recipe below, then
replay; the transcriptions are deterministic — three identical runs produced byte-identical
output, 15 tokens, 0.146–0.156 s):

| id | spoken | Wispr reference output |
| --- | --- | --- |
| `list` | "I want to grab three things at the grocery store: milk for the cake, eggs for breakfast, and white bread." | `I want to grab three things at the grocery store:` + `<ol><li>milk for the cake</li><li>eggs for breakfast</li><li>white bread</li></ol>` |
| `retraction` | "Hey Michelle, meet me at my apartment lobby at six PM, actually no, seven PM." | `Hey Michelle, meet me at my apartment lobby at 7 pm.` |
| `email` | "Hi Nora, I am looking forward to working with you. Are you available to meet at three PM on Friday? Best, Jacob." | `Hi Nora,` + blank line + body + blank line + `Best,` + `Jacob` |
| `long` | "Plan a week long itinerary for a trip to Italy…" | one paragraph, `week-long` hyphenated, sentence-cased |

TTS recipe (reproducible, tiny, no downloads beyond `espeak-ng`):
`nix run nixpkgs#espeak-ng -- -v en-us -s 155 -w <out>.wav "<text>"`.

Record in `eval/` as a `formatting` slice: `input transcript -> expected output`, so this is
a **regression test**, not a one-off comparison. Note the `long` fixture has a deliberate
ASR mishearing (`nightlife` → `hikes`) — keep it, it is a useful reminder that the formatter
must not "fix" content, only layout.

## 5. Model choice

### 5.1 DECIDED (2026-10-08): ship `superwhisper/s1-mini`, behind a swappable backend

The decision, recorded: **ship S1-mini now to prove the pipeline end to end, and keep it
modular so it can be swapped later.** The attribution debt is accepted as a temporary cost;
moving to an in-house fine-tune is a follow-up, not a blocker.

That makes *swappability* a first-class requirement, not a nicety — see §5.2.

| | |
| --- | --- |
| Base | Qwen3-0.6B, fine-tuned for exactly this one task (**not a chat model**) |
| Size | **462 MiB** Q4_K_M GGUF; BF16 safetensors 1.50 GB |
| Params | 596M unique (28 layers, GQA 16 Q / 8 KV), embeddings tied |
| Licence | Apache-2.0 **+ one additional naming term** (see §5.5) |
| Quality | 94.8 % token accuracy, 7,519 held-out English cases, greedy, measured on Q4_K_M |
| Does | fillers, false starts, self-corrections, punctuation, capitalisation, spoken numbers / dates / times / currency / emails, **Markdown lists**, **email layout** |
| Limits | English only (v1); **≤ ~1,000 input tokens**, chunk longer at sentence boundaries |
| Pinning | Releases are **tagged** — pin `revision="v1"` so upstream cannot move under us |

### 5.2 Swappable by design (the explicit requirement)

Mirror the ASR backend registry, which exists in this repo precisely because hardwiring one
model caused problems the first time (`transcribe2.BACKENDS`, and the same fix recorded in
`docs/TODO.md`). The formatter gets the identical shape:

```python
# src/voice_transcriber/formatter.py
BACKENDS: dict[str, str] = {
    "s1-mini":      "voice_transcriber.formatters.s1_mini",       # bundled GGUF, default
    "llama-server": "voice_transcriber.formatters.llama_server",  # external endpoint
    "noop":         "voice_transcriber.formatters.noop",          # identity, for tests
}
DEFAULT_BACKEND = "s1-mini"
REQUIRED_FUNCTIONS = ("format_text", "available", "warm")
```

Every backend implements the same three-function contract and nothing else:

```python
def available() -> bool: ...                          # are the weights / endpoint present?
def warm() -> None: ...                               # optional preload; must not raise
def format_text(text: str, *, style: str, structure: str, context: str,
                timeout_s: float = 3.0) -> str: ...   # the ONLY place a model is called
```

Three consequences worth stating, because they are the point of doing it this way:

1. **The swap is a config value**, not a refactor: `formatter_model` resolves through the
   model registry (`docs/plan-diarization.md` §6.1), so a future in-house fine-tune is a new
   `ModelSpec` + one backend module.
2. **`noop` makes the shared test tier work with no weights**, and makes it possible to
   prove the plumbing before any model exists.
3. **A backend that fails is a fallback, not an outage** — any exception, timeout or invalid
   output falls back to the deterministic text (§8).

The `llama-server` backend matters as a stepping stone: it is OpenAI-compatible, and
`process_slm_llm_rewrite()` **already** has a working client for that shape. Milestone M2
uses it to prove the model end-to-end before any new integration is written.

### 5.3 The exact integration contract (from the model card)

Get any one of these wrong and the output is garbled or **completely blank**. Every item
below is a pinned test, not a comment.

**The system prompt is mandatory and verbatim.** Its exact wording:

```
You are a text normalizer for speech-to-text transcripts. The input begins with a control
line specifying the styling, structure, and context settings; clean the transcript to match
those settings and output only the cleaned text.
```

**The user message is a control line, a newline, then the raw transcript**, in exactly this
shape:

```
[Styling: <styling>] [Structure: <structure>] [Context: <context>]
<raw ASR transcript>
```

| Axis | Values | Meaning | Maps from |
| --- | --- | --- | --- |
| Styling | `casual`, `semi-casual`, `semi-formal`, `formal` | register: capitalisation, apostrophes, contraction expansion | new `formatter_style` setting |
| Structure | `prose`, `lists` | may the model emit Markdown bullets (needs ≥3 real items) | **the existing `structure_mode`** |
| Context | `general`, `email` | email turns on greeting / body / sign-off blocks | new `formatter_context` setting |

The three axes are independent and every combination was trained.

**`enable_thinking=False` is REQUIRED.** This is the highest-value line in this whole plan.
The chat template comes from Qwen3, which enables thinking mode by default; S1-mini was
trained with it off. Without the flag the model emits an empty `<think>` block and stops,
and the model card calls this *"the single most common way to get a blank result from this
model"*. In llama.cpp that means `--jinja --chat-template-kwargs '{"enable_thinking":false}'`.
**Do not substitute `--reasoning-budget 0`** — the card explicitly says it suppresses the
think block a different way and degrades the output.

**The rendered assistant prefix is literally**
`<|im_start|>assistant\n<think>\n\n</think>\n\n` — two newlines inside the think block and
two after it. If we ever build the prompt by hand instead of via the template, this is the
string to match.

**Decoding and sizing rules:**

* Greedy only — `do_sample: false` ships in `generation_config.json`; temperature 0 if overridden.
  Normalisation is a deterministic transformation and sampling only adds variance.
* `max_new_tokens = 1.3 × input_tokens + 32`. This bounds both runaway output **and** latency,
  and it supersedes a generic length cap.
* Input ≤ ~1,000 tokens; chunk longer transcripts **at sentence boundaries**.
* **An empty string is a valid result.** Filler-only input ("um") correctly returns nothing;
  the pipeline must treat that as success, not failure. §8 rule 5 turns this into a precise
  test: empty output is accepted only when the input had no content words.
* The model will not follow general instructions. It is a text transformation, not an assistant.

### 5.4 Still rejected for the formatter

* **A generic untuned instruct model** (Qwen2.5-1.5B, Llama-3.2, Gemma-3, Phi-4-mini…).
  Measured, not stylistic: the un-tuned 4-bit 2B base scored **0.0 % on held-out
  self-correction** versus 100 % after fine-tuning, and produced a "self-correction
  inversion" that keeps the *replaced* side. A generic model also paraphrases, which breaks
  the one promise this app makes.
* **BERT-class restorers** (`oliverguhr/fullstop-*`, `Horizon-Labs/punctuation-restoration-*`,
  `1-800-BAD-CODE/xlm-roberta_punctuation_fullstop_truecase`). Cheap (10–270 MB, 10–400 ms)
  but punctuation/truecase only; no corrections, no structure. Keep in mind as a
  low-RAM fallback tier, not a formatter.
* **Disfluency deletion taggers** (`stillerman/fdt-disfluency-*`). Elegant — KEEP/DELETE by
  construction, so it cannot hallucinate — but trained partly on CC BY-NC-SA data and the
  card says research use only.
* **The existing external vLLM path as the product answer.** `enable_slm` already talks to
  `VT_VLLM_URL` (default `http://localhost:8000`), which is a server the *user* must run.
  It stays as the power-user option and as the M2 stepping stone — not the shipped default.

### 5.5 What shipping S1-mini obliges us to do

The licence is Apache-2.0 **plus one additional term**, so these are requirements, not
courtesy:

1. Retain the full licence text → commit it to `config/licenses/` and add the path to the
   release NOTICE, exactly as the Cohere model already does.
2. Retain the upstream `NOTICE` file.
3. State significant changes if we redistribute a modified version (we will not — ship the
   upstream GGUF byte-for-byte and verify its digest through the model registry).
4. **Keep the name "S1-mini" by "Superwhisper" with that exact capitalisation wherever the
   model is named** — the app's licence/about surface, `README.md`, and the model-download
   prompt. This is the term that credits a competitor; do not paraphrase it, and do not
   quietly omit it.

Sanity-check the result against the parity gate in §4 before believing it works.

## 6. Runtime

**llama.cpp via `llama-cpp-python`** — MIT, GGUF, CPU-first with optional CUDA/Metal/Vulkan,
embeddable in Python. `llama-server` is OpenAI-compatible, so the **existing**
`process_slm_llm_rewrite()` HTTP client can be pointed at it unchanged — that is the M2
stepping stone, not the destination.

**Verified against the local nixpkgs checkout, so the packaging question is closed:**

| What | Where | Notes |
| --- | --- | --- |
| `llama-cpp-python` | `pkgs/top-level/python-packages.nix:9936` → `pkgs.python3Packages.llama-cpp-python` | v0.3.23, built from source with `scikit-build-core`; deps `diskcache`, `jinja2`, `numpy`, `typing-extensions` |
| `llama-cpp` (native, + server binary) | `pkgs/by-name/ll/llama-cpp` | `cudaSupport`, `metalSupport`, `rocmSupport`, `vulkanSupport` variants all exist |
| `sherpa-onnx` (for workstream D) | `pkgs/by-name/sh/sherpa-onnx` + `python3Packages.sherpa-onnx` | see the diarization plan |

So the build change is **two lines in `flake.nix`'s `pythonEnv`**, plus the matching
`pyproject.toml` entry:

```nix
pythonEnv = python.withPackages (python-pkgs: with python-pkgs; [
  # … existing …
  llama-cpp-python      # formatter runtime
  sherpa-onnx           # diarization runtime (workstream D)
]);
```

Three caveats worth knowing before you promise a date:

* **`llama-cpp-python` is compiled from source**, which adds build time and closure size, and
  the derivation sets `GGML_NATIVE=off` for reproducibility — meaning **no `-march=native`**,
  so CPU inference will be a little slower than a hand-built binary. Measure, do not assume
  (§9 estimates are extrapolations).
* **CUDA is the standard Nix mechanism** (`config.cudaSupport`), which affects the whole
  closure — the same open question already tracked in `docs/TODO.md` for a CUDA AppImage.
* **Windows does not use the flake.** The PyInstaller build needs its own answer: both
  projects publish Windows wheels, but confirm `llama-cpp-python`'s wheel covers the target
  Python, and record anything unverified in `docs/TODO.md` per the repo convention.

Nothing else changes: **the Nix and AppImage builds never carry weights**, so a GGUF arrives
through the existing download path exactly like the Cohere safetensors.

Do **not** import `llama_cpp` at module import time. Same rule the ASR backends follow: a
model-free test run must never pay for it.

### 6.1 Subprocess first — because the bindings cannot do runtime CPU dispatch

**A finding that reverses the intuitive choice, and contradicts an earlier draft of this plan.**

llama.cpp can build *runtime* CPU dispatch (`GGML_CPU_ALL_VARIANTS` + `GGML_BACKEND_DL`) so that
**one artifact runs fast on both old and new CPUs** — the alternative is picking an instruction
baseline and either SIGILLing on old hardware or crawling on new. **`llama-cpp-python` cannot
use it.** Those variants are loaded by `ggml_backend_load_all()`, the Python bindings never
expose it, and a dynamic-backend build therefore fails at model load with *"no backends are
loaded"* — an open upstream issue (#2069), not a configuration mistake.

The portability situation under Nix is subtler than it looks, and both halves matter:

| Build | `GGML_NATIVE` | Effective ISA | Runs on a pre-AVX2 CPU? |
| --- | --- | --- | --- |
| **Nix** (`llama-cpp-python`) | `off`, and Nix sets `SOURCE_DATE_EPOCH` | **baseline x86-64 (SSE2)** | **Yes** — but slow |
| plain `pip` with `-DGGML_NATIVE=OFF` | `off`, no `SOURCE_DATE_EPOCH` | AVX2/FMA/F16C on | **No** — SIGILL |

nixpkgs' own comment notes AVX2 can be **"13x faster compared to NixOS's x86_64 defaults"**, so
the Nix in-process artifact is *portable and slow* — potentially by an order of magnitude.

| Option | Portable? | Fast? | Verdict |
| --- | --- | --- | --- |
| In-process, Nix default | yes | **no** (SSE2 baseline) | works, poor perf |
| In-process, `ALL_VARIANTS`+`DL` | — | — | **broken** — bindings can't load backends (#2069) |
| In-process + `ctypes` call to `ggml_backend_load_all_from_path()` | unverified | unverified | plausible workaround, **unproven**; not something to build a release on |
| **`llama-server` subprocess** | **yes** | **yes** | **nixpkgs' `pkgs.llama-cpp` already sets `cpuArchDynamicDispatch = true`,** and llama.cpp's own server calls the loader the bindings lack |

**Decision: `llama-server` as a subprocess is the primary shipping path.** It is the only option
that is both portable and fast today, it needs no patching, and it happens to solve the
cancellation problem too (kill the process — see §6.3). Windows can use llama.cpp's **official
prebuilt `llama-server.exe`** zips rather than compiling, though note those zips are
`ALL_VARIANTS`+`DL` builds and are therefore a drop-in for the **server**, *not* for the
bindings.

Keep the in-process backend in the registry as the fallback and the lower-overhead option for
users who build from source with a native ISA — the whole point of §5.2 is that this is a config
choice, not a fork. But **do not ship in-process as the default** until the perf gap is measured
on real hardware.

This also reuses existing code: `process_slm_llm_rewrite()` already speaks the
OpenAI-compatible HTTP API `llama-server` serves, so the M2 stepping stone and the shipped
default are the same wire protocol.

### 6.2 Controlling the prompt exactly (this is where integrations break)

The chat template lives in the GGUF's `tokenizer.chat_template` metadata, and
`llama-cpp-python` reads it into a Jinja formatter. Two ways to keep S1-mini's trained format
byte-exact:

* Preferred: pass `chat_format="chat_template.default"` so the metadata template is used
  rather than a guessed builtin (`chatml` / `llama-3` / …) — the guess is a real failure mode
  given this model's strictness.
* Escape hatch: `create_completion(prompt=<list of token IDs>)` **skips tokenisation and the
  template entirely**. If the template fights us — particularly over the
  `enable_thinking=False` empty `<think>` block — render the prompt ourselves and feed raw
  token IDs. Pin whichever path we choose with a test asserting the exact rendered prefix
  from §5.3.

For the subprocess path the equivalents are `--jinja --chat-template-kwargs
'{"enable_thinking":false}'`, and `POST /apply-template` to inspect the rendered prompt
without running inference — which is the fastest way to debug a blank-output report.

### 6.3 Determinism, cancellation, and GGUF packaging

* **Cancellation differs by backend, and the subprocess path is simpler.** In-process has *no*
  `timeout=` parameter — it must be a `StoppingCriteria` callable returning `True` past a
  deadline (checked each step, works when streaming), with worker-thread abandonment as the
  backstop. With `llama-server` you just terminate and optionally restart the process, which is
  cruder but robust. §8 rule 6 applies either way.
* **`temperature=0.0` is true greedy** in `llama-cpp-python` (it branches to `add_greedy()`, so
  the seed is irrelevant). The default seed is *random*, so if the temperature is ever non-zero
  the seed must be pinned. Also pin `top_k`, `top_p`, `min_p` and keep penalties neutral
  (`repeat_penalty=1.0`). Server equivalents: `--temp 0 --seed N --top-k 0 --top-p 1
  --repeat-penalty 1.0`. Bit-identical output is only guaranteed for a fixed build + device, so
  tests must not assert exact strings across machines.
* **`max_tokens` defaults differ and bite:** `create_completion` defaults to **16** tokens,
  `create_chat_completion` to **unlimited**. Always pass the §5.3 ceiling explicitly.
* **Model loading is cheap; the first prefill is not.** With mmap, load is near-instant — the
  cost lands on first-token latency as page faults. The bindings have **no auto-warmup** (the
  server has `warmup=true` by default), so in-process must construct in a worker thread and
  immediately run one `max_tokens=1` completion to pre-fault the pages. That is what makes the
  user's first real dictation warm instead of 10× slow.
* **Pin the runtime to the model.** GGUF v3 is a real contract (the reader accepts v2/v3, rejects
  v0/v1 and anything newer), but the practical failure is *architecture or quantization support*
  in the runtime, not the version number. Pin `llama-cpp-python==0.3.23` — which pins a specific
  vendored llama.cpp commit — alongside a GGUF pinned by immutable revision + sha256 in our own
  release, and never let the runtime upgrade independently of the model.
* **PyInstaller (Windows/Linux) — three verified traps:**
  1. There is **no official PyInstaller hook** for `llama_cpp`; use
     `collect_dynamic_libs("llama_cpp")`, which preserves the `llama_cpp/lib` destination.
  2. **Linux: the default glob is wrong.** A manylinux wheel ships `libllama.so`,
     `libllama.so.0` *and* `libllama.so.0.x.y` as separate real files; the default `lib*.so`
     pattern matches only the unversioned names, so the bundle dies at runtime with
     *"cannot open shared object file: libggml.so.0"*. Add `lib*.so.*` to `search_patterns`.
  3. **macOS: ship per-arch, not universal.** `llama-cpp-python` force-enables Metal, and in a
     universal build the Intel guard is skipped, leaving AVX2/FMA on in the x86_64 slice — which
     can SIGILL on an older Intel Mac.
* **Packaging is easy for this model.** 462 MiB is far below GitHub's **2 GiB per-asset** cap, so
  no splitting is needed. For anything larger: quantized GGUF and fp16 safetensors are
  **essentially incompressible**, so the existing `xz`-per-part step buys ~nothing and must not
  be relied on to fit a cap — use `llama-gguf-split --split --split-max-size 2G`, which beats the
  safetensors path because llama.cpp loads a split GGUF by pointing at the first shard alone.
  And **do not bundle a GGUF inside a PyInstaller one-file build** — that re-extracts the whole
  payload to `_MEIPASS` on every launch.
* **Licence notices to carry:** llama.cpp and llama-cpp-python are both **MIT** (include both
  texts plus the vendored `vendor/llama.cpp/LICENSE`); the bundled OpenMP runtime (`libgomp`)
  needs its notice; **OpenBLAS is BSD-3 *with* a binary-redistribution notice clause** — but
  nixpkgs does **not** enable BLAS by default, so the default artifact avoids it. CUDA would add
  NVIDIA's EULA terms; only ship GPU backends if we actually support them.

## 7. Architecture

```
ASR text
   │
   ├─ deterministic cleanup            (EXISTS — cleanup_mode, retraction, dictionary, numbers)
   │
   ├─ FORMATTER  (NEW, optional)       raw text -> cleaned text, same contract as today:
   │                                   a string in, a string out; model-free when disabled
   │
   ├─ structure stage                  (EXISTS — process_structure_blocks)
   │
   └─ renderer                         (EXISTS — punctuation presets, structure modes)
```

Placement rules, and each one is a hard requirement rather than a preference:

1. **The formatter runs after deterministic cleanup and before the structure stage.** The
   rules are free and deterministic; there is no reason to spend tokens on what a regex
   already does, and the model is more likely to be right about casing than about a
   dictionary entry the user configured explicitly.
2. **The formatter must never run when `cleanup_mode: off`.** That mode promises "nothing is
   deleted", and the formatter's whole contract is deletion of fillers and false starts.
   `docs/cleanup_modes.md` §4 already states this for the SLM pass; keep it true.
3. **The formatter must not fight the structure setting.** If `structure_mode: off`, the
   formatter's list markup is stripped to prose. If `structure_mode: blocks` and the output
   mode is typing, the existing downgrade to `inline` still applies — the formatter never
   gets to inject an `Enter`.
4. **Every formatter output passes validation before it is used** (§8). This is the module's
   most important function.

### 7.1 Settings

| Key | Values | Default | Notes |
| --- | --- | --- | --- |
| `formatter` | `off`, `on` | `off` | `off` never loads the model. |
| `formatter_model` | backend/model name | `s1-mini` | Resolves through the model registry (§6.1 of the diarization plan) — **this is the swap point**. |
| `formatter_style` | `casual`, `semi-casual`, `semi-formal`, `formal` | `semi-formal` | The `[Styling: …]` axis. Its own setting, deliberately **not** derived from the punctuation preset, so neither can clobber the other (hazard H2 in `plan-structured-formatting.md`). |
| `formatter_context` | `general`, `email` | `general` | The `[Context: …]` axis. The destination-aware hook, manual for now. |

**`structure_mode` is the third axis** — do not add a fourth setting for it. The existing
values map straight onto the model's `[Structure: …]` axis, which is much better than
letting the model emit bullets and stripping them afterwards:

| `structure_mode` | control line sent |
| --- | --- |
| `off` | `[Structure: prose]` |
| `inline` | `[Structure: lists]` (the renderer downgrades newlines for typing, as today) |
| `blocks` | `[Structure: lists]` |

The post-processing order still holds: the model is told what shape to produce, and then the
**existing** structure stage and punctuation preset remain the final renderers. If the model
emits a bullet under `structure_mode: off`, that is a guardrail failure (§8), not something
to silently strip — stripping would hide a prompt that no longer matches the trained format.

Unknown value → **`off`** (fail safe), unlike `cleanup_mode` which fails to the shipped
behaviour. A malformed model id follows `_load_model_backend`'s pattern: warn, name the
default, carry on.

Every key must land in all the surfaces enumerated in
[`docs/plan-diarization.md`](plan-diarization.md) §6 (config, env `VT_FORMATTER*`,
`DEFAULT_SETTINGS`, setters, `control.VERBS` + `status`, both TUI modals including the
mirrored Rust literals and its grep guard, `tests/shared/`, docs, `CHANGELOG.md`).
**`help --json` is the catalogue; do not hardcode verb lists in docs.**

## 8. Guardrails (the part that must not be got wrong)

The model is the only component in this codebase that can *invent* text. It therefore needs
the strictest validation in the codebase. Existing precedent to build on:
`_sanitize_slm_output()`, `_is_valid_speech_rewrite()` (≥35 % word-overlap floor, refusal
detection, >2.5× length rejection) and the exponential backoff in `post_processor.py`.

Required, in order:

1. **Length bound.** Output token count within `1.3 × input_tokens + 32` (the model's own
   recommended ceiling, which also bounds latency). Catches runaway generation and
   summarisation.
2. **Content bound.** Every *content* word in the output must appear in the input, allowing
   only an explicit whitelist of transformations: punctuation, capitalisation, number/date
   normalisation, and the removal of fillers/corrections. This is the "no new content" rule
   and it is what makes "resolve corrections but never write new sentences" enforceable.
3. **List-item provenance.** If the output contains list items, **every item's text must
   appear verbatim in the input**. A model that invents a plausible extra grocery item is
   exactly the failure mode that would destroy trust in the feature.
4. **Refusal/meta detection.** Reject "I'm sorry…", "Here is the cleaned text:", any
   `<cleaned_text>` wrapper leakage, or markdown fences.
5. **Empty-input behaviour.** Filler-only input must produce empty string — that is the
   correct output, not a failure. The rule, precisely: **empty output is accepted only when
   the input contained no content words** (after filler removal). Empty output for an input
   that had real content is a guardrail failure and falls back to the unformatted text.
6. **Latency bound.** A hard deadline after which the original text is used. This is *not* a
   `timeout=` kwarg — `llama-cpp-python` has none (§6.3). Implement it as a `StoppingCriteria`
   callable that returns `True` past the deadline, with worker-thread abandonment as the
   backstop. A formatter stall must never cost a user their dictation.
7. **Fail open, always.** Any exception, timeout, invalid output, or missing model →
   return the cleaned-but-unformatted text. The feature can fail; the dictation cannot.

Also: **never log transcript content** when the formatter is on, keep the posture that
nothing is written to disk, and do not let the model output reach the stats file.

## 9. Hardware requirements and graceful degradation

Documented in the README next to the existing model-download guidance. Baseline is the
Cohere ASR (~2 GB int8 resident) **plus** the formatter:

| Tier | System RAM | Expected experience |
| --- | --- | --- |
| **Below minimum** | < 8 GB total | The formatter should refuse to enable, with a clear message and the measured requirement. Dictation is unaffected. |
| **Minimum** | 8 GB | Works. ~1–3 s added latency per utterance on CPU. Ships **off** by default. |
| **Recommended** | 16 GB | Comfortable; the intended configuration. |
| **GPU** | ≥ 4 GB VRAM | Decode offloaded via llama.cpp CUDA/Metal; near-instant, well inside the dictation SLA. |

Throughput estimates (memory-bandwidth-bound, extrapolated from measured 7B/13B
`llama.cpp` figures — **treat as estimates and measure on real hardware**, per §11):

| Model class | Q4 size | ~CPU tok/s (modern dual-channel desktop) | ~latency, 150-token output |
| --- | --- | --- | --- |
| 0.6B (s1-mini) | 484 MB | ~80–150 | ~1–3 s |
| 1.5B | ~1.1 GB | ~40–70 | ~2–4 s |
| 2B | ~1.3 GB | ~35–60 | ~3–4 s |
| 4B | ~2.5 GB | ~18–30 | ~5–8 s |

**The SLA exemption, stated precisely:** the ≤1.5 s gate in
`docs/agent_testing_workflow.md` applies to the **default** configuration. With
`formatter: on` the release-to-clipboard budget is instead "ASR time + formatter time", and
`status` must report the formatter's contribution separately so the regression is visible
rather than mysterious. `tests/benchmark_synthetic_e2e.py` already separates `asr_ms` from
`tech_c_ms` — extend that pattern rather than inventing a new one.

GPU detection should reuse the existing device handling rather than adding new platform
code; if a GPU is present, offload layers; if not, CPU with a thread count consistent with
`VT_CPU_THREADS`.

## 10. Milestones

| M | Content | Exit criteria |
| --- | --- | --- |
| **M0** | ~~Decide A/B/C~~ **DECIDED: option A (S1-mini), swappable backend** (§5.1). Commit the licence text and NOTICE to `config/licenses/`, and set the model registry entry to pin `revision="v1"`. | licence + NOTICE committed; the naming clause is honoured with exact capitalisation on every surface that names the model |
| **M1** | Model registry generalisation (shared with diarization D1) | a second `ModelSpec` resolves; Cohere unaffected; `tests/shared` green |
| **M2** | Bridge: run the GGUF behind `llama-server` and point the **existing** `VT_VLLM_URL` client at it | the four §4 fixtures produce non-empty, sane output — proves the pipe end-to-end before writing any new integration |
| **M3** | `formatters/llama_server.py` — the **shipped** backend: spawn/attach, process lifecycle, kill-on-timeout, guardrails §8 | **code done** — model-free tests green against a stub server; every guardrail has a failing-input test; *a warm server answers within the §9 budget* is the outstanding half and needs the weights (M2/M6) |
| **M3b** | `formatters/s1_mini.py` — the in-process backend (registry fallback) | works, and its throughput is **measured against the subprocess path** — this measurement decides which one is the documented default |
| **M4** | Settings end-to-end (§7.1) | `off` byte-identical to today; both TUIs; `help --json`; `status` reports configured vs effective |
| **M5** | Model bundle published + first-use download prompt (size stated) | clean machine → enable formatter → works offline afterwards |
| **M6** | `eval/` formatting slice + the §4 parity gate + latency numbers | parity measured on all four fixtures; latency recorded per hardware tier |
| **M7** | Docs: spec doc, README requirements table, `architecture.md`, `TODO.md` notes | an agent can enable, measure and debug it from the docs alone |

M2 is deliberately first among the implementation steps: it gives a measurable result in a
day using code that already exists, and it de-risks everything after it. Because §6.1 moved the
shipped path to the subprocess, **M3 is now the real backend rather than a stepping stone** —
and M3b exists only to keep the in-process option honest rather than assumed.

## 11. Verification

| Layer | What is pinned | Tier |
| --- | --- | --- |
| Unit, model-free | All seven guardrails in §8, each with an adversarial input: invented sentence, invented list item, summarised output, refusal text, fence leakage, over-length, timeout. Plus `cleanup_mode: off` suppressing the formatter, and `structure_mode: off` stripping its lists. | `tests/shared/` |
| Unit, model-free | A **fake formatter** implementing the same interface, so the whole pipeline is testable with no weights and no `llama_cpp` import. | `tests/shared/` |
| Regression | `formatter: off` → output byte-identical to today. Non-negotiable. | `tests/shared/` |
| Parity | The four §4 fixtures against the recorded Wispr outputs, with the layout-equivalence rule. | `eval/` |
| Quality | Re-run the existing 154-clip eval with the formatter on and compare WER/CER to the baseline in `eval/results.json`. **The formatter must not make WER worse** — it is allowed to change layout and casing, not words. | `eval/` |
| Latency | ASR vs formatter time reported separately for each hardware tier. | `tests/benchmark_synthetic_e2e.py` |
| Platform | Linux first; Windows/WSL inherit the same path but need the memory footprint measured. | `docs/TODO.md` |

## 12. Risks

| Risk | Mitigation |
| --- | --- |
| **Hallucination** — the model writes text the user never said | §8 guardrails, especially list-item provenance and the no-new-content rule. Fail open. |
| **Competitor attribution** (§5.2) | Decide before M0 is closed; option C is the exit. |
| Self-reported 94.8 % accuracy does not hold on real audio | Validate on `eval/` **before** committing to the model (§M6); it is English-only v1. |
| Prompt sensitivity — the card warns that deviating from the trained template garbles output | Send the exact trained control line; pin it with a test; do not let users free-form the system prompt. |
| Latency regression perceived as a bug | Off by default, documented requirement table, `status` reports the split, and the SLA exemption is written down rather than implied. |
| Two more models bloat the download | Diarization (~40 MB) and the formatter (~484 MB) are **separate bundles**, each opt-in, each with its size stated before download. Nothing downloads during a dictation. |
| Model thrash on low-RAM machines (ASR + formatter resident together) | Document the minimum; refuse to enable below it rather than swapping and stuttering. |

## 13. Open questions

1. **§5.2 — which option?** This gates M0 and everything after it.
2. **Should the formatter ever be default-on for users who have the RAM?** Tempting for
   parity; contradicts "off by default" and the SLA. Recommendation: still off, but offer it
   in first-run setup with the measured RAM headroom shown.
3. **Where does `formatter_context` come from** — a manual setting only, or eventually the
   active-application detection that `docs/plan-structured-formatting.md` leaves open? Manual
   first; context detection is its own plan with a privacy decision attached.
4. **Do we ever fine-tune on the user's own corrections?** Doing so would beat every cloud
   product at the user's own vocabulary — and it is also the one thing that could make this
   app *more* private-violating than the cloud if done carelessly. If pursued, it must be
   local-only, opt-in, and deletable. Out of scope for this plan; worth its own document.
