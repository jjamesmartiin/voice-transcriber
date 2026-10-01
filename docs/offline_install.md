# Offline & airgapped installs

The app and the model weights are distributed separately, on purpose:

| Piece | Size | OS-specific? |
| :--- | :--- | :--- |
| **App bundle** | ~1 GB (model-free) or ~6.8 GB (weights bundled) | **Yes** — one per OS |
| **Model bundle** | ~2.9 GB (split-xz) | **No** — identical on every OS |

The weights are plain files (`model.safetensors` + tokenizer + modeling code)
addressed by *model revision*, never by app release. The same model bundle boots
the Windows, Linux and macOS apps, so an airgapped deployment carries **one**
model bundle plus one app bundle per OS.

Everything the installer needs (`urllib`, `lzma`, `tarfile`, `zipfile`) ships
inside the app bundle, so an airgapped host needs **no extra tooling** — no
Python install, no `xz`, no `7z`, no `unzip`.

---

## Normal first run (online)

Start the app. If no local copy of the weights exists, it downloads
`cohere-transcribe-<revision>.partN.xz` from the revision-keyed release tag,
verifies each part's SHA-256, `lzma`-decompresses and concatenates them, extracts
the tar, checks `model.safetensors` against the pinned `SAFETENSORS_SHA256`, and
writes a `SOURCE.json` provenance file. After that it loads fully offline.

Location, in priority order:

1. `$VT_MODEL_DIR` (explicit override)
2. bundled `models/cohere` next to the app (only in a weights-bundled build)
3. `<repo>/models/cohere` when running from a git checkout
4. per-user data dir — `%APPDATA%\vt\models\cohere` (Windows),
   `~/.local/share/vt/models/cohere` (Linux/macOS)

---

## Airgapped deployment

### 1. Produce the model bundle (once, on a connected machine)

```bash
python scripts/prepare_model_release.py --model-dir <flat model dir> \
    --out dist/model --zip dist/model/cohere-transcribe-bundle.zip
```

Outputs the split parts, `SHA256SUMS`, and (with `--zip`) a single transportable
archive. See [`docs/releasing.md`](releasing.md) for the publish workflow.

### 2. Produce the app bundle for each target OS

Build the app **without** the weights — the model bundle supplies them.

```bash
# Windows
build.bat --no-model                        # -> dist/VoiceTranscriber/  (~1.1 GB)

# Linux
nix build .                                  # -> result/bin/vt (AppImage via nix bundle)

# macOS: run from source (platforms/macos/README.md)
```

### 3. Carry to the airgapped host

Copy the OS app bundle and the model bundle onto USB. On the target machine,
place the model bundle either:

* **directly**, as `model-bundle/` next to the executable (auto-detected) —
  for an **AppImage**, next to the `.AppImage` file itself (the runtime exports
  `$APPIMAGE`/`$APPDIR`, which the installer checks), or
* anywhere, and point at it with `VT_MODEL_SOURCE_DIR=<path>`.

`model-bundle/` may be:

* a **directory** holding `cohere-transcribe-<rev>.partN.xz` + `SHA256SUMS`, or
* a **`.zip`** (or `.tar[.gz|.xz|.bz2]`) containing them — the archive produced
  by `--zip` above.

### 4. Install

Launch the app. It sees the bundle before it ever touches the network, verifies
and assembles it exactly as the online path does, and installs to the per-user
model dir. Later runs load from disk with no removable media attached.

For a source checkout, the same code path is available directly:

```bash
python -m model_download --from /media/usb/cohere-transcribe-bundle.zip
python -m model_download --verify-only         # confirm the local copy
```

### 5. Verify (optional but recommended)

* `SHA256SUMS` lists a digest per part; a mismatch aborts with **no partial
  install**.
* The extracted `model.safetensors` is checked against the pinned
  `SAFETENSORS_SHA256` in `src/model_download.py`.
* `SOURCE.json` records where the weights came from (`source_kind: "local"` for
  a bundle install), the revision, and the install time.

---

## Keeping a completely network-free host

* Set `VT_AUTO_DOWNLOAD_MODEL=0` to forbid any automatic network access.
* Setting `VT_MODEL_SOURCE_DIR` is **fail-closed**: if the bundle is missing or
  corrupt the install fails rather than silently falling back to the network
  (which would both defeat the airgap and reveal connectivity).
* `VT_MODEL_RELEASE_BASE` relocates the online source (e.g. an internal mirror).

---

## Building a weights-bundled (fully self-contained) app

If you would rather not carry a separate model bundle at all, bake the weights
into the app for that OS:

```cmd
build.bat            # Windows, bundles models/cohere (~6.8 GB)
```

The trade-off is size and per-OS duplication: the weights are re-copied into
every OS build, and the ~6.8 GB result exceeds GitHub's 2 GiB per-asset limit,
so it is a USB/physical-media artifact rather than a release asset.
