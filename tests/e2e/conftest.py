"""E2E tier fixtures.

``test_live_speaker_mic_loopback.py`` is a *script*, not a pytest module: it plays
reference audio through the speakers and records it back through the microphone, so
it must never run inside an automated pytest invocation (it would make noise and
need a real mic). It is driven directly::

    nix develop --command python tests/e2e/test_live_speaker_mic_loopback.py all

Ignoring it explicitly is what makes this tier's intent unambiguous: without it,
``pytest tests/e2e`` silently collected zero tests from a file whose name says
otherwise, and a reader could not tell whether that was deliberate or broken. It is
deliberate — see ``docs/agent_testing_workflow.md``.
"""
import os

collect_ignore = ["test_live_speaker_mic_loopback.py"]

#: These fixtures make the model generate a whole email or list, which on a busy
#: machine (the ASR model is resident by this point) exceeds the formatter's
#: 3 s latency budget - and an over-budget rewrite is discarded, so the *parity*
#: assertions failed for a reason that has nothing to do with parity. The budget
#: is a latency promise to the user, not a model limit; the tier that owns it is
#: tests/shared/test_formatter.py. This tier measures the model's output.
os.environ.setdefault("VT_FORMATTER_TIMEOUT_S", "20")
