# Releasing

Runbook for cutting a release of voice-transcriber on GitHub
(`github.com/jjamesmartiin/voice-transcriber`).

> **The one thing that used to bite:** CI builds and attaches the AppImage
> **only**, and can never attach the ~2.8 GB of weights (a GitHub-hosted runner
> has no `dist/model/`). That used to mean uploading the weights by hand on
> *every* release. It no longer does: weights are addressed by **model
> revision**, not by app release, so a new version re-uses the existing bundle
> and uploads nothing. See [Where the weights live](#where-the-weights-live).

---

## TL;DR checklist

```bash
# 1. Bump the version across all versioned surfaces (see "Version bump" below).
#    flake.nix, src/voice_transcriber/tui.py, src/voice_transcriber/__init__.py,
#    tui-rs/src/main.rs, pyproject.toml, packaging/nix/package.nix,
#    packaging/linux/*.appdata.xml, scripts/generate_demo_gif.py

# 2. Verify the tree is green before tagging.
./scripts/test.sh
nix build .#vt-tui --no-link --print-out-paths

# 3. Commit, tag, push the tag. Pushing the tag is what creates the release.
git add -A && git commit -m "release: v1.3.1"
git tag v1.3.1
git push github main --tags        # remote 'github' == git@github.com:jjamesmartiin/voice-transcriber.git

# 4. Wait for the `release` workflow to finish (it creates the Release and
#    attaches vt-x86_64.AppImage, VoiceTranscriber-windows-x86_64.zip and
#    REQUIREMENTS.txt). Watch it, or poll the release object:
nix develop --command gh release view v1.3.1

# 5. NOTHING TO DO FOR THE WEIGHTS OR THE llama-server BUNDLE. Each lives in
#    its own keyed bundle (model revision, upstream pin) and is re-published
#    only when that key changes — see "Where the weights live" and
#    "The pinned llama-server bundle".

# 6. Verify the release carries the release assets (see "Verifying a release").
```

---

## Version bump

The version string is duplicated and **not** derived from the git tag. Bump all
surfaces before tagging, or the AppImage/Windows bundle/UI/catalogue metadata will report a stale
version:

| File | What |
| :--- | :--- |
| `flake.nix` | `version = "1.3.1";` in the flake `outputs` let-block |
| `flake.nix` | `version = "1.3.1";` in the `pkgs.stdenv.mkDerivation` attrs |
| `src/voice_transcriber/tui.py` | `def __init__(self, app_version="1.3.1", ...)` |
| `src/voice_transcriber/__init__.py` | `__version__ = "1.3.1"` |
| `tui-rs/src/main.rs` | `const VERSION: &str = "1.3.1";` |
| `pyproject.toml` | `version = "1.3.1"` |
| `packaging/nix/package.nix` | `version = "1.3.1";` |
| `packaging/linux/*.appdata.xml` | a new `<release version="1.3.1" date="YYYY-MM-DD"/>` entry |
| `scripts/generate_demo_gif.py` | the hero GIF's `voice transcriber v1.3.1 active` banner (then regenerate the GIF) |

`tui-rs/Cargo.toml` tracks the Rust frontend (`vt-tui`) internal crate version (`0.1.0`) independently
and is not the app version.

Sanity check that nothing was missed:

```bash
python -m pytest tests/shared/test_version_consistency.py
```

---

## What CI does, and what it does not

`.github/workflows/release.yml` triggers on any pushed tag matching `v*`:

1. **Linux AppImage job (`ubuntu-latest`)**: installs Nix, runs `nix bundle --bundler github:ralismark/nix-appimage .#default` → `vt-x86_64.AppImage`, then generates `REQUIREMENTS.txt` and attaches both.
2. **Windows Standalone ZIP job (`windows-latest`)**: installs dependencies, executes `platforms/windows/build_offline.py --no-model`, zips `dist/VoiceTranscriber` → `VoiceTranscriber-windows-x86_64.zip`, then compiles `platforms/windows/installer.iss` with the preinstalled `ISCC.exe` → `VoiceTranscriber-windows-x86_64-setup.exe` (the same bundle, as one downloadable file; see the installer notes below).
3. `softprops/action-gh-release@v3` attaches the assets to the GitHub Release with `fail_on_unmatched_files: true`.

A release carries the **app runtime only** — no weights, no config, no source
archive: GitHub attaches `Source code (zip)`/`(tar.gz)` to every tag by itself, so
building another one would be a duplicate. `REQUIREMENTS.txt` (see below) names
the bundles a build resolves. There is deliberately **no macOS artifact yet** —
see `docs/TODO.md`.

It uses no secrets. Its header comment states the weights are deliberately not
fetched in CI.

The AppImage is accepted by the AppImage catalogue because `.#default`
installs a `.desktop` entry and icons from `packaging/linux/`. nix-appimage's
`extra-files.sh` looks for `share/applications/*.desktop` whose `Exec=` basename
matches the bundled program (`vt`), copies the `Icon=` file in from
`share/icons/hicolor/`, and derives the AppDir's `.DirIcon` from it. When any of
that is missing it gives up silently and `appdir-lint.sh` then fails the whole
AppImage with `FATAL: .DirIcon is missing` (see
<https://github.com/AppImage/appimage.github.io/pull/8908>).
`tests/linux/test_appimage_packaging.py` pins the pieces.

`.github/workflows/ci.yml` is separate (push/PR only) and runs the four-OS test
matrix. It does not publish anything.

**Consequences:**

- There is **no automated model upload**, by design and by omission — and none
  is needed. The workflow's `files:` glob does not include the weights, and the
  weights do not belong on a version tag at all; they live in their own bundle
  tag that changes only when `REVISION` does.
- CI *could not* build them even if the glob were widened: the GitHub-hosted
  runner has no Hugging Face cache and no `dist/model/`. Only the maintainer's
  machine does. (Widening the glob without also providing the files would make
  the release job fail on `fail_on_unmatched_files: true`.)
- **Windows standalone ZIP** is built in CI on `windows-latest`. When building locally on Linux/POSIX, PyInstaller cannot cross-compile Windows EXEs, so local Windows builds must run on a Windows host via `scripts\build.bat`.
- **The single-file installer** (`platforms/windows/installer.iss`, compiled by
  `ISCC.exe` in the Windows job) is a wrapper around that same bundle. Inno Setup
  6.3+ is preinstalled on the `windows-latest` runner, so nothing is installed in
  CI; the asset name is unversioned, so the app version is passed in as
  `/DAppVersion=` and the script refuses to build without it
  (`#ifndef AppVersion` → `#error`). The script has no `[UninstallDelete]`
  **by design**: weights, config and logs live under `%APPDATA%\vt`, and a
  hand-placed `models\` directory can live inside `{app}` — deleting either on
  uninstall would throw away ~4 GB that the next install re-downloads.
  `tests/windows/test_windows_installer.py` pins all of that structurally,
  because neither this repo nor the Linux CI can compile an `.iss`.
- **It is deliberately not `PyInstaller --onefile`.** A true single-file exe
  unpacks the entire ~1.1 GB payload into a fresh `%TEMP%\_MEIxxxxxx` on every
  launch (nothing is cached between runs) and would also force the pinned
  `llama-server` set into the archive instead of beside the exe. An installer
  extracts once and the app then starts fast. See `platforms/windows/README.md`.

---

## The model assets

### Naming

```
cohere-transcribe-<revision>.part<N>.xz     # the weights
cohere-transcribe-<revision>.SHA256SUMS     # "<sha256>  <filename>" per part
```

with `<revision> = 499888924f5f1313b48ab0686c8f3a94178a4709` (the
`CohereLabs/cohere-transcribe-03-2026` HF revision).

Those assets are attached to a **model bundle tag**, not to an app release:

```
model-cohere-<revision[:12]>               # e.g. model-cohere-499888924f5f
```

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

Usually you do not call this directly — `scripts/publish_model_bundle.sh --build`
runs it for you and then publishes. Its job: tar the flat model files plus the
Apache-2.0 `LICENSE`/`NOTICE`, split the tar into raw byte-range parts (a tar
concatenates cleanly across any byte boundary), xz-compress each part, write
`SHA256SUMS`, then **verify** by reassembling, extracting, and re-hashing
`model.safetensors` against the known HF blob hash.

```bash
# Auto-locates the snapshot in ~/.cache/huggingface/hub
nix develop --command python scripts/prepare_model_release.py --out dist/model

# Or point it at an explicit flat dir (e.g. an existing models/cohere)
nix develop --command python scripts/prepare_model_release.py \
  --model-dir models/cohere --out dist/model
```

Useful flags: `--parts N`, `--xz-level N` (default 6; 9 gains almost nothing on
weights), `--skip-verify` (don't). They pass straight through
`publish_model_bundle.sh --build`, so `-- --parts 3` works there too.

It refuses to finish if verification fails, so a green run is a publishable run.
Redistribution is legitimate because the model is Apache-2.0; the LICENSE +
NOTICE are bundled into the tar by the script and the license text lives at
`config/licenses/Cohere-Apache-2.0.txt` (git-tracked).

**Bumping the revision is a one-file change.** `REVISION` and
`SAFETENSORS_SHA256` live only in `src/model_download.py`.
`scripts/prepare_model_release.py` and `src/transcribe_cohere.py` import them
(previously all three held their own copy, which could drift silently), and
`tests/shared/test_model_download.py::test_model_revision_lives_only_in_model_download`
fails if either reintroduces one. Update the asset filename references in this
document as well, since those are prose.

---

## Publishing a new model revision

**Only when `REVISION` changes.** Weights are keyed by model revision, so a new
app version is not a reason to run this — drop as many versions as you like and
upload nothing.

One command does the whole job — package *and* publish:

```bash
# 1. Show the plan without touching anything or running xz.
nix develop --command ./scripts/publish_model_bundle.sh --build --dry-run

# 2. Do it. Packaging is CPU-heavy; the upload is ~2.9 GB and happens once per
#    revision, not once per release.
nix develop --command ./scripts/publish_model_bundle.sh --build
```

Re-running is cheap and safe: the upload is **idempotent** (already-published
parts are skipped by comparing remote digests), so a dropped upload is fixed by
simply re-running. That is also how you answer "do I need to publish anything?"
— run it and watch for `already published`.

Omit `--build` to publish assets already sitting in `dist/model/`. Extra flags
go to the packaging step, e.g.
`./scripts/publish_model_bundle.sh --build -- --parts 3`.

The script reads `REPO_SLUG`, `MODEL_BUNDLE_TAG` and `REVISION` straight out of
`src/model_download.py`, so it cannot disagree with what the client requests. It
verifies the local parts against `SHA256SUMS` first, creates the bundle release
as a **prerelease** (keeping it out of `/releases/latest`, which is where the
AppImage is found), then:

1. uploads each part and re-reads its remote digest to confirm it landed,
2. **only then** uploads `SHA256SUMS`.

That order is the whole point. `SHA256SUMS` is what makes a bundle
*discoverable*: publishing it before the parts advertises weights that are not
there, which is exactly how a mid-upload bundle used to hijack the client. If
any part fails to verify, the script aborts **without** publishing the manifest,
so a half-uploaded bundle stays invisible instead of becoming a broken one.

Practical notes:

- **Auth** comes from your **system keyring** (secret-service), not from
  `~/.config/gh/hosts.yml` — that file has no token, which makes it look like
  you are logged out when you are not. Required scope is `repo`.
  Check with `nix develop --command gh auth status`.
- This is a multi-gigabyte upload, but only the first time a revision is
  published. On a slow link, run it somewhere with real bandwidth. `gh` does not
  resume a part; a dropped part is simply not registered and gets uploaded
  again.
- Do **not** hand-upload assets to an app release tag with
  `gh release upload`. Weights on a version tag are never read by the client and
  only create a second, unused home for them.
- If `nix run nixpkgs#gh` fails with `Truncated tar archive detected`, the
  cached `nixpkgs-unstable` channel tarball is corrupt. Clear it
  (`rm -rf ~/.cache/nix/tarball-cache*`) or just use the dev shell, which
  resolves from `flake.lock` and is unaffected.

---

## The pinned llama-server bundle

The Windows formatter backend runs a pinned, redistributable `llama-server`
build. Its single declaration home is
`src/voice_transcriber/llama_server_bundle.py`; the publisher reads every
constant back out of it, so the two cannot drift.

| | |
| :--- | :--- |
| Upstream | `ggml-org/llama.cpp` nightly `b11429` |
| Commit | `d81235049384534c167caea52b85a694f6103d14` |
| Asset | `llama-b11429-bin-win-cpu-x64.zip` — 19,398,918 bytes (~18.5 MiB), 51 files, ~47.2 MiB unpacked |
| SHA-256 | `1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe` |
| Our tag | `tool-llama-server-b11429`, a **pre-release** |
| Attestation | <https://github.com/ggml-org/llama.cpp/attestations/52904614> |

**Why a nightly.** Upstream publishes no binary on its stable tags: the `v0.6.0`
release object carries exactly one asset, `nightly-tag.txt`, whose content is the
`b`-tag pointing at the *same commit*. Upstream's `docs/release.md` says
releases are not published to GitHub Releases, only nightly/development builds.
`b11429` is therefore the only coherent prebuilt pin, and it is the one matching
a tagged stable release rather than an arbitrary point on `master`. The binary
reports `0.6.0-dev` because nightlies are built with `LLAMA_BUILD_IS_DEV=ON` —
do not parse that as a version, and do not expect it to equal the tag.

**Cross-checking the download.** The pinned digest is recomputed locally and
agrees with the GitHub API's per-asset `digest` field; upstream also publishes
SLSA build provenance, which is an independent confirmation of the same value:

```bash
gh attestation verify --owner ggml-org llama-b11429-bin-win-cpu-x64.zip
```

**Publishing it** (`scripts/publish_llama_server_bundle.sh`) fetches
`UPSTREAM_URL`, verifies `ASSET_SHA256` **before touching GitHub**, creates
`tool-llama-server-b11429` as a pre-release, and uploads the archive first and
`SHA256SUMS` last — the same "SHA256SUMS makes a bundle discoverable" rule as
the model weights. A digest mismatch, or a failed archive upload, aborts
**without** publishing the manifest. Re-running is idempotent (remote digests are
compared), `--dry-run` prints the plan and changes nothing, and the download is
cached under `dist/llama-server/`.

**This bundle is re-published only when the pin changes** — never per app
release. Bumping `PIN_TAG` / `PIN_COMMIT` / `ASSET_*` in
`llama_server_bundle.py` renames `BUNDLE_TAG`, which is what forces a fresh
publish; every app release in between resolves the same tag.

**Licence notices.** Redistributing the archive creates four obligations, all
recorded in `config/licenses/LlamaServer-LICENSES.txt` and summarised in
`LlamaServer-NOTICE.txt`: llama.cpp/ggml (MIT), cpp-httplib (MIT, statically
compiled), BoringSSL (Apache-2.0, statically linked; fetched from its pinned tag
because it is not in the llama.cpp repo) and the LLVM OpenMP runtime
(Apache-2.0 WITH LLVM-exception). Only `LICENSE-LLVM-OpenMP` travels inside the
archive; the other three are ours to supply. The archive also omits the
Microsoft VC++ runtime, which is a machine prerequisite rather than something we
may copy.

---

### Release ordering, and what the release job should do

The bundle must be published **before** a release that fetches it: the runtime URL
(`pin.bundle_url(model_download.REPO_SLUG)`) 404s until
`scripts/publish_llama_server_bundle.sh` has run. There is intentionally no
upstream fallback.

The Windows release job currently supplies nothing, so it warns and bundles no
server (see `docs/TODO.md`, the Windows-build entry). To ship one, the job should:

1. fetch `pin.UPSTREAM_URL`, verifying `pin.ASSET_SHA256`;
2. pass the archive as `VT_LLAMA_SERVER_ASSET` (a digest is then checked by
   default — `VT_LLAMA_SERVER_SHA256` only overrides it for a deliberate alternate);
3. leave `PATH` alone — the build must never resolve the server from it (CWE-427).

`build_offline.py` also reports the Microsoft VC++ runtime as a **prerequisite**:
the upstream archive does not contain `MSVCP140.dll`/`VCRUNTIME140*.dll`, so a
machine without the redistributable cannot start the server even with the whole
set copied.

## Verifying a release

```bash
gh release view v1.4.1
```

Expect exactly **four** assets:

| Asset | ~Size |
| :--- | :--- |
| `vt-x86_64.AppImage` | ~1.14 GB |
| `VoiceTranscriber-windows-x86_64.zip` | ~373 MiB |
| `VoiceTranscriber-windows-x86_64-setup.exe` | ~373 MiB |
| `REQUIREMENTS.txt` | <2 KB |

plus GitHub's own `Source code (zip)` / `Source code (tar.gz)`, which every tag
gets automatically and CI does not build. Releases predating `REQUIREMENTS.txt`
legitimately have **two** assets and no manifest — `v1.4.1` and `v1.4.0` do.
The Windows zip and the installer are the same PyInstaller bundle: the installer
is the primary download (one file, per-user install, Start Menu entry), the zip
is the portable fallback. Both asset names are unversioned on purpose, so
`releases/latest/download/<name>` is a stable link; the version lives inside the
installer (`/DAppVersion=`, read from `voice_transcriber.__version__`, never the
tag) and in the app itself.

`REQUIREMENTS.txt` is the manifest of what this build resolves at run time: one
block per registered model bundle (tag, base URL, `SHA256SUMS` name, revision,
per-file digests) and one for the pinned `llama-server`. It is **generated** by
`scripts/write_release_requirements.py`, which reads the values out of
`model_download.MODELS` and `llama_server_bundle` — the same declarations the
client uses — so it cannot drift and must never be hand-edited or hand-uploaded.
Regenerate locally with:

```bash
nix develop --command python scripts/write_release_requirements.py \
  --tag v1.4.2 --out REQUIREMENTS.txt
```

Every bundle it names has to be published **before** the release that names it,
which is why publishing a bundle precedes a release that fetches it ("Release
ordering" below).

`release.yml` builds the AppImage on `ubuntu-latest` and the Windows ZIP on
`windows-latest`, each with `fail_on_unmatched_files: true`, so a build that
produces nothing fails the job instead of publishing a release with no asset.
Only `x86_64` is built — `aarch64-linux` and macOS packaging are open (see
TODO.md); the asset names carry the architecture so a future arm build can attach
to the same release.

The Windows ZIP deliberately ships **no** `llama-server`: the pinned backend is
fetched on demand at runtime (see "The pinned llama-server bundle"), so its
absence is expected — and the build prints a `::warning::` saying exactly that,
which is not a failure.

An app release carrying weight assets means someone hand-uploaded them; they are
dead weight, because the client resolves weights from the bundle tag, never from
a version tag.

Check the bundle, which is what first-run users actually download:

```bash
TAG=$(nix develop --command python -c 'import sys; sys.path.insert(0,"src");
import model_download as m; print(m.MODEL_BUNDLE_TAG)')
nix develop --command gh release view "$TAG"
```

Expect `part1.xz`, `part2.xz` and `SHA256SUMS` — and **not** `vt.AppImage`.

End-to-end check that the *client* path works — i.e. what a first-run user gets.
This exercises the real download/verify/install code.

> **Warning: this pulls the full ~2.9 GB of weights.** Run it on a good
> connection — not on a metered or tethered link. It is not part of routine
> release verification.

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

## Where the weights live

In `src/model_download.py`:

```python
REPO_SLUG            = "jjamesmartiin/voice-transcriber"
MODEL_BUNDLE_TAG     = f"model-cohere-{REVISION[:12]}"
DEFAULT_RELEASE_BASE = f"https://github.com/{REPO_SLUG}/releases/download/{MODEL_BUNDLE_TAG}"
```

The client never consults `/releases/latest`. That tag belongs to the AppImage,
and resolving weights through it is what previously let a mid-upload bundle
shadow a complete one. The bundle URL is instead **derived from `REVISION`**:

- **A new app release uploads nothing.** Same `REVISION` → same tag → same
  assets. Publishing weights is a once-per-revision job, not a per-version one.
- **Bumping `REVISION` renames the tag**, which is what forces a fresh publish
  when — and only when — the weights actually change.
- Weights can never silently come from the wrong revision: the manifest name
  embeds the revision (`cohere-transcribe-<rev>.SHA256SUMS`), so a tag holding a
  *different* revision 404s on the manifest and is skipped.

Candidate order is `[requested base, DEFAULT_RELEASE_BASE]`, deduplicated — an
explicit override or `VT_MODEL_RELEASE_BASE` is tried first, with the canonical
bundle still appended as a fallback. Before committing to a candidate the client
**HEAD-checks every part named in the manifest**, because a manifest can name
parts that are absent (an aborted or hand-managed publish). That check means a
bundle that is mid-upload or was abandoned is skipped rather than breaking the
install; `scripts/publish_model_bundle.sh` also publishes in the correct order —
parts first, manifest last — and refuses to publish a manifest for missing
parts.

`LEGACY_RELEASE_BASE` (a hard-coded `v1.1.0` fallback) was removed once
`model-cohere-499888924f5f` was published.

History: `v1.1.1` shipped AppImage-only and served weights from the `v1.1.0`
tag. `v1.2.0` (cut just before the revision-keyed scheme) tried
`/releases/latest` and then fell back to `v1.1.0`. From the revision-keyed
bundle onward, no app release carries weights at all.

### Which releases are disposable, and which are not

**Never delete a `model-*` or `tool-*` bundle release.** Its tag *is* the
runtime download URL for every shipped app: the client resolves
`.../releases/download/<bundle tag>/<manifest>` at first run and on demand. The
bundle is also invisible to `/releases/latest` (it is a pre-release), so it never
competes with an app download.

**App releases are the disposable ones.** No current client resolves anything
through an app tag, so deleting an old `vX.Y.Z` release costs only the
bookmarked download URL and any third-party catalogue entry that pinned it.
Delete the release, not the git tag: `gh release delete <tag>` keeps the tag
unless you add `--cleanup-tag`, and the tag is what `CHANGELOG.md`'s compare
links need.

**Known drift (checked against the API 2026-10-12):** the `v1.1.0`, `v1.1.1` and
`v1.2.0` releases **no longer exist** — the API 404s them. Any surviving
`v1.1.1`/`v1.2.0` install therefore can no longer fetch weights, and has to be
pointed at `model-cohere-499888924f5f` or given the files via `VT_MODEL_DIR`.
Nothing current is affected, because today's client reads only the
revision-derived tag. An earlier revision of this document claimed those assets
*were* still there and must be kept — that was already false when it was read.

**Pruned 2026-10-12:** `v1.2.1`, `v1.3.0` and `v1.3.1` were deleted under that
rule — release objects and assets removed, git tags kept, so `CHANGELOG.md`'s
compare links still resolve. Nothing a shipped client fetches was deleted. Their
assets were (all `vt-x86_64.AppImage` + `VoiceTranscriber-windows-x86_64.zip`):
`v1.2.1` sha256 `d31080ed…`/`2c75f331…`, `v1.3.0` `01c8ef85…`/`4d2c2193…`,
`v1.3.1` `7d890099…`/`a8413b74…`. Re-uploading a deleted asset is not possible
from here: the old bundles are gone from the build host too, so re-building the
same tag is the only way back.

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
| `VT_MODEL_RELEASE_BASE` | Override the asset base URL (default: the revision-derived `model-cohere-<rev12>` bundle tag) |
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
