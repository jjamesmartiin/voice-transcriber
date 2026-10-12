#!/usr/bin/env python3
"""The optional SLM polish pass must reach the local server, never a proxy.

`urllib.request.urlopen` reads `http_proxy` / `HTTPS_PROXY` / `no_proxy` and, on
macOS, the system proxy via `_scproxy`. None of those can tell `localhost:8000`
from the open internet, so on any machine with a proxy configured the transcript
- the whole point of the feature - was forwarded to it instead, while the app
went on reporting that it "talks only to 127.0.0.1" (README, *Privacy and
offline behaviour*).

These tests prove the request arrives at the configured endpoint and leaves the
proxy untouched, by standing up both and pointing the environment at the proxy.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

import post_processor


class _Recorder:
    """An HTTP server that records the requests it is sent."""

    def __init__(self, reply: dict, models: dict | None = None):
        self.bodies: list[bytes] = []
        self.paths: list[str] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def address_string(self):
                # BaseHTTPRequestHandler does a reverse-DNS lookup here by
                # default, which hangs on hosts with a slow resolver.
                return self.client_address[0]

            def log_message(self, *args):
                pass

            def _send(self, payload: dict):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                recorder.paths.append(self.path)
                self._send(models or {})

            def do_POST(self):
                recorder.paths.append(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                recorder.bodies.append(self.rfile.read(length))
                self._send(reply)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def slm(monkeypatch):
    """SLM enabled, endpoint and proxy both pointed at local recorders."""
    endpoint = _Recorder({"choices": [{"message": {"content": "buy milk"}}]})
    proxy = _Recorder({"choices": [{"message": {"content": "proxied"}}]})

    monkeypatch.setattr(post_processor, "VLLM_API_URL",
                        f"http://127.0.0.1:{endpoint.port}/v1/chat/completions")
    monkeypatch.setenv("VT_ENABLE_SLM", "1")
    monkeypatch.setenv("VT_SLM_MODEL", "test-model")     # skip the /v1/models probe
    monkeypatch.setenv("VT_SLM_TIMEOUT", "5")
    # A configured proxy is the whole hazard. Any real machine can have one.
    for variable in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY",
                     "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(variable, f"http://127.0.0.1:{proxy.port}")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    # The pass backs off after a failure; a previous test must not silence this one.
    monkeypatch.setattr(post_processor, "_SLM_OFFLINE_STREAK", 0)
    monkeypatch.setattr(post_processor, "_SLM_LAST_OFFLINE_CHECK", 0.0)

    yield endpoint, proxy
    endpoint.close()
    proxy.close()


def test_the_transcript_reaches_the_configured_endpoint(slm):
    endpoint, _proxy = slm
    post_processor.process_slm_llm_rewrite("um so buy milk actually")
    assert len(endpoint.bodies) == 1
    assert b"buy milk" in endpoint.bodies[0]


def test_the_transcript_never_reaches_the_proxy(slm):
    """The regression this file exists for.

    With urlopen the request went through the proxy; with the module's
    proxy-free opener it cannot, whatever the environment says.
    """
    _endpoint, proxy = slm
    post_processor.process_slm_llm_rewrite("um so buy milk actually")
    assert proxy.bodies == []


def test_the_request_still_succeeds_when_a_proxy_is_configured(slm):
    """Opting out of the proxy must not break the pass, only redirect it."""
    endpoint, _proxy = slm
    assert post_processor.process_slm_llm_rewrite("um so buy milk actually") == "buy milk"
    assert len(endpoint.bodies) == 1


def test_model_discovery_queries_the_configured_endpoint(monkeypatch):
    """Discovery follows VT_VLLM_URL.

    Regression: the models URL was hardcoded to `http://localhost:8000/v1/models`,
    so on any other port the probe hit nothing, failed silently, and every request
    went out as `Qwen/Qwen2.5-1.5B-Instruct` whatever the server actually served.
    """
    endpoint = _Recorder({"choices": [{"message": {"content": "buy milk"}}]},
                         models={"data": [{"id": "actual-model"}]})
    monkeypatch.setattr(post_processor, "VLLM_API_URL",
                        f"http://127.0.0.1:{endpoint.port}/v1/chat/completions")
    monkeypatch.setenv("VT_ENABLE_SLM", "1")
    monkeypatch.delenv("VT_SLM_MODEL", raising=False)
    monkeypatch.setenv("VT_SLM_TIMEOUT", "5")
    monkeypatch.setattr(post_processor, "_SLM_OFFLINE_STREAK", 0)
    monkeypatch.setattr(post_processor, "_SLM_LAST_OFFLINE_CHECK", 0.0)
    try:
        post_processor.process_slm_llm_rewrite("um so buy milk actually")
        assert "/v1/models" in endpoint.paths, endpoint.paths
        assert len(endpoint.bodies) == 1
        assert json.loads(endpoint.bodies[0])["model"] == "actual-model"
    finally:
        endpoint.close()
