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
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../src')))

import slm_client


class _Recorder:
    """An HTTP server that records the requests it is sent."""

    def __init__(self, reply: dict, models: dict | None = None):
        self.bodies: list[bytes] = []
        self.paths: list[str] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def address_string(self):
                # One of two reverse-DNS lookups in http.server. This one runs per
                # request; the other (server_bind's socket.getfqdn) runs between
                # bind() and listen(), so it leaves the port refusing connections.
                # Both hang on a host with a slow resolver, which is why they are
                # both overridden here.
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

        class Server(ThreadingHTTPServer):
            """Skips `socket.getfqdn(host)`, which HTTPServer.server_bind does after
            bind() and before listen() - on macOS that goes through mDNSResponder
            and blocks, holding the port while refusing connections."""

            def server_bind(self):
                socketserver.TCPServer.server_bind(self)
                self.server_name, self.server_port = self.server_address[:2]

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def slm(monkeypatch):
    """SLM enabled, endpoint and proxy both pointed at local recorders."""
    endpoint = _Recorder({"choices": [{"message": {"content": "buy milk"}}]})
    proxy = _Recorder({"choices": [{"message": {"content": "proxied"}}]})

    monkeypatch.setattr(slm_client, "VLLM_API_URL",
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
    monkeypatch.setattr(slm_client, "_SLM_OFFLINE_STREAK", 0)
    monkeypatch.setattr(slm_client, "_SLM_LAST_OFFLINE_CHECK", 0.0)
    # A remote opt-in from the environment must not leak into a loopback test.
    monkeypatch.delenv(slm_client.ENV_ALLOW_REMOTE, raising=False)

    yield endpoint, proxy
    endpoint.close()
    proxy.close()


def test_the_transcript_reaches_the_configured_endpoint(slm):
    endpoint, _proxy = slm
    slm_client.process_slm_llm_rewrite("um so buy milk actually")
    assert len(endpoint.bodies) == 1
    assert b"buy milk" in endpoint.bodies[0]


def test_the_transcript_never_reaches_the_proxy(slm):
    """The regression this file exists for.

    With urlopen the request went through the proxy; with the module's
    proxy-free opener it cannot, whatever the environment says.
    """
    _endpoint, proxy = slm
    slm_client.process_slm_llm_rewrite("um so buy milk actually")
    assert proxy.bodies == []


def test_the_request_still_succeeds_when_a_proxy_is_configured(slm):
    """Opting out of the proxy must not break the pass, only redirect it."""
    endpoint, _proxy = slm
    assert slm_client.process_slm_llm_rewrite("um so buy milk actually") == "buy milk"
    assert len(endpoint.bodies) == 1


def test_model_discovery_queries_the_configured_endpoint(monkeypatch):
    """Discovery follows VT_VLLM_URL.

    Regression: the models URL was hardcoded to `http://localhost:8000/v1/models`,
    so on any other port the probe hit nothing, failed silently, and every request
    went out as `Qwen/Qwen2.5-1.5B-Instruct` whatever the server actually served.
    """
    endpoint = _Recorder({"choices": [{"message": {"content": "buy milk"}}]},
                         models={"data": [{"id": "actual-model"}]})
    monkeypatch.setattr(slm_client, "VLLM_API_URL",
                        f"http://127.0.0.1:{endpoint.port}/v1/chat/completions")
    monkeypatch.setenv("VT_ENABLE_SLM", "1")
    monkeypatch.delenv("VT_SLM_MODEL", raising=False)
    monkeypatch.setenv("VT_SLM_TIMEOUT", "5")
    monkeypatch.setattr(slm_client, "_SLM_OFFLINE_STREAK", 0)
    monkeypatch.setattr(slm_client, "_SLM_LAST_OFFLINE_CHECK", 0.0)
    try:
        slm_client.process_slm_llm_rewrite("um so buy milk actually")
        assert "/v1/models" in endpoint.paths, endpoint.paths
        assert len(endpoint.bodies) == 1
        assert json.loads(endpoint.bodies[0])["model"] == "actual-model"
    finally:
        endpoint.close()


# ---------------------------------------------------------------------------
# The remote-endpoint gate
# ---------------------------------------------------------------------------
# `VT_VLLM_URL` used to be used as-is, so pointing it at another machine POSTed
# every transcript there (in the clear, for http://) with no opt-in. The
# formatter refuses a non-loopback host unless `VT_FORMATTER_ALLOW_REMOTE=1`;
# the SLM pass now mirrors that with `VT_VLLM_ALLOW_REMOTE`.
class _NoNetwork:
    """An opener that records attempts and refuses to touch the network."""

    def __init__(self):
        self.calls: list = []

    def open(self, *args, **kwargs):
        self.calls.append(args)
        raise AssertionError("the SLM pass attempted a network connection")


def _slm_enabled(monkeypatch):
    monkeypatch.setenv("VT_ENABLE_SLM", "1")
    monkeypatch.setenv("VT_SLM_MODEL", "test-model")     # skip the /v1/models probe
    monkeypatch.setenv("VT_SLM_TIMEOUT", "5")
    monkeypatch.delenv(slm_client.ENV_ALLOW_REMOTE, raising=False)
    monkeypatch.setattr(slm_client, "_SLM_OFFLINE_STREAK", 0)
    monkeypatch.setattr(slm_client, "_SLM_LAST_OFFLINE_CHECK", 0.0)


def test_a_remote_url_is_refused_and_names_the_flag(monkeypatch, capsys):
    """Without the opt-in, a non-loopback endpoint is refused before any I/O.

    Regression: the transcript was POSTed to whatever host VT_VLLM_URL named,
    with no loopback restriction and no TLS required.
    """
    opener = _NoNetwork()
    monkeypatch.setattr(slm_client, "_VLLM_OPENER", opener)
    monkeypatch.setattr(slm_client, "VLLM_API_URL",
                        "http://example.invalid:8000/v1/chat/completions")
    _slm_enabled(monkeypatch)

    original = "um so buy milk actually"
    assert slm_client.process_slm_llm_rewrite(original) == original
    out = capsys.readouterr().out
    assert slm_client.ENV_ALLOW_REMOTE in out, out
    assert opener.calls == [], "a refused pass must not touch the network"


def test_the_flag_allows_a_remote_endpoint(monkeypatch):
    """The opt-in keeps an existing remote vLLM box working."""
    _slm_enabled(monkeypatch)
    monkeypatch.setenv(slm_client.ENV_ALLOW_REMOTE, "1")
    assert slm_client._slm_endpoint(
        "http://example.com:8000/v1/chat/completions")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/v1/chat/completions",
    "http://localhost:8000/v1/chat/completions",
    "http://[::1]:8000/v1/chat/completions",
    "https://127.0.0.1:8443/v1/chat/completions",
])
def test_loopback_endpoints_need_no_flag(monkeypatch, url):
    monkeypatch.delenv(slm_client.ENV_ALLOW_REMOTE, raising=False)
    assert slm_client._slm_endpoint(url)


@pytest.mark.parametrize("url", [
    "http://0.0.0.0:8000/v1/chat/completions",       # 'any', not loopback
    "http://localhost.:8000/v1/chat/completions",    # trailing dot
    "http://10.0.0.1:8000/v1/chat/completions",
    "http://example.com:8000/v1/chat/completions",
    "127.0.0.1:8000/v1/chat/completions",            # scheme-less
    "file:///etc/hosts",                             # scheme we cannot POST to
])
def test_non_loopback_endpoints_are_refused(monkeypatch, url):
    monkeypatch.delenv(slm_client.ENV_ALLOW_REMOTE, raising=False)
    with pytest.raises(slm_client.SlmRemoteRefused):
        slm_client._slm_endpoint(url)
