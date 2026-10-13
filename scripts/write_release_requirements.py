#!/usr/bin/env python3
"""
Write REQUIREMENTS.txt: the bundles an app release resolves at run time.

Why: an app release attaches the app runtime only — no weights, no config. The
model weights and the formatter's pinned ``llama-server`` live in their own
pre-release tags, addressed by model revision / upstream pin, and are
re-published only when a pin changes. Anyone mirroring a release, or installing
offline, has to know which of those bundles *this* build asks for.

Every value is read out of the registries (``model_download.MODELS``,
``llama_server_bundle``) rather than restated, so this file cannot disagree with
what the client actually fetches. Stdlib only, no network: it is safe to run on
a runner with no model cache.

Usage:
  python3 scripts/write_release_requirements.py --tag v1.4.2 --out REQUIREMENTS.txt
"""
import argparse
import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))
from voice_transcriber import llama_server_bundle as llama  # noqa: E402
from voice_transcriber import releases  # noqa: E402
from voice_transcriber.model_download import MODELS, REPO_SLUG  # noqa: E402


def render(tag=None):
    """The REQUIREMENTS.txt body. ``tag`` is the app release, or None locally."""
    lines = [
        "Voice Transcriber — release requirements",
        f"Release: {tag or 'unreleased working tree'}",
        "",
        "This release ships the application runtime only: no model weights, no",
        "config. Everything below comes from one prerequisites release, addressed",
        "by content: each bundle is named for the model revision or upstream pin it",
        "was built from and carries its own SHA256SUMS. Nothing is re-uploaded for",
        "an app release, so this list only changes where a revision number does.",
        "",
        f"Prerequisites release: {releases.PREREQUISITES_TAG}",
        f"  https://github.com/{REPO_SLUG}/releases/tag/{releases.PREREQUISITES_TAG}",
        "",
        "Fetch each bundle's parts first and its manifest last: the manifest is what",
        "makes a bundle discoverable, so a half-published one stays visible as",
        "incomplete rather than appearing valid.",
        "",
        "Model bundles",
    ]
    for spec in MODELS.values():
        install = spec.target_subdir or spec.name
        lines += [
            f"  {spec.name} — {spec.display_name}",
            f"    manifest   {spec.asset_prefix}.SHA256SUMS",
            f"    revision   {spec.revision}",
        ]
        for name, digest in sorted(spec.digests.items()):
            lines.append(f"    sha256     {name}  {digest}")
        lines += [
            f"    installs   models/{install}",
        ]
        if spec.download_hint:
            lines[-1] += f" — {spec.download_hint}"
    lines += [
        "",
        "Tool bundle",
        "  llama-server — the optional formatter's backend",
        f"    asset      {llama.ASSET_NAME}",
        f"    manifest   {llama.MANIFEST_NAME}",
        f"    sha256     {llama.ASSET_SHA256}",
        f"    upstream   {llama.UPSTREAM_URL}",
        f"    note       fetched on demand by this build; needs on the host: "
        f"{', '.join(llama.RUNTIME_PREREQUISITES)}",
        "",
        "Not included, by design: the Microsoft VC++ runtime is a machine",
        "prerequisite and is not redistributable with the app.",
        "",
        "Provenance and licence notices for every bundle:",
        f"  https://github.com/{REPO_SLUG}/tree/main/config/licenses",
        "",
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--tag", help="app release tag, e.g. v1.4.2")
    parser.add_argument("--out", default="REQUIREMENTS.txt",
                        help="output path ('-' for stdout)")
    args = parser.parse_args(argv)

    body = render(args.tag)
    if args.out == "-":
        sys.stdout.write(body)
        return 0
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(body)
    print(f"wrote {args.out} ({len(body.splitlines())} lines, "
          f"{len(MODELS)} model bundles + 1 tool bundle)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
