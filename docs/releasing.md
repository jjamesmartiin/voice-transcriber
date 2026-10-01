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

# 5. NOTHING TO DO FOR THE WEIGHTS. They live in a revision-keyed model
#    bundle and are re-published only when REVISION changes — see
#    "Where the weights live".

# 6. Verify the release carries exactly one asset (see "Verifying a release").
```

---

## Version bump

The version string is duplicated and **not** derived from the git tag. Bump all
four before tagging, or the AppImage/UI/catalogue metadata will report a stale
version:

| File | What |
| :--- | :--- |
| `flake.nix` | `version = "1.2.0";` in the flake `outputs` let-block |
| `flake.nix` | `version = "1.2.0";` in the `pkgs.stdenv.mkDerivation` attrs |
| `src/tui.py` | `def __init__(self, app_version="1.2.0", ...)` |
| `packaging/linux/*.appdata.xml` | `<release version="1.2.0" date="YYYY-MM-DD"/>` |

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

The AppImage is only accepted by the AppImage catalogue because `.#default`
installs a `.desktop` entry and icons from `packaging/linux/`. nix-appimage's
`extra-files.sh` looks for `share/applications/*.desktop` whose `Exec=` basename
matches the bundled program (`vt`), copies the `Icon=` file in from
`share/icons/hicolor/`, and derives the AppDir's `.DirIcon` from it. When any of
that is missing it gives up silently and `appdir-lint.sh` then fails the whole
AppImage with `FATAL: .DirIcon is missing` (see
<https://github.com/AppImage/appimage.github.io/pull/8908>).
`tests/linux/test_appimage_packaging.py` pins the pieces.

`.github/workflows/ci.yml` is separate (push/PR only) and runs the three-OS test
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

## Verifying a release

```bash
nix develop --command gh release view v1.2.0
```

Expect exactly **one** asset:

| Asset | ~Size |
| :--- | :--- |
| `vt-x86_64.AppImage` | ~1.18 GB |

Only `x86_64-linux` is built today (see TODO.md). The asset name carries the
architecture so a future aarch64 build can attach to the same release.

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
`model-cohere-499888924f5f` was published. The `v1.1.0` assets themselves stay:
already-shipped clients (`v1.1.1`, `v1.2.0`) still resolve weights through that
tag, so do not delete them.

History: `v1.1.1` shipped AppImage-only and serves weights from the `v1.1.0`
tag. `v1.2.0` (cut just before the revision-keyed scheme) tries
`/releases/latest` and then falls back to `v1.1.0`. From the revision-keyed
bundle onward, no app release carries weights at all.

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
