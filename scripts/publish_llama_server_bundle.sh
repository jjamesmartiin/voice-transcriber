#!/usr/bin/env bash
# Publish the pinned, redistributable llama-server build for Windows x64.
#
# RUN THIS ONLY WHEN THE PIN IN src/voice_transcriber/llama_server_bundle.py
# CHANGES. The bundle is addressed by the upstream nightly tag (tag
# `tool-llama-server-<nightly>`), not by app release, so dropping a new app
# version requires running nothing at all: every version resolves to the same
# bundle until the pin moves. Re-uploading ~19 MB per release is not the point,
# and the client never reads a version tag.
#
# Unlike the model weights there is nothing to package: the archive is built and
# published upstream, so this script fetches pin.UPSTREAM_URL and checks it
# against pin.ASSET_SHA256 *before* publishing anything. A digest mismatch means
# upstream re-cut the asset (or the pin is wrong), and aborts the run.
#
# Ordering is the part that actually matters. The archive goes up first and
# SHA256SUMS is published LAST, only once the archive has been verified present
# on the remote. SHA256SUMS is what makes a bundle discoverable, so publishing it
# early advertises a server that is not there. If the archive fails to verify,
# the script aborts WITHOUT publishing the manifest, so a half-uploaded bundle
# stays invisible instead of becoming a broken one.
#
# Usage:
#   ./scripts/publish_llama_server_bundle.sh            fetch, verify, publish the pin
#   ./scripts/publish_llama_server_bundle.sh --dry-run  show the plan, change nothing
#
# The pinned archive is cached in dist/llama-server/ so a re-run is cheap and
# does not re-download it. Set OUT to use another directory.
set -euo pipefail

cd "$(dirname "$0")/.."

DRY_RUN="${DRY_RUN:-0}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --)        shift; break ;;
        *)         break ;;
    esac
done

PY=""
for c in python python3; do
    if command -v "$c" >/dev/null 2>&1; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
    echo "error: no python on PATH. Run this inside the dev shell:" >&2
    echo "  nix develop --command ./scripts/publish_llama_server_bundle.sh" >&2
    exit 1
fi

if command -v gh >/dev/null 2>&1; then
    GH=(gh)
else
    GH=(nix run nixpkgs#gh --)
fi

# Read the constants out of the pin module so the publisher can never disagree
# with what the client will actually fetch. Our own release repo comes from the
# model registry, which is the single source of truth for it.
read -r UPSTREAM_REPO PIN_TAG BUNDLE_TAG ASSET_NAME ASSET_SHA256 UPSTREAM_URL \
        PIN_COMMIT PIN_STABLE_TAG ATTESTATION_URL REPO < <("$PY" -c '
import sys; sys.path.insert(0, "src")
from voice_transcriber import llama_server_bundle as p
from voice_transcriber import model_download as m
print(p.UPSTREAM_REPO, p.PIN_TAG, p.BUNDLE_TAG, p.ASSET_NAME, p.ASSET_SHA256,
      p.UPSTREAM_URL, p.PIN_COMMIT, p.PIN_STABLE_TAG, p.ATTESTATION_URL,
      m.REPO_SLUG)
')

OUT="${OUT:-dist/llama-server}"
ARCHIVE="$OUT/$ASSET_NAME"
SUMS="$OUT/SHA256SUMS"

echo "bundle  : $BUNDLE_TAG"
echo "repo    : $REPO"
echo "upstream: $UPSTREAM_REPO @ $PIN_TAG ($PIN_COMMIT)"
echo "stable  : $PIN_STABLE_TAG (same commit; a nightly is the only prebuilt asset)"
echo "asset   : $ASSET_NAME"
echo "sha256  : $ASSET_SHA256"
echo "attest  : $ATTESTATION_URL"
echo "source  : $UPSTREAM_URL"
[ "$DRY_RUN" = 1 ] && echo "mode    : DRY RUN (no changes will be made)"
echo

if [ "$DRY_RUN" = 1 ] && [ ! -f "$ARCHIVE" ]; then
    echo "DRY RUN: would download the pinned archive, verify its SHA-256, then publish:"
    echo "  1. download $UPSTREAM_URL"
    echo "     -> $ARCHIVE"
    echo "  2. verify sha256 == $ASSET_SHA256 (abort on mismatch)"
    echo "  3. write $SUMS"
    echo "  4. create pre-release $BUNDLE_TAG (if absent)"
    echo "  5. upload $ASSET_NAME, then SHA256SUMS last"
    exit 0
fi

# 1. Fetch the pinned archive, unless a previous run already cached it.
mkdir -p "$OUT"
if [ -f "$ARCHIVE" ]; then
    echo "Using existing $ARCHIVE"
else
    echo "Fetching $ASSET_NAME ..."
    "$PY" - "$UPSTREAM_URL" "$ARCHIVE" <<'PY'
import sys
import urllib.request

url, dest = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as fh:
    total = int(resp.headers.get("Content-Length") or 0)
    seen = 0
    while True:
        chunk = resp.read(1 << 20)
        if not chunk:
            break
        fh.write(chunk)
        seen += len(chunk)
        if total:
            print(f"\r  {seen * 100 // total:3d}%", end="", file=sys.stderr, flush=True)
print(file=sys.stderr)
PY
fi

# 2. The pin must match before anything is published.
echo "Verifying $ASSET_NAME against the pin ..."
actual=$(sha256sum "$ARCHIVE" | awk '{print $1}')
if [ "$actual" != "$ASSET_SHA256" ]; then
    echo >&2
    echo "error: SHA-256 mismatch for $ASSET_NAME" >&2
    echo "  expected: $ASSET_SHA256" >&2
    echo "  actual:   $actual" >&2
    echo "Refusing to publish an archive that does not match" >&2
    echo "src/voice_transcriber/llama_server_bundle.py. This is usually upstream" >&2
    echo "re-cutting an asset under the same tag; re-pin if the new build is wanted." >&2
    exit 1
fi
echo "  + $ASSET_NAME verified"

# 3. Local assets must be self-consistent before anything is published.
echo "Writing $SUMS ..."
(cd "$OUT" && sha256sum "$ASSET_NAME" > SHA256SUMS)
(cd "$OUT" && sha256sum -c SHA256SUMS)

# 4. The release object hosts the assets. --prerelease keeps it out of
#    /releases/latest, which is where the AppImage is published.
if ! "${GH[@]}" release view "$BUNDLE_TAG" --repo "$REPO" >/dev/null 2>&1; then
    if [ "$DRY_RUN" = 1 ]; then
        echo "would create pre-release $BUNDLE_TAG"
    else
        echo "Creating pre-release $BUNDLE_TAG ..."
        "${GH[@]}" release create "$BUNDLE_TAG" --repo "$REPO" --prerelease \
            --title "llama-server ${PIN_TAG} (Windows x64 CPU)" \
            --notes "Pinned llama.cpp ${PIN_TAG} Windows x64 CPU build, redistributed
with its licence notices (see config/licenses/LlamaServer-LICENSES.txt).

Third-party build, not an app release: this tag exists only to host the
redistributed formatter server, and is republished only when the pin in
src/voice_transcriber/llama_server_bundle.py changes, never per app release.
Upstream: ${UPSTREAM_URL}
See docs/releasing.md."
    fi
fi

state=$(mktemp)
trap 'rm -f "$state"' EXIT
refresh() {
    if ! "${GH[@]}" api "repos/$REPO/releases/tags/$BUNDLE_TAG" \
        --jq '.assets[] | "\(.name)\t\(.digest // "-")"' > "$state" 2>/dev/null; then
        [ "$DRY_RUN" = 1 ] && { : > "$state"; return 0; }
        return 1
    fi
}
refresh

want_of() {  # sha256 the asset should have: from SUMS, else computed
    local n="$1" w
    w=$(awk -v n="$n" '$2 == n {print $1}' "$SUMS")
    [ -n "$w" ] || w=$(sha256sum "$OUT/$n" | awk '{print $1}')
    printf '%s' "$w"
}
have_of() { awk -v n="$1" '$1 == n {print $2}' "$state"; }

publish() {  # $1 = path to a local asset; idempotent
    local name want have
    name=$(basename "$1")
    want=$(want_of "$name")
    have=$(have_of "$name")
    if [ "$have" = "sha256:$want" ]; then
        echo "  = $name already published"
        return 0
    fi
    if [ "$DRY_RUN" = 1 ]; then
        echo "  ~ would upload $name ($(stat -c %s "$1") bytes)"
        return 0
    fi
    echo "  > $name ($(stat -c %s "$1") bytes)"
    "${GH[@]}" release upload "$BUNDLE_TAG" "$1" --repo "$REPO" --clobber
    refresh
    have=$(have_of "$name")
    if [ "$have" != "sha256:$want" ]; then
        echo "  ! $name remote digest is '${have:--}', expected 'sha256:$want'" >&2
        return 1
    fi
    echo "  + $name verified"
}

# 5. The archive first.
if ! publish "$ARCHIVE"; then
    echo >&2
    echo "aborting: SHA256SUMS was NOT published, so no client can discover" >&2
    echo "$BUNDLE_TAG yet. Fix the failing upload and re-run; an already-good" >&2
    echo "archive is skipped, so a re-run is cheap." >&2
    exit 1
fi

# 6. SHA256SUMS last, and only now.
echo
echo "Archive verified on the remote; publishing the manifest."
publish "$SUMS"

echo
if [ "$DRY_RUN" = 1 ]; then
    echo "Dry run complete: no changes made."
else
    echo "Bundle $BUNDLE_TAG is live. Nothing to do for the next app release."
fi
