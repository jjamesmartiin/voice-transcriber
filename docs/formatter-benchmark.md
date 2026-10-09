# On-device formatter — S1-mini end-to-end benchmark (C2 / M2)

**Measured:** 2026-10-09 · **branch:** `work/c2-e2e` · **owner:** milestone C2/M2
**Plan:** [`docs/plan-on-device-formatter.md`](plan-on-device-formatter.md) §4 (acceptance),
§5.3 (prompt contract), §8 (guardrails), §9 (latency budget).
**Conclusion up front:** the model works and answers every fixture correctly, and a warm
`llama-server` costs **0.13–0.44 s** per utterance. But **two bugs in files this milestone
does not own** stop three of the four fixtures from reaching the user: the guardrail
false-rejects the model's `7pm` / `3pm`, and the structure stage double-bullets its lists.
The `long` fixture is byte-identical to Wispr.

Everything below was measured on this machine against the real quantised GGUF, through
`post_processor.clean_speech_transcription()` with the formatter enabled — not by calling
the backend directly except where the row says so.

---

## 1. Hardware and runtime

| | |
| --- | --- |
| CPU | AMD Ryzen AI 9 HX PRO 370 w/ Radeon 890M — 12 cores / 24 threads |
| RAM | 54.6 GiB (`MemTotal 57211672 kB`) |
| OS | NixOS 26.05pre, kernel `7.2.0` |
| `llama-server` | `/run/current-system/sw/bin/llama-server`, `version: 0.2.0-dev`, built with GCC 15.3.0 |
| Python | 3.13.12 (from `nix develop`) |
| Model | `~/.local/share/vt/models/formatter/s1-mini-q4_k_m.gguf`, 484,219,808 bytes, sha256 `3b41ebe2502cbd03e811d5d16b022f5ab551eda58d62597d152f89535003c634` (re-verified) |
| Threads | `VT_FORMATTER_THREADS` unset → `os.cpu_count() // 2` = **12** |

The server was launched with the backend's own command shape (see
`formatters/llama_server.py:_build_command`), which is what the spawn path uses:

```bash
llama-server \
  -m ~/.local/share/vt/models/formatter/s1-mini-q4_k_m.gguf \
  --host 127.0.0.1 --port 18080 \
  -c 4096 -t 12 \
  --jinja --chat-template-kwargs '{"enable_thinking":false}' \
  --temp 0 --seed 0 --top-k 0 --top-p 1 --repeat-penalty 1.0 \
  --no-webui
```

To drive the *real* pipeline (attach mode), the harness sets
`post_processor.set_formatter_settings(enabled=True, model="llama-server", style="semi-formal", …)`
and `set_cleanup_mode("full")`, then calls `clean_speech_transcription(text,
skip_slm=True, cleanup_mode="full", structure_mode=…, )` with `VT_FORMATTER_SERVER_URL`
pointing at the warm server. The reproducible, self-contained equivalent lives in the test
added in §7, and the essential shape is:

```python
import os, sys; sys.path.insert(0, "src")
os.environ["VT_FORMATTER_SERVER_URL"] = "http://127.0.0.1:18080"
import post_processor as pp
pp.set_cleanup_mode("full")
pp.set_formatter_settings(enabled=True, model="llama-server",
                          style="semi-formal", context="general")
out = pp.clean_speech_transcription(RETRACTION_IN, skip_slm=True,
                                    cleanup_mode="full", structure_mode="off")
```

The pinned, repeatable version of the same thing is the test added in §7:

```bash
nix develop --command python -m pytest tests/e2e/test_formatter_e2e.py -v
```

---

## 2. The four §4 fixtures: output vs Wispr

Pipeline settings for each row: formatter **on**, `style=semi-formal`, `cleanup_mode=full`,
`structure_mode` and `context` as noted. `[Structure: …]` is the control line the backend
sent the model (§5.3 mapping).

| id | settings | Wispr Flow reference | model candidate (backend) | rendered pipeline output | matches Wispr? |
| --- | --- | --- | --- | --- | --- |
| `retraction` | `structure=off`, `context=general` | `Hey Michelle, meet me at my apartment lobby at 7 pm.` | `Hey Michelle, meet me at my apartment lobby at 7pm.` | `Hey michelle, meet me at my apartment lobby at six PM, actually no, seven PM.` | **no** — candidate rejected by guardrail, pipeline fell back |
| `list` | `structure=blocks`, `context=general` | `I want to grab three things at the grocery store:` + `- milk for the cake` / `- eggs for breakfast` / `- white bread` | `…:\n- Milk for the cake\n- Eggs for breakfast\n- White bread` | `…:\n\n- - Milk for the cake\n- - Eggs for breakfast\n- - White bread` | **no** — double bullets from the structure stage |
| `email` | `structure=off`, `context=email` | `Hi Nora,` ⏎⏎ body ⏎⏎ `Best,` ⏎ `Jacob` | `Hi Nora,\n\nI am looking forward to working with you. Are you available to meet at 3pm on Friday?\n\nBest,\nJacob` | `Hi nora, I am looking forward to working with you. Are you available to meet at three PM on Friday? Best, jacob.` | **no** — candidate rejected by guardrail, pipeline fell back |
| `long` | `structure=off`, `context=general` | one paragraph, `week-long`, `hikes` left alone | identical to Wispr | identical to Wispr | **yes** |

The exact inputs are recorded in `tests/e2e/test_formatter_e2e.py`. Note the `long`
transcript contains a deliberate ASR mishearing (`nightlife` → `hikes`); the model leaves
`hikes` alone, which is the correct behaviour (inventing `nightlife` would be guardrail 2).

With the shipped default `structure_mode: off`, the `list` fixture is deliberately prose
(`[Structure: prose]`), and the model correctly returns the comma list unchanged. The list
layout is only requested once the user turns structure on, per §7.1 — and as §3.2 shows,
it currently renders with doubled markers.

### Deviation classification (the distinction that matters)

| fixture | deviation | class |
| --- | --- | --- |
| `retraction` | model says `7pm`, Wispr says `7 pm` | **cosmetic** at model level — but the rendered output is unformatted because the guardrail *rejects* it (see §3) |
| `retraction` | rendered output keeps `six PM … actually no, seven PM` | **integration failure** — the retraction the feature exists to resolve is not applied |
| `list` | rendered output has `- - ` before every item | **cosmetic / layout** — no invented content, no summarisation, no refusal; but visibly wrong |
| `email` | model says `3pm`, Wispr says `3 pm` | **cosmetic** |
| `email` | rendered output has no paragraph breaks and lowercases `nora` / `jacob` | **integration failure** — the greeting/sign-off layout the feature exists to produce is not applied |
| `long` | none | — |

No fixture produced invented content, a summary, or a refusal. The failures are
"the correct answer was thrown away", not "the model lied".

---

## 3. Blockers found (both in files this milestone must not edit)

### 3.1 Guardrail false-rejects the model's times — `src/voice_transcriber/formatter.py`

`formatter._canonical_tokens()` splits on `[a-z0-9]+`. The input `seven PM` canonicalises
to the two tokens `["7", "pm"]` (number-word folding), but the model's correct answer
`7pm` canonicalises to the single token `["7pm"]`. Guardrail 2 then reports
`output contains text absent from the input: 7pm` and `formatter.format_text()` returns the
original. Same for `three PM` → `3pm`.

Number/date normalisation is on the plan's explicit whitelist of allowed transformations
(§8 rule 2), so this is a false positive, not a genuine hallucination catch.

Observed directly:

```
[backend] candidate='Hey Michelle, meet me at my apartment lobby at 7pm.'
[validate] reason='output contains text absent from the input: 7pm'
[final]   'Hey michelle, meet me at my apartment lobby at six PM, actually no, seven PM.'
```

**Needed change** (not made): fold digit/letter boundaries in `_canonical_tokens()`, e.g.
tokenise with `[a-z]+|[0-9]+` instead of `[a-z0-9]+`, so `7pm` and `7 pm` compare equal.
The model's raw answers then pass validation unchanged. One-line change; the existing
`tests/shared/test_formatter.py` parity fixtures already encode the intent.

### 3.2 Structure stage double-bullets the model's lists — `src/voice_transcriber/post_processor.py`

When `structure_mode` is `inline`/`blocks`, the model emits `- Milk for the cake`. The
deterministic `process_structure_blocks()` then re-detects the list and `_render_items()`
prepends its own `- `, because it only strips `step one`-style labels
(`_NUMBER_LABEL_PREFIX_REGEX`), not an existing `- ` marker. Result:
`- - Milk for the cake`.

```
[backend] candidate='I want to grab three things at the grocery store:\n- Milk for the cake\n- Eggs for breakfast\n- White bread'
[validate] reason=None
[final]   'I want to grab three things at the grocery store:\n\n- - Milk for the cake\n- - Eggs for breakfast\n- - White bread'
```

**Needed change** (not made): have `_render_items()` strip a leading `_LIST_MARKER_PREFIX_REGEX`
from the body before adding its own marker (or skip a region whose items already carry
markers). The formatter output and the structure stage must not both number the list.

Both fixes are prerequisites for the four fixtures matching Wispr; without them M2's
acceptance criterion is only met at the model level, not at the rendered level.

---

## 4. Latency

### 4.1 Warm server — the number that matters

Measured after the server had answered several requests (prompt cache hot). `wall` is the
release-to-output time of the **whole** `clean_speech_transcription()` call, formatter
included, over 10 runs; `prompt_ms` / `predicted_ms` are `llama-server`'s own `timings`
from the `/v1/chat/completions` response.

| fixture | server prompt tokens | server completion tokens | `prompt_ms` | `predicted_ms` | pipeline wall min / median / max |
| --- | --- | --- | --- | --- | --- |
| `retraction` | 98 | 15 | 8.4 ms (1 uncached token) | 108.8 | 133.2 / **135.6** / 140.4 ms |
| `list` | 102 | 26 | 8.4 ms (1 uncached token) | 199.4 | 220.5 / **225.6** / 248.7 ms |
| `email` | 107 | 28 | — | 216.2 | 223.6 / **235.0** / 351.6 ms |
| `long` | 129 | 52 | — | — | 425.9 / **441.7** / 473.1 ms |

The cached-prompt timing only measures the one uncached token (97–128 of the prompt tokens
come from the KV cache). The **first** request after spawn prefills the whole prompt:
`prompt_n=98, prompt_ms=47.4` for the retraction fixture, and `prompt_n=102,
prompt_ms=64.9` for the list fixture. Both numbers are reported because neither alone is
the whole story.

A direct `/v1/chat/completions` call for the retraction fixture, warm, is **129.3 ms** wall
with `prompt_n=1, prompt_ms=8.4, predicted_n=15, predicted_ms=108.8` — i.e. the pure
inference is ~109 ms and the rest is HTTP + cache lookup. Pipeline-overhead above the raw
HTTP call is ~6 ms.

### 4.2 Cold start (spawn to first healthy answer)

Measured with the backend's own spawn path (`llama_server.warm()` then the first real
request), no attach URL:

| stage | time |
| --- | --- |
| spawn → `/health` healthy (`warm()`) | **0.776 s** |
| spawn → **first answer** | **0.938 s** (first inference 161.2 ms) |
| first (fully uncached) prompt | `prompt_n=98`, `prompt_ms=47.4`, `predicted_n=15`, `predicted_ms=106.9` |

The model loads by mmap, so most of the 484 MB never faults in until used; the load itself
is not the cost. The app warms the backend before first use, so a user's first real
dictation does not pay the 0.78 s spawn.

### 4.3 Does it fit the §9 budget?

Yes, on this hardware. For a typical short utterance (98 prompt / 15 completion tokens)
the warm formatter costs **~0.13 s**; a medium email costs **~0.24 s**; the 129-token
`long` paragraph costs **~0.44 s**.

Against the ≤1.5 s release-to-clipboard SLA — which §9 explicitly exempts when the
formatter is on, replacing it with "ASR time + formatter time" — the formatter consumes:

* **~9 %** of 1.5 s for a short utterance (135.6 ms),
* **~16 %** for the email (235.0 ms),
* **~29 %** for the long paragraph (441.7 ms).

The default `formatter.DEFAULT_TIMEOUT_S = 3.0` was never approached; there is ~2.5 s of
headroom on the longest fixture.

---

## 5. Memory

| metric | value |
| --- | --- |
| `llama-server` peak RSS (`VmHWM`) | **150.9 MiB** in the clean single-session run; up to **170.7 MiB** across runs |
| total system RSS delta (`MemAvailable` before → after) | **~150–177 MB** |
| GGUF on disk | 484.2 MB (mmap'd; resident is the working set, not the file) |

That is well inside the §9 "8 GB minimum" tier and comfortable at the 16 GB recommendation.
The Cohere ASR (~2 GB int8 resident) is *not* included in this measurement.

---

## 6. Determinism

Three identical runs of each fixture produced byte-identical output (`len(set(runs)) == 1`)
for all four, both at the model level and through the pipeline. This matches the greedy
configuration (`--temp 0 --seed 0 --top-k 0 --top-p 1 --repeat-penalty 1.0` plus the
request-level `temperature=0, top_k=0, top_p=1, repeat_penalty=1.0`). Because bit-identical
output is only guaranteed for a fixed build + device, the e2e test asserts determinism
within a run and does **not** assert exact strings across machines.

---

## 7. The pinned test

`tests/e2e/test_formatter_e2e.py` (new) is skipped unless **both** the GGUF and
`llama-server` are present, so the model-free tiers stay green on a machine without
weights. Verified skip with `HOME=/tmp/…`: all 7 tests SKIP with the weights-absent reason.

Result **with weights present**:

```
tests/e2e/test_formatter_e2e.py::test_backend_candidate_is_non_empty_and_sane_for_every_fixture PASSED
tests/e2e/test_formatter_e2e.py::test_retraction_resolves_at_the_model_level PASSED
tests/e2e/test_formatter_e2e.py::test_output_is_deterministic_across_three_runs PASSED
tests/e2e/test_formatter_e2e.py::test_long_fixture_matches_wispr_end_to_end PASSED
tests/e2e/test_formatter_e2e.py::test_retraction_matches_wispr_end_to_end XFAIL
tests/e2e/test_formatter_e2e.py::test_email_layout_matches_wispr_end_to_end XFAIL
tests/e2e/test_formatter_e2e.py::test_list_matches_wispr_end_to_end XFAIL
4 passed, 3 xfailed
```

The three `xfail`s are `strict=False` and carry the exact blocker in their reason string
(§3.1 / §3.2). When those bugs are fixed they flip to `XPASS` instead of silently becoming
new passing tests. The suite spawns one warm `llama-server`, shuts it down in a fixture
finaliser, and leaves no stray process (verified: `ps -ef | grep -F llama-server` → 0).

`tests/shared` remains green with no weights loaded:
**1269 passed, 2 skipped** (`nix develop --command python -m pytest tests/shared -q`).
`ruff check src/ tests/` passes.

---

## 8. What this milestone did not do

* **Did not fix** `src/voice_transcriber/formatter.py` or
  `src/voice_transcriber/post_processor.py` — both are off-limits for C2/M2. §3 records
  the precise change each needs; a follow-up milestone owns them.
* **Did not run real audio through the ASR.** These are transcript-to-text measurements;
  ASR time is not included in any number above. The `%` of SLA figures are the
  formatter's share of the 1.5 s gate only.
* **Did not measure GPU offload.** CPU only (Radeon 890M present but not exercised).
* **Did not measure Windows/WSL.** Linux only, as C2/M2 scoped.
