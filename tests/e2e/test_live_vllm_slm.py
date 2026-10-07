#!/usr/bin/env python3
"""Live integration test for the optional vLLM SLM rewrite pass.

Deliberately lives in the e2e tier, not ``tests/shared``: it performs real
network I/O against ``localhost:8000`` and only asserts when a vLLM server is
actually answering, so it has no place in the hermetic, model-free tier that
runs in every CI job. The test skips cleanly when nothing is listening.

Run directly:
    python tests/e2e/test_live_vllm_slm.py
Or as part of the e2e tier:
    ./test.sh e2e
"""

import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))

from post_processor import (
    clean_speech_transcription,
    process_slm_llm_rewrite,
)


class TestLiveVllmSlmIntegration(unittest.TestCase):
    def test_live_vllm_slm_integration(self):
        """vLLM REST API integration: idempotency and retraction accuracy."""
        vllm_url = os.environ.get("VT_VLLM_URL", "http://localhost:8000/v1/chat/completions")
        models_url = vllm_url.replace("/chat/completions", "/models")

        # Check if local vLLM service is active
        try:
            req = urllib.request.Request(models_url)
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status != 200:
                    self.skipTest("vLLM service not responding with 200 OK")
        except Exception:
            self.skipTest("Local vLLM server is not running on port 8000")

        old_env = os.environ.get("VT_ENABLE_SLM")
        old_url = os.environ.get("VT_VLLM_URL")
        try:
            os.environ["VT_ENABLE_SLM"] = "1"
            os.environ["VT_VLLM_URL"] = vllm_url
            raw_input = "i went to the store um and bought some apples... actually oranges"
            expected_target = "I went to the store and bought oranges."

            # 1. Direct process_slm_llm_rewrite (temperature=0.0 deterministic output)
            slm_result1 = process_slm_llm_rewrite(raw_input, timeout_sec=2.0)
            self.assertTrue(len(slm_result1) > 0)

            # 2. Full hybrid pipeline accuracy
            full_result1 = clean_speech_transcription(raw_input)

            self.assertEqual(
                full_result1.rstrip("."),
                expected_target.rstrip("."),
                f"Expected '{expected_target}', got '{full_result1}'",
            )
            self.assertNotIn(
                "apples", full_result1.lower(), "Retracted word 'apples' should be removed"
            )
            self.assertNotIn(
                "um", full_result1.lower(), "Hesitation filler 'um' should be removed"
            )
            self.assertIn(
                "oranges", full_result1.lower(), "Retraction target 'oranges' must be present"
            )

            # 3. Idempotency (repeatability across 3 consecutive runs)
            full_result2 = clean_speech_transcription(raw_input)
            full_result3 = clean_speech_transcription(raw_input)

            self.assertEqual(
                full_result1, full_result2, "vLLM SLM pass must be idempotent (run 1 vs run 2)"
            )
            self.assertEqual(
                full_result2, full_result3, "vLLM SLM pass must be idempotent (run 2 vs run 3)"
            )
        finally:
            if old_env is None:
                os.environ.pop("VT_ENABLE_SLM", None)
            else:
                os.environ["VT_ENABLE_SLM"] = old_env
            if old_url is None:
                os.environ.pop("VT_VLLM_URL", None)
            else:
                os.environ["VT_VLLM_URL"] = old_url


if __name__ == "__main__":
    unittest.main(argv=["first-arg"], exit=False)
