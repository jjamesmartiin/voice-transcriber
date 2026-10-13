"""Release layout: where our assets live, as opposed to what they contain.

A leaf module with no imports, on purpose. Two modules need the same tag:

* ``model_download`` declares the weights registry and already imports
  ``http.client``, ``lzma`` and ``tarfile`` for the installer;
* ``llama_server_bundle`` is the formatter's pin declaration and must stay
  importable with no network stack at all — pinned by
  ``tests/shared/test_llama_server_bundle.py::test_importing_the_pin_starts_nothing_and_fetches_nothing``,
  which imports it with ``socket.socket`` and ``subprocess.Popen`` set to None.

Declaring the tag in either one would make the other pull in a whole subsystem
for one string, so it is declared here and nowhere else. What belongs here is
only ever the *address* of a release; the pins and digests that say what is
inside one stay with the artifact they describe.
"""
from __future__ import annotations

#: One release hosts every prerequisite, so the releases list has a single entry
#: for all of them and a model revision bump adds assets to it rather than
#: minting a tag of its own. Prerequisites are addressed by *content*: the asset
#: names embed the revision (`cohere-transcribe-<rev>.partN.xz`) and so does each
#: manifest, which is what stops a stale or half-published bundle being mistaken
#: for the one being requested — a bundle for another revision 404s on its own
#: revision-named manifest. What that gives up is per-revision isolation: old
#: revisions' assets stay in this release for the older app versions that ask for
#: them, so the release grows where the tag list used to. See docs/releasing.md.
PREREQUISITES_TAG = "PREREQUISITES"

#: Title and body of that release. Both publishers
#: (``scripts/publish_model_bundle.sh`` and ``scripts/publish_llama_server_bundle.sh``)
#: create it if it is missing and read these back out, so whichever runs first
#: produces the same entry instead of one named after itself.
PREREQUISITES_TITLE = "Prerequisites (model weights and formatter backend)"

#: One paragraph, deliberately without newlines: the publishers read it back
#: through a shell ``read``, so an embedded newline would arrive truncated.
PREREQUISITES_NOTES = (
    "Everything a first run needs besides the app itself: the ASR weights, and "
    "the optional formatter's weights and pinned llama-server backend. Not an "
    "app release: this one tag hosts prerequisites for every app version, so a "
    "new app release re-uploads nothing and a model revision adds assets here "
    "instead of getting a release of its own. Each bundle is named for the "
    "revision or upstream pin it was built from and has its own SHA256SUMS, so "
    "a half-published or superseded bundle is never mistaken for the one being "
    "requested. See docs/releasing.md."
)

__all__ = ["PREREQUISITES_TAG", "PREREQUISITES_TITLE", "PREREQUISITES_NOTES"]
