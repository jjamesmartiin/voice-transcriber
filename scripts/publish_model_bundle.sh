#!/usr/bin/env bash
# Publish a model weights bundle for a revision pinned in src/model_download.py.
#
# RUN THIS ONLY WHEN A MODEL'S `revision` CHANGES. Weights are addressed by model
# revision (tag `model-<name>-<rev12>`), not by app release, so dropping a new app
# version requires running nothing at all — v1.2.1, v1.3.0, ... all resolve to the
# same bundle. Not having to re-upload gigabytes per release is the whole point.
#
# The registry names every model this script can publish: `cohere` (~2.8 GB),
# `formatter` (~462 MiB S1-mini GGUF) and `diarization` (~40 MB sherpa-onnx
# graphs). Nothing below is model-specific; --model selects the entry and every
# constant (repo, tag, prefix, display name) is read back out of the spec.
#
# Ordering is the part that actually matters. The parts go up first, and
# SHA256SUMS is published LAST, only once every part has been verified present on
# the remote. SHA256SUMS is what makes a bundle discoverable, so publishing it
# early advertises weights that are not there. (The client also HEAD-checks each
# part before trusting a manifest, but do not lean on that — get the order right.)
#
# Usage:
#   ./scripts/publish_model_bundle.sh              publish the existing dist/model assets for cohere
#   ./scripts/publish_model_bundle.sh --build      package them first, then publish
#   ./scripts/publish_model_bundle.sh --model NAME publish a specific registry model
#                                                  (cohere, formatter, diarization;
#                                                  default: cohere)
#   ./scripts/publish_model_bundle.sh --dry-run    show the plan, change nothing
#
# Extra flags are forwarded to scripts/prepare_model_release.py with --build
# (e.g. --parts N, --xz-level N, --model-dir DIR).
set -euo pipefail

cd "$(dirname "$0")/.."

BUILD=0
MODEL="${VT_MODEL:-cohere}"
DRY_RUN="${DRY_RUN:-0}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --build)   BUILD=1; shift ;;
        --model)   MODEL="$2"; shift 2 ;;
        --model=*) MODEL="${1#--model=}"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --)        shift; break ;;
        *)         break ;;   # packaging flags are forwarded, not consumed here
    esac
done

PY=""
for c in python python3; do
    if command -v "$c" >/dev/null 2>&1; then PY=$c; break; fi
done
if [ -z "$PY" ]; then
    echo "error: no python on PATH. Run this inside the dev shell:" >&2
    echo "  nix develop --command ./scripts/publish_model_bundle.sh" >&2
    exit 1
fi

if command -v gh >/dev/null 2>&1; then
    GH=(gh)
else
    GH=(nix run nixpkgs#gh --)
fi

# Read the constants out of the client module so the publisher can never
# disagree with what the client will actually request.
read -r REPO TAG REV PREFIX DISPLAY < <(MODEL="$MODEL" "$PY" -c \
    'import os, sys; sys.path.insert(0, "src"); import model_download as m; \
     s = m.get_spec(os.environ["MODEL"]); \
     print(m.REPO_SLUG, s.bundle_tag, s.revision, s.asset_prefix, s.display_name)')

OUT="${OUT:-dist/model}"
SUMS="$OUT/${PREFIX}.SHA256SUMS"
DRY_RUN="${DRY_RUN:-0}"

echo "model  : $MODEL ($DISPLAY)"
echo "repo   : $REPO"
echo "tag    : $TAG   (revision ${REV:0:12})"
echo "source : $OUT"
[ "$DRY_RUN" = 1 ] && echo "mode   : DRY RUN (no changes will be made)"
echo

# 1. Package the assets from the local model snapshot when asked. This must
#    come before the existence check below, since it is what creates the files.
if [ "$BUILD" = 1 ]; then
    if [ "$DRY_RUN" = 1 ]; then
        echo "would package assets into $OUT from the local model snapshot:"
        echo "  $PY scripts/prepare_model_release.py --model $MODEL --out $OUT $*"
        echo
    else
        echo "Packaging from the local model snapshot: tar -> split -> xz -> SHA256SUMS -> verify."
        echo "CPU-heavy and serial; this is the slow half."
        echo
        "$PY" scripts/prepare_model_release.py --model "$MODEL" --out "$OUT" "$@"
        echo
    fi
fi

if [ ! -f "$SUMS" ]; then
    if [ "$DRY_RUN" = 1 ] && [ "$BUILD" = 1 ]; then
        echo "Dry run complete: the assets are not built yet, so there is no publish"
        echo "plan to show. Re-run without --dry-run to package and publish for real."
        exit 0
    fi
    echo "error: $SUMS not found." >&2
    if [ "$BUILD" = 1 ]; then
        echo "  (--build was given but packaging produced nothing)" >&2
    else
        echo "  Build the assets first, or let this script do it:" >&2
        echo "    $0 --build" >&2
    fi
    exit 1
fi

# 2. Local assets must be self-consistent before anything is published.
echo "Verifying local parts against $(basename "$SUMS") ..."
(cd "$OUT" && sha256sum -c "$(basename "$SUMS")")

mapfile -t PARTS < <(awk '{print $2}' "$SUMS")
[ "${#PARTS[@]}" -gt 0 ] || { echo "error: $SUMS lists no parts" >&2; exit 1; }
for p in "${PARTS[@]}"; do
    case "$p" in
        "${PREFIX}".part*.xz) ;;
        *) echo "error: $SUMS lists an unexpected entry: $p" >&2; exit 1 ;;
    esac
done

# 3. The release object hosts the assets. --prerelease keeps it out of
#    /releases/latest, which is where the AppImage is published.
if ! "${GH[@]}" release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    if [ "$DRY_RUN" = 1 ]; then
        echo "would create weights release $TAG (prerelease)"
    else
        echo "Creating weights release $TAG ..."
        "${GH[@]}" release create "$TAG" --repo "$REPO" --prerelease \
            --title "Model bundle ${REV:0:12}" \
            --notes "${DISPLAY} weights for revision \`${REV}\`.

Not an app release: this tag exists only to host the model assets, so app
releases never have to re-upload them. See docs/releasing.md."
    fi
fi

state=$(mktemp)
trap 'rm -f "$state"' EXIT
refresh() {
    if ! "${GH[@]}" api "repos/$REPO/releases/tags/$TAG" \
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
    "${GH[@]}" release upload "$TAG" "$1" --repo "$REPO" --clobber
    refresh
    have=$(have_of "$name")
    if [ "$have" != "sha256:$want" ]; then
        echo "  ! $name remote digest is '${have:--}', expected 'sha256:$want'" >&2
        return 1
    fi
    echo "  + $name verified"
}

# 4. Parts first.
for p in "${PARTS[@]}"; do
    if ! publish "$OUT/$p"; then
        echo >&2
        echo "aborting: SHA256SUMS was NOT published, so no client can discover" >&2
        echo "$TAG yet. Fix the failing part and re-run; already-good parts and" >&2
        echo "already-uploaded parts are skipped, so a re-run is cheap." >&2
        exit 1
    fi
done

# 5. SHA256SUMS last, and only now.
echo
echo "All parts verified on the remote; publishing the manifest."
publish "$SUMS"

echo
if [ "$DRY_RUN" = 1 ]; then
    echo "Dry run complete: no changes made."
else
    echo "Bundle $TAG is live. Nothing to do for the next app release."
fi
