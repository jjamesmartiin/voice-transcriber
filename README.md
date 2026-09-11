# Voice Transcriber

A robust, modular voice transcription tool with global hotkeys for Linux (Wayland/X11) and Windows (via NixOS WSL or Native).

---

## Quick Start

### Windows (Native)
Run directly on Windows without WSL:
- See [**`README_WINDOWS.md`**](README_WINDOWS.md) for full native setup.
```powershell
# In PowerShell:
pip install -r requirements.txt
.\run.ps1
```

### Windows (via NixOS WSL)
Run the Linux backend inside WSL with transparent Windows host hotkeys and clipboard integration:
- See [**`README_WSL.md`**](README_WSL.md) for full WSL guide.
```powershell
# From Windows PowerShell:
.\run_wsl.ps1
```
*(Or inside WSL: `./run.sh`)*

---

### Linux
```bash
# Add user to input group for global hotkeys
sudo usermod -a -G input $USER
# Log out and back in, then run:
nix run .

# Or run with Python:
python src/main.py
```

#### NixOS Example
```nix
users.users.yourusername.extraGroups = [ "input" ];
```

See [NixOS options](https://search.nixos.org/options?channel=25.11&include_modular_service_options=1&include_nixos_options=1&query=users.users.*.extra) for more info.

---

## Usage

### Controls
- **Alt+Shift** (hold) - Start recording, release to transcribe
- **Space** (tap while holding Alt+Shift) - Hold the recording hands-free: release the keys and keep talking, then press & release Alt+Shift again to transcribe
- **Ctrl+Alt+I** - Open settings menu

### Settings Menu
- P/S - Set primary/secondary audio device
- M - Toggle mute
- B - Switch model (whisper/cohere)
- T - Toggle auto-type to screen
- c - Save and exit

---

## 📐 Architecture & Documentation

For a comprehensive technical breakdown, end-to-end execution flowcharts, and mathematical decision trees, see:
- 📑 [**Technical Architecture & Execution Flowcharts**](docs/architecture.md)
- 🧪 [**AI Agent Quality Control & Endpoint Testing Protocol**](docs/agent_testing_workflow.md)

---

## 🧪 AI Agent Quality Control & Endpoint Testing Protocol

Any code changes made by AI assistants or contributors must pass the automated acoustic loopback endpoint test suite before completion:

- **Post-Release Latency Gate**: **$\le 1.50$ seconds (ideally $\le 1.0$s)** from key release (`stop_recording`) to clipboard paste/typing.
- **Accuracy Gate**: **$\ge 80.0\%$ (Target 100%)** match ratio against ground truth across short words, phrases, and 30s continuous dictation.
- **Hallucination Gate**: **0 noise tokens** (e.g., `"you"`) on ambient noise or short audio clips.

```bash
# Run automated acoustic loopback benchmark suite:
nix develop --command python tests/test_live_speaker_mic_loopback.py all
```

See [**`docs/agent_testing_workflow.md`**](docs/agent_testing_workflow.md) for full self-validation guidelines.

---

## ⚙️ Configuration

Voice Transcriber supports fully customizable YAML and JSON configuration files.

To customize startup defaults (e.g. `is_muted: true`, `auto_type: false`), copy the template:
```bash
cp config/example-config/config.yaml.example config/config.yaml
```

Check [**`config/example-config/config.yaml.example`**](config/example-config/config.yaml.example) for detailed comments on all available options. A JSON variant is also supported: [`config/example-config/audio_device_config.json.example`](config/example-config/audio_device_config.json.example).

### Hugging Face Token

The default Cohere model is [gated](https://huggingface.co/CohereLabs/cohere-transcribe-03-2026) and requires a Hugging Face access token for download. Set `hf_token` in your local (gitignored) `config/config.yaml`, e.g.:

```bash
cp config/example-config/config.yaml.example config/config.yaml
# then set: hf_token: "hf_..."
```

Fallbacks if `hf_token` is unset: the `HF_TOKEN` environment variable, then `huggingface-cli login` credentials. No token is needed once the model is cached locally — under the repo's own `models/cohere` when running from a git checkout, or under `~/.local/share/vt/models/cohere` for read-only Nix/AppImage installs.

---

## Model Details

- **Cohere Transcribe (`CohereLabs/cohere-transcribe-03-2026`)**: Primary default model. High precision, low hallucination rate.
- **Faster-Whisper (`small` / `medium`)**: Local Whisper engine fallback with CTranslate2 optimization.

---

## Wispr Flow Real-Time Self-Correction & Post-Processor

Voice Transcriber features a multi-tiered Wispr Flow post-processing pipeline for real-time speech self-correction and disfluency cleanup:

### 1. Configuration & Environment Options

| Environment Variable | Default Value | Description |
| :--- | :--- | :--- |
| `VT_ENABLE_SLM` | `0` (set `1` to enable) | Toggles local `vLLM` SLM post-processing pass (`1` = active, `0` = Technique A pre-pass only). Battery-friendly default is `0` (no vLLM server required). |
| `VT_MODEL_DTYPE` | `bf16` on BF16-capable CPUs, else `fp32` | Cohere ASR weight precision (`bf16`, `fp16`, or `fp32`). `bf16` is bit-lossless for the BF16-stored weights while halving RAM. |
| `VT_SLM_MODEL` | `Qwen/Qwen2.5-0.5B-Instruct` | Local SLM model served on vLLM (`Qwen2.5-0.5B`, `Llama-3.2-1B`, etc.). |
| `VT_VLLM_URL` | `http://localhost:8000/v1/chat/completions` | Local vLLM OpenAI-compatible REST API endpoint. |
| `VT_SLM_TIMEOUT` | `1.5` (seconds) | Maximum timeout before gracefully falling back to Technique A ASR text. |
| `VT_CPU_THREADS` | `min(8, os.cpu_count())` | PyTorch CPU thread cap to prevent CPU spinning. Lower values (e.g. `4`) save battery at some latency cost. |
| `VT_MODE` | `auto` (`auto`, `fixed`, `stop-and-wait`) | Audio micro-batching mode for real-time streaming. |

---

### 2. Latency Benchmarks (Synthetic E2E Speech Audio $\rightarrow$ ASR $\rightarrow$ Wispr Flow)

Measured on AMD Ryzen (`pc-jamesm2`) serving local `vLLM` (`Qwen2.5-0.5B-Instruct` on CPU):

| Benchmark Test Case | Audio Duration | ASR Decoding Latency | Wispr Flow SLM Pass | Total E2E Latency | Idempotent Match |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **Self-Correction Retraction** (*"5 PM... actually 6 PM"*) | 4.78s | 1328.7 ms | **446.8 ms** | **1775.5 ms** | ✅ **100% Yes** |
| **Name Retraction** (*"John... I mean Alice"*) | 3.37s | 1220.0 ms | **282.1 ms** | **1502.1 ms** | ✅ **100% Yes** |
| **Date Self-Correction** (*"Tuesday... no wait Wednesday"*) | 3.95s | 1239.2 ms | **216.4 ms** | **1455.6 ms** | ✅ **100% Yes** |
| **Voice Erasure** (*"scratch that"*) | 3.37s | 1319.5 ms | **347.5 ms** | **1667.0 ms** | ✅ **100% Yes** |
| **Subordinating Conjunction Fix** (*"specifically. because"*) | 3.38s | 1273.0 ms | **258.3 ms** | **1531.3 ms** | ✅ **100% Yes** |

---

### 3. Synthetic Audio Test Suite & Latency Benchmark Runner

You can generate synthetic speech audio on-the-fly and run end-to-end latency benchmarks without relying on manual microphone recordings:

```bash
# Run synthetic E2E audio test suite and print latency table:
python3 tests/benchmark_synthetic_e2e.py

# Run in Nix shell:
nix-shell -p espeak-ng python3Packages.pytest python3Packages.numpy python3Packages.soundfile python3Packages.torch python3Packages.transformers python3Packages.librosa python3Packages.sentencepiece --run "python3 tests/benchmark_synthetic_e2e.py"
```

---

## Custom Word & Phrase Dictionary

Voice Transcriber includes a high-speed, Trie-compacted dictionary replacer that executes in **~0.005 ms (5 microseconds)** without slowing down text processing or audio streaming.

You can configure word and multi-word phrase conversions directly in your `config/config.yaml` or in an external dictionary file. Spoken inputs are matched case-insensitively with boundary protection (e.g. `"cat"` will never accidentally alter `"catalog"` or `"catch"`), and target casing and formatting are strictly preserved.

### 1. Adding Replacements in `config/config.yaml`

Add a `dictionary` map to your `config/config.yaml`:

```yaml
dictionary:
  "pull request": "PR"
  "pr": "PR"
  "v l l m": "vLLM"
  "vllm": "vLLM"
  "vs code": "VS Code"
  "github": "GitHub"
  "k8s": "Kubernetes"
  "postgres": "PostgreSQL"
  "smiley face": "😊"
```

### 2. Using an External Dictionary File

If you prefer keeping your dictionary separate, point `dictionary_file` to a YAML or JSON file:

```yaml
dictionary_file: "config/dictionary.yaml"
```

Or simply create `config/dictionary.yaml` (Voice Transcriber will auto-detect it on startup):

```yaml
# config/dictionary.yaml
"pull request": "PR"
"open ai": "OpenAI"
"nixos": "NixOS"
"dot net": ".NET"
```

### 3. Technical Acronyms & Homophone Repair Patterns

For developer terms and advanced regex homophone repairs:
- **Technical Acronym Whitelist**: Any capitalized target words in your dictionary are automatically whitelisted from mid-sentence decapitalization. You can also manually add entries to `TECHNICAL_ACRONYMS_AND_PROPER_NOUNS` in `src/post_processor.py`.
- **Regex Homophone Rules**: For complex phonetic regex patterns, you can add tuples of `(regex_pattern, replacement_str)` to `HOMOPHONE_REPAIR_PATTERNS` in `src/post_processor.py`.

---

## Distribution & Releases (no Hugging Face required)

The Cohere model (`CohereLabs/cohere-transcribe-03-2026`) is **Apache-2.0 licensed**, which permits redistribution with attribution. GitHub Releases therefore ship the weights as mirrored, compressed assets — end users never need a Hugging Face account or token.

### First run: automatic model install

When the Cohere backend starts and finds no local copy, the app automatically:

1. downloads the release assets (`cohere-transcribe-<revision>.part1.xz` / `.part2.xz`, ~1.46 GB each),
2. verifies each part's SHA-256 against the published `SHA256SUMS`,
3. decompresses, concatenates, and extracts them into the project's own **`models/cohere`** directory when you're running from a git checkout (so it's obvious the weights belong to this repo), or into `~/.local/share/vt/models/cohere` when running from a read-only install (Nix store / AppImage), then
4. writes a **`SOURCE.json`** provenance file next to the weights recording where they came from: origin repo, exact GitHub release tag, revision, `model.safetensors` SHA-256, license, and install date,
5. loads fully offline (`local_files_only=True`) on every launch afterwards.

```bash
# Inspect where a model came from (printed at install, and always on disk):
cat models/cohere/SOURCE.json          # git checkout install
cat ~/.local/share/vt/models/cohere/SOURCE.json   # Nix/AppImage install

# Explicit install location (overrides repo-first and the user dir):
export VT_MODEL_DIR=/path/to/models/cohere

# Opt out of automatic downloads (you supply the model yourself):
export VT_AUTO_DOWNLOAD_MODEL=0

# Point at a different asset mirror (self-hosted, fork, ...):
export VT_MODEL_RELEASE_BASE=https://github.com/<you>/voice-transcriber/releases/latest/download
```

Needs ~2.9 GB to download and ~4.1 GB free disk to unpack (the bf16 weights). If no release assets are reachable, the app falls back to the classic Hugging Face download path.

### Making a release

No secrets or tokens are involved — the model weights are mirrored by the maintainer, never fetched in CI.

1. Push a `v*` tag. The [`.github/workflows/release.yml`](.github/workflows/release.yml) workflow builds and attaches a single self-contained Linux **AppImage** (`vt-x86_64-linux.AppImage`) via `nix bundle`, so non-Nix users can run the app without installing Nix:

```bash
git tag v1.0.2 && git push origin v1.0.2
```

2. Attach the mirrored model assets to the same release (built once from the HF cache on the maintainer machine, round-trip verified):

```bash
python3 scripts/prepare_model_release.py --out dist/model   # if not already built
gh release upload v1.0.2 dist/model/*                        # parts + SHA256SUMS
```

…or drag `dist/model/*` into the release page on github.com.

### License & attribution for the mirrored weights

Redistribution of the Cohere weights is permitted under the Apache License 2.0; the packaged model includes `LICENSE` (Apache-2.0 text) and `NOTICE` (attribution) files inside the model directory. See [`config/licenses/Cohere-Apache-2.0.txt`](config/licenses/Cohere-Apache-2.0.txt).

## License
See [LICENSE](LICENSE) for details.
