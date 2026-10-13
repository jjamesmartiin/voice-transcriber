#!/usr/bin/env python3
"""Drift guards for the pinned ``llama-server`` build.

These assert internal consistency, not network state: they cannot tell you whether
the digest is *correct*, only that the declaration cannot drift into a shape that
silently disagrees with itself (a tag that no longer appears in the URL, a stub
shipped without the DLL it needs, a digest that is not a digest).

Hermetic and model-free, so it runs in every CI job including the pip-only Windows
and macOS ones.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

from voice_transcriber import llama_server_bundle as pin
from voice_transcriber import releases


def test_the_tag_appears_in_every_derived_name():
    """A bump that misses one of these is how a release ships the wrong binary."""
    assert pin.PIN_TAG in pin.ASSET_NAME
    assert pin.PIN_TAG in pin.UPSTREAM_URL
    assert pin.ASSET_NAME in pin.UPSTREAM_URL
    assert pin.UPSTREAM_REPO in pin.UPSTREAM_URL
    # The *tag* is deliberately not pin-derived any more: every prerequisite
    # lives in one release, which is a release-organisation decision rather than
    # part of the pin. The pin still names the asset, which is what the client
    # verifies (ASSET_SHA256), so a wrong binary cannot be served.
    assert pin.BUNDLE_TAG == releases.PREREQUISITES_TAG
    # ...and the manifest is named for the archive, so a shared release never has
    # a bare `SHA256SUMS` claiming to cover everything in it.
    assert pin.MANIFEST_NAME == f"{pin.ASSET_NAME}.SHA256SUMS"


def test_the_digest_is_a_sha256_and_the_attestation_is_ours_to_check():
    assert re.fullmatch(r"[0-9a-f]{64}", pin.ASSET_SHA256)
    assert pin.ATTESTATION_URL.startswith(f"https://github.com/{pin.UPSTREAM_REPO}/")


def test_the_asset_is_the_windows_cpu_x64_zip():
    """CPU-only dispatch is the whole reason one asset is enough; a GPU variant
    would drag in a runtime this app does not ship and cannot test."""
    assert pin.ASSET_NAME.endswith("-win-cpu-x64.zip")
    assert "-cuda" not in pin.ASSET_NAME and "-vulkan" not in pin.ASSET_NAME
    assert pin.ASSET_BYTES < pin.UNPACKED_BYTES, "a ~51-file archive should expand"


def test_the_executable_is_never_pinned_without_what_it_imports():
    """`llama-server.exe` is a ~9 KB stub whose only local import is
    `llama-server-impl.dll`, so an "is the exe present?" check is not a check.

    This is the regression that a single-file pin would reintroduce: the server
    resolves and then fails to start.
    """
    members = set(pin.REQUIRED_MEMBERS)
    for needed in ("llama-server.exe", "llama-server-impl.dll", "llama.dll",
                   "ggml-base.dll", "libomp.dll"):
        assert needed in members, needed
    assert any(m.startswith("ggml-cpu-") for m in members), (
        "the CPU dispatch ladder is what lets one asset serve every x64 machine")


def test_the_vc_runtime_is_recorded_as_a_prerequisite_not_a_member():
    """The archive does not contain the Microsoft VC++ runtime, so it must be
    reported to the user as a prerequisite — it is never something we copy."""
    for dll in pin.RUNTIME_PREREQUISITES:
        assert dll not in pin.REQUIRED_MEMBERS, (
            f"{dll} comes from the VC++ redistributable, which the archive does not "
            f"ship; treating it as a member would fail every ingest")
    assert "VCRUNTIME140.dll" in pin.RUNTIME_PREREQUISITES


def test_redistribution_obligations_are_declared():
    """We re-publish someone else's binary, so the notices are not optional."""
    joined = " ".join(pin.LICENCE_NOTICES)
    assert "MIT" in joined and "BoringSSL" in joined and "OpenMP" in joined


def test_importing_the_pin_starts_nothing_and_fetches_nothing():
    """It is a declaration: no sockets, no spawn, no filesystem writes."""
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import socket, subprocess; "
        "socket.socket = None; subprocess.Popen = None; "
        "from voice_transcriber import llama_server_bundle as pin; "
        "assert pin.PIN_TAG; print('OK')" % os.path.abspath(
            os.path.join(os.path.dirname(__file__), '../../src'))
    )
    import subprocess as sp
    result = sp.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_the_app_fetches_our_copy_not_upstreams():
    """Republishing is only worth anything if the app fetches what we published.

    Fetching `UPSTREAM_URL` at runtime would put users on a third party's nightly
    URL, bypass our own pre-release, and detach the licence notices in
    `config/licenses/` from the copy being distributed. `UPSTREAM_URL` is the
    *publisher's* source.
    """
    ours = pin.bundle_url("example/voice-transcriber")
    assert ours != pin.UPSTREAM_URL
    assert pin.BUNDLE_TAG in ours and pin.ASSET_NAME in ours
    assert pin.UPSTREAM_REPO not in ours, "our copy must not be upstream's URL"
    assert "example/voice-transcriber" in ours


def test_the_repo_slug_is_not_declared_twice():
    """`model_download.REPO_SLUG` is the single home for which repo hosts our
    releases; the pin must not grow a second copy of that fact."""
    import pathlib
    source = pathlib.Path(pin.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^REPO_SLUG\s*=", source, re.M), (
        "bundle_url takes the slug as an argument on purpose — see its docstring")
