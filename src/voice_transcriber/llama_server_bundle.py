"""The pinned, redistributable ``llama-server`` build for Windows x64.

**Why this is a nightly.** Upstream publishes no binary on its stable tags: the
``v0.6.0`` release object has exactly one asset, ``nightly-tag.txt``, whose
content is the ``b``-tag pointing at the *same commit*. Upstream's own
``docs/release.md`` says so — *"Currently releases are not published to github
releases, only nightly/development builds are available there."* So the only
coherent prebuilt pin is a ``b``-tag, and it is the one that matches a tagged
stable release rather than an arbitrary point on ``master``.

**Why the exe alone is not the artifact.** ``llama-server.exe`` is a ~9 KB stub
whose only local import is ``llama-server-impl.dll``; that in turn needs
``llama.dll``, ``llama-common.dll``, ``mtmd.dll``, ``ggml.dll``, ``ggml-base.dll``,
``libomp.dll`` and a ``ggml-cpu-*.dll``. The asset is a 51-file ZIP. Copying the
executable by itself produces a bundle that resolves and then fails to start.

**Two things the ZIP does not contain**, both of which bite on a clean machine:

* the Microsoft VC++ runtime (``MSVCP140.dll``, ``VCRUNTIME140.dll``,
  ``VCRUNTIME140_1.dll``) — see :data:`RUNTIME_PREREQUISITES`;
* the licence notices for llama.cpp/ggml, cpp-httplib and BoringSSL (the last is
  statically linked). Only ``LICENSE-LLVM-OpenMP`` ships in the archive, so
  redistribution requires us to supply the rest — see ``config/licenses/``.

This module is the single declaration home for the pin, so the publisher, the
Windows build and the runtime fetch cannot drift apart. It deliberately carries no
behaviour: importing it downloads nothing and starts nothing.
"""

from __future__ import annotations

from voice_transcriber import releases

#: Upstream project the binary comes from.
UPSTREAM_REPO = "ggml-org/llama.cpp"

#: The nightly whose commit is exactly what the ``v0.6.0`` tag points at. Nightlies
#: are built with ``LLAMA_BUILD_IS_DEV=ON``, so the binary reports ``0.6.0-dev`` —
#: do not parse that string as a version, and do not expect it to match the tag.
PIN_TAG = "b11429"
PIN_COMMIT = "d81235049384534c167caea52b85a694f6103d14"
PIN_STABLE_TAG = "v0.6.0"

#: The Windows x64 CPU asset: runtime CPU dispatch with an SSE2 floor and a full
#: ``ggml-cpu-*.dll`` ladder, so one asset covers every x64 Windows machine and no
#: fallback variant is needed. GPU variants exist and are deliberately not used —
#: this app ships CPU-only PyTorch.
ASSET_NAME = "llama-b11429-bin-win-cpu-x64.zip"
ASSET_BYTES = 19_398_918
UNPACKED_BYTES = 49_497_373

#: Recomputed locally and cross-checked against the GitHub API's per-asset
#: ``digest`` field. Upstream also publishes SLSA build provenance, which is an
#: independent confirmation of the same value:
#: ``gh attestation verify --owner ggml-org <file>``.
ASSET_SHA256 = "1283323272b04cd07905816a597a0da810918102de958f4ff6f7bbaa70ed2efe"
ATTESTATION_URL = "https://github.com/ggml-org/llama.cpp/attestations/52904614"

#: Where the archive can be fetched without any of our infrastructure, for a user
#: who would rather do it by hand and verify it themselves.
UPSTREAM_URL = (
    f"https://github.com/{UPSTREAM_REPO}/releases/download/{PIN_TAG}/{ASSET_NAME}"
)

#: The release tag holding the redistributed copy, shared with the model bundles
#: (see ``voice_transcriber.releases.PREREQUISITES_TAG``): one entry in the list for
#: everything a first run needs. This module owns the *pin*; the tag it is
#: published under is a release-organisation decision, so it is imported rather
#: than declared twice.
BUNDLE_TAG = releases.PREREQUISITES_TAG

#: The manifest that ships beside the archive. Not the bare ``SHA256SUMS`` the
#: model bundles would never have used anyway: in a shared release, a file named
#: just ``SHA256SUMS`` sits next to three revision-named manifests and reads as
#: "the" checksum file for the release rather than for this archive.
MANIFEST_NAME = f"{ASSET_NAME}.SHA256SUMS"


def bundle_url(repo_slug: str) -> str:
    """Where the *app* fetches the archive: our pre-release, not upstream.

    Takes the repo slug rather than declaring one, because
    ``model_download.REPO_SLUG`` is already the single declaration of which repo
    hosts our releases — duplicating it here would create two homes for one fact
    and a way for them to disagree.

    Fetching *our* copy is the point of republishing: one host for users, no
    dependency on a third party's nightly staying published, and the licence
    notices in ``config/licenses/`` attach to the copy we distribute. Upstream's
    :data:`UPSTREAM_URL` stays the *publisher's* source, never the runtime's.

    Ordering matters at release time: this URL does not exist until
    ``scripts/publish_llama_server_bundle.sh`` has run, so the bundle must be
    published before a release that fetches it.
    """
    return (f"https://github.com/{repo_slug}/releases/download/{BUNDLE_TAG}/"
            f"{ASSET_NAME}")

#: Files that must arrive together. ``llama-server.exe`` is a stub: it resolves
#: without these and then fails to start, which is why the check is a set and not
#: a single ``os.path.exists``.
REQUIRED_MEMBERS: tuple[str, ...] = (
    "llama-server.exe",
    "llama-server-impl.dll",
    "llama.dll",
    "llama-common.dll",
    "mtmd.dll",
    "ggml.dll",
    "ggml-base.dll",
    "libomp.dll",
    # The CPU dispatch ladder. All of them are shipped so the backend registry can
    # score and pick at runtime; a single hardcoded variant would pick for the user.
    "ggml-cpu-x64.dll",
    "ggml-cpu-sse42.dll",
    "ggml-cpu-sandybridge.dll",
    "ggml-cpu-ivybridge.dll",
    "ggml-cpu-piledriver.dll",
    "ggml-cpu-haswell.dll",
    "ggml-cpu-skylakex.dll",
    "ggml-cpu-cannonlake.dll",
    "ggml-cpu-cascadelake.dll",
    "ggml-cpu-cooperlake.dll",
    "ggml-cpu-icelake.dll",
    "ggml-cpu-zen4.dll",
    "ggml-cpu-alderlake.dll",
    "ggml-cpu-sapphirerapids.dll",
)

#: Supplied by the Microsoft VC++ Redistributable, **not** by the archive. A
#: machine without it fails to launch the server even with every member present,
#: which is a prerequisite to report rather than a file to copy — we cannot
#: redistribute the redistributable's DLLs from the archive.
RUNTIME_PREREQUISITES: tuple[str, ...] = (
    "MSVCP140.dll",
    "VCRUNTIME140.dll",
    "VCRUNTIME140_1.dll",
)

#: Licence obligations created by redistributing this archive. Both upstream
#: notices and our copies live under ``config/licenses/``; ``LICENSE-LLVM-OpenMP``
#: is the one notice the archive does ship, and it must be kept alongside.
LICENCE_NOTICES: tuple[str, ...] = (
    "llama.cpp and ggml (MIT)",
    "cpp-httplib (MIT)",
    "BoringSSL (statically linked; licence fetched from the pinned tag)",
    "LLVM OpenMP runtime (Apache-2.0 WITH LLVM-exception)",
)
