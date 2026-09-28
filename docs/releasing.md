# Releasing

Runbook for cutting a release of voice-transcriber on GitHub
(`github.com/jjamesmartiin/voice-transcriber`).

> **The one thing that bites:** CI builds and attaches the AppImage **only**.
> The ~2.8 GB of model weights are **not** uploaded by CI and must be attached
> by hand, every release. If you skip that step the release still works — but
> only because the app silently falls back to a *hardcoded older tag*
> (see [The `/latest` fallback trap](#the-latest-fallback-trap)). Do not rely
> on that. Upload the weights.

---

## TL;DR checklist

```bash
# 1. Bump the version in all three places (see "Version bump" below).
#    flake.nix (x2) and src/tui.py (x1)

# 2. Verify the tree is green before tagging.
./test.sh
nix build .#vt-tui --no-link --print-out-paths

# 3. Commit, tag, push the tag. Pushing the tag is what creates the release.
git add -A && git commit -m "release: v1.2.0"
git tag v1.2.0
git push github main --tags        # remote 'github' == git@github.com:jjamesmartiin/voice-transcriber.git

# 4. Wait for the `release` workflow to finish (it creates the Release and
#    attaches vt.AppImage). Watch it, or just poll the release object:
nix develop --command gh release view v1.2.0

# 5. ATTACH THE MODEL WEIGHTS — the step CI does not do.
nix develop --command gh release upload v1.2.0 \
  dist/model/cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.part1.xz \
  dist/model/cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.part2.xz \
  dist/model/cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.SHA256SUMS \
  --clobber

# 6. Verify all four assets are present (see "Verifying a release").
```

`--clobber` makes step 5 safe to re-run if the upload drops part-way through a
~1.5 GB part.

---

## Version bump

The version string is duplicated and **not** derived from the git tag. Bump all
three before tagging, or the AppImage/UI will report a stale version:

| File | What |
| :--- | :--- |
| `flake.nix` | `version = "1.2.0";` in the flake `outputs` let-block |
| `flake.nix` | `version = "1.2.0";` in the `pkgs.stdenv.mkDerivation` attrs |
| `src/tui.py` | `def __init__(self, app_version="1.2.0", ...)` |

`tui-rs/Cargo.toml` tracks the Rust frontend (`vt-tui`) version independently
and is not the app version.

Sanity check that nothing was missed:

```bash
grep -rn '[0-9]\+\.[0-9]\+\.[0-9]\+' flake.nix src/tui.py | grep -v '^\s*#'
```

---

## What CI does, and what it does not

`.github/workflows/release.yml` triggers on any pushed tag matching `v*`:

1. runs on `ubuntu-latest`, installs Nix,
2. `nix bundle --bundler github:ralismark/nix-appimage .#default` → one AppImage,
3. `softprops/action-gh-release@v2` with `files: ./*.AppImage` and
   `fail_on_unmatched_files: true` — this **creates the Release object** if one
   does not exist, and attaches the AppImage.

It uses no secrets. Its header comment states the weights are deliberately not
fetched in CI.

`.github/workflows/ci.yml` is separate (push/PR only) and runs the three-OS test
matrix. It does not publish anything.

**Consequences:**

- There is **no automated model upload**, by design and by omission. The
  workflow's `files:` glob simply does not include them.
- CI *could not* build them even if the glob were widened: the GitHub-hosted
  runner has no Hugging Face cache and no `dist/model/`. Only the maintainer's
  machine does. (Widening the glob without also providing the files would make
  the release job fail on `fail_on_unmatched_files: true`.)
- No prebuilt **Windows** binary is published. `dist/` is gitignored and
  PyInstaller cannot cross-compile; see
  [`platforms/windows/plan-to-compile.md`](../platforms/windows/plan-to-compile.md).

---

## The model assets

### Naming

```
cohere-transcribe-<revision>.part<N>.xz     # the weights
cohere-transcribe-<revision>.SHA256SUMS     # "<sha256>  <filename>" per part
```

with `<revision> = 499888924f5f1313b48ab0686c8f3a94178a4709` (the
`CohereLabs/cohere-transcribe-03-2026` HF revision).

Two parts by default, so each stays under GitHub's **2 GiB per-file** release
limit after xz compression.

### Where they already are

They are normally **already built and sitting in the working tree**:

- `dist/model/` — the ready-to-upload release assets
- `models/cohere/` — the unpacked flat HF snapshot (~4.13 GB, gitignored)

`dist/model/` is gitignored, so it does not travel with a clone. It is a build
output, like `result/`, and can be stale — but the weights are versioned by
content hash, so they are only regenerated when the upstream model revision
changes.

Confirm the local copies are intact before uploading:

```bash
cd dist/model && sha256sum -c cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.SHA256SUMS
```

### Regenerating them (only when the model revision changes)

`scripts/prepare_model_release.py` does the whole job: it tars the flat model
files plus the Apache-2.0 `LICENSE`/`NOTICE`, splits the tar into raw byte-range
parts (a tar concatenates cleanly across any byte boundary), xz-compresses each
part, writes `SHA256SUMS`, then **verifies** by reassembling, extracting, and
re-hashing `model.safetensors` against the known HF blob hash.

```bash
# Auto-locates the snapshot in ~/.cache/huggingface/hub
python3 scripts/prepare_model_release.py --out dist/model

# Or point it at an explicit flat dir (e.g. an existing models/cohere)
python3 scripts/prepare_model_release.py --model-dir models/cohere --out dist/model
```

Useful flags: `--parts N`, `--xz-level N` (default 6; 9 gains almost nothing on
weights), `--skip-verify` (don't).

It refuses to finish if verification fails, so a green run is a publishable run.
Redistribution is legitimate because the model is Apache-2.0; the LICENSE +
NOTICE are bundled into the tar by the script and the license text lives at
`config/licenses/Cohere-Apache-2.0.txt` (git-tracked).

**If you bump `REVISION`**, update it in *both* `scripts/prepare_model_release.py`
and `src/model_download.py` (`REVISION`, and `SAFETENSORS_SHA256`), plus the
asset filename references in this document.

---

## Uploading the weights

`gh` is in the dev shell (there is no `python`/`gh` on the host PATH):

```bash
nix develop --command gh release upload v1.2.0 \
  dist/model/cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.part1.xz \
  dist/model/cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.part2.xz \
  dist/model/cohere-transcribe-499888924f5f1313b48ab0686c8f3a94178a4709.SHA256SUMS \
  --clobber
```

Practical notes:

- **Auth** comes from your **system keyring** (secret-service), not from
  `~/.config/gh/hosts.yml` — that file has no token, which makes it look like
  you are logged out when you are not. Required scope is `repo`.
  Check with `nix develop --command gh auth status`.
- Passing `dist/model/*.xz` as a shell glob works too; listing names explicitly
  is safer against a stale third part lingering in the directory.
- This is a multi-gigabyte upload. On a slow link, run it somewhere with real
  bandwidth, and re-run with `--clobber` if it drops. `gh` does not resume a
  part; a dropped part is simply not registered and gets uploaded again.
- If `nix run nixpkgs#gh` fails with `Truncated tar archive detected`, the
  cached `nixpkgs-unstable` channel tarball is corrupt. Clear it
  (`rm -rf ~/.cache/nix/tarball-cache*`) or just use the dev shell, which
  resolves from `flake.lock` and is unaffected.

---

## Verifying a release

```bash
nix develop --command gh release view v1.2.0
```

Expect exactly **four** assets:

| Asset | ~Size |
| :--- | :--- |
| `vt.AppImage` | ~1.18 GB |
| `cohere-transcribe-….part1.xz` | 1 458 806 112 |
| `cohere-transcribe-….part2.xz` | 1 456 452 916 |
| `cohere-transcribe-….SHA256SUMS` | 268 |

A release with only `vt.AppImage` means step 5 was skipped.

End-to-end check that the *client* path works — i.e. what a first-run user gets.
This exercises the real download/verify/install code:

```bash
# Force a clean install into a throwaway dir and confirm it round-trips.
VT_MODEL_DIR=$(mktemp -d)/cohere \
  nix develop --command python -m src.model_download
```

It fetches the manifest, downloads both parts, checks each SHA-256, decompresses,
extracts, and re-hashes `model.safetensors` against the pinned
`SAFETENSORS_SHA256`, then writes `SOURCE.json` provenance. Any mismatch aborts
with no partial install. Point `VT_MODEL_RELEASE_BASE` at a specific tag to test
a tag other than the default.

---

## The `/latest` fallback trap

In `src/model_download.py`:

```python
DEFAULT_RELEASE_BASE  = "https://github.com/jjamesmartiin/voice-transcriber/releases/latest/download"
FALLBACK_RELEASE_BASE = "https://github.com/jjamesmartiin/voice-transcriber/releases/download/v1.1.0"
```

On first run the client tries `latest`, and on a 404 fast-fails to
`FALLBACK_RELEASE_BASE`. So:

- If a release is published **without** weights, `latest` 404s and the client
  quietly installs the weights from the **hardcoded `v1.1.0` pin**. Users are
  served old-but-valid weights, and the release looks fine. This is a silent
  failure, not a loud one — which is exactly why the upload step is easy to
  forget.
- `FALLBACK_RELEASE_BASE` is a hand-maintained pin. It must always point at a
  tag whose assets actually exist. Bumping it is optional busywork *if* you
  reliably upload to every new release; it is your safety net if you don't.

History, for context: `v1.1.1` shipped AppImage-only and has been serving
weights from the `v1.1.0` fallback since 2026-09-26.

**A future improvement** would be a `workflow_dispatch` job on a self-hosted
runner that has `dist/model/`, so the upload stops being manual. Nothing like
that exists yet.

---

## Client-side install behaviour

Worth knowing when debugging "it can't find the model":

Install target order (`cohere_models_dir()`):

1. `$VT_MODEL_DIR` if set,
2. a frozen (PyInstaller) `_MEIPASS` bundle,
3. `<repo>/.git`'s `models/cohere` when running from a **writable** checkout,
4. otherwise the per-user data dir — `~/.local/share/vt/models/cohere` on
   Linux/macOS, `%APPDATA%\vt\models\cohere` on Windows (or
   `$XDG_DATA_HOME/vt`).

Steps 3–4 matter because the Nix store and AppImage installs are read-only, so
they must use the per-user dir. A `SOURCE.json` recording repo, revision,
release tag, sha256, license, and install date is written next to the weights.

Environment knobs:

| Variable | Effect |
| :--- | :--- |
| `VT_MODEL_DIR` | Explicit install dir; overrides all other targets |
| `VT_AUTO_DOWNLOAD_MODEL` | `0`/`false`/`no`/`off` disables auto-download |
| `VT_MODEL_RELEASE_BASE` | Override the asset base URL (default: `latest`) |
| `XDG_DATA_HOME` | Relocates the per-user fallback (useful in tests) |

Manual check of an existing install:

```bash
nix develop --command python -m src.model_download --verify-only
```

---

## Gotchas

- **Nix flakes only see git-tracked files.** A new file under `src/` left
  untracked is excluded from `nix build` / `nix run`, shipping a build whose
  `main.py` imports a module that is not there. `git add` before building.
- **`result/` is stale until rebuilt** — it symlinks to the last `nix build`.
- **The release object does not exist until CI creates it.** `gh release upload`
  against a tag with no Release object fails; wait for the workflow. (If you
  genuinely need to get ahead of it, `gh release create v1.2.0 --verify-tag
  --notes "..."` first — CI's `softprops` step will then attach the AppImage to
  the existing release rather than creating a second one.)
- **`config/config.yaml` is a real user config** (untracked). Manual commands
  must not clobber it; restore anything you change.
- **Don't `pkill -f "src/main.py"`** — the pattern matches the shell running the
  command too. Use `ps -ef | grep -F main.py` and kill by PID.
