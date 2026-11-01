"""Single endpoint resolver (Improvement 4) + loopback-safe transport (Improvement 3).

No running Phoenix required: mocked transports / fake clients /
monkeypatched env vars only.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe_debug.config.config import VibeConfig
from vibe_debug.phoenix_endpoint import (
    _is_loopback_host,
    make_async_client,
    make_client,
    resolve_base_url,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_phoenix_env(monkeypatch):
    monkeypatch.delenv("PHOENIX_HOST", raising=False)
    monkeypatch.delenv("PHOENIX_PORT", raising=False)
    yield


# -- Improvement 4: precedence ---------------------------------------------


def test_precedence_explicit_beats_env_and_config(monkeypatch):
    monkeypatch.setenv("PHOENIX_PORT", "6007")
    config = VibeConfig(otlp_endpoint="http://confighost:6009/v1/traces")
    assert (
        resolve_base_url("http://explicithost:6008", config=config)
        == "http://explicithost:6008"
    )


def test_precedence_env_beats_config(monkeypatch):
    monkeypatch.setenv("PHOENIX_PORT", "6007")
    config = VibeConfig(otlp_endpoint="http://confighost:6009/v1/traces")
    assert resolve_base_url(config=config) == "http://127.0.0.1:6007"


def test_precedence_config_beats_default():
    config = VibeConfig(otlp_endpoint="http://confighost:6009/v1/traces")
    assert resolve_base_url(config=config) == "http://confighost:6009"


def test_precedence_default():
    assert resolve_base_url() == "http://localhost:6006"


# -- Improvement 4: OTLP derivation and edge cases --------------------------


def test_otlp_derivation_strips_path():
    config = VibeConfig(otlp_endpoint="http://examplehost:6007/v1/traces")
    assert resolve_base_url(config=config) == "http://examplehost:6007"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("http://h:6007/", "http://h:6007"),
        ("http://h:6007", "http://h:6007"),
        ("http://[::1]:6006", "http://[::1]:6006"),
        ("http://examplehost", "http://examplehost"),
        ("examplehost:6007", "http://examplehost:6007"),
        ("https://h:8443/v1/traces/", "https://h:8443"),
    ],
)
def test_edge_cases_clean_base_url(value, expected):
    assert resolve_base_url(value) == expected


def test_malformed_explicit_falls_back_to_default():
    assert resolve_base_url("://not a url") == "http://localhost:6006"
    assert resolve_base_url("ftp://h:21") == "http://localhost:6006"
    assert resolve_base_url("") == "http://localhost:6006"


def test_malformed_env_falls_back_to_config(monkeypatch):
    monkeypatch.setenv("PHOENIX_PORT", "not-a-port")
    config = VibeConfig(otlp_endpoint="http://confighost:6009/v1/traces")
    assert resolve_base_url(config=config) == "http://confighost:6009"


def test_env_host_port_and_full_url_forms(monkeypatch):
    monkeypatch.setenv("PHOENIX_HOST", "myhost:6010")
    assert resolve_base_url() == "http://myhost:6010"
    monkeypatch.setenv("PHOENIX_HOST", "http://otherhost:6011/path")
    assert resolve_base_url() == "http://otherhost:6011"
    monkeypatch.setenv("PHOENIX_PORT", "6012")
    assert resolve_base_url() == "http://otherhost:6012"


# -- Improvement 4: custom port end-to-end ----------------------------------


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, base_url, recorded, payload):
        self.base_url = base_url
        self._recorded = recorded
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, path, json=None):
        self._recorded.append(f"{self.base_url}{path}")
        return _FakeResponse(self._payload)


_PAYLOAD = {
    "data": {
        "projects": {
            "edges": [
                {"node": {"spans": {"edges": [{"node": {"name": "s1"}}]}}}
            ]
        }
    }
}


def test_custom_port_cli_path(monkeypatch):
    from vibe_debug import cli

    monkeypatch.setenv("PHOENIX_PORT", "6007")
    recorded = []
    monkeypatch.setattr(
        cli,
        "make_client",
        lambda base_url, **kw: _FakeClient(base_url, recorded, _PAYLOAD),
    )
    spans = cli._fetch_spans()
    assert recorded == ["http://127.0.0.1:6007/graphql"]
    assert spans and spans[0]["node"]["name"] == "s1"


def test_custom_port_mcp_path(monkeypatch):
    from server import mcp_bridge

    monkeypatch.setenv("PHOENIX_PORT", "6007")
    recorded = []
    monkeypatch.setattr(
        mcp_bridge,
        "make_client",
        lambda base_url, **kw: _FakeClient(base_url, recorded, _PAYLOAD),
    )
    payload = mcp_bridge.query_phoenix("{ projects }")
    assert recorded == ["http://127.0.0.1:6007/graphql"]
    assert payload == _PAYLOAD


def test_no_hardcoded_localhost_6006_in_cli_bridge_manager():
    for rel in ("vibe_debug/cli.py", "server/mcp_bridge.py", "server/manager.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "localhost:6006" not in text, rel


# -- Improvement 3: loopback detection --------------------------------------


@pytest.mark.parametrize(
    "host,expected",
    [
        ("localhost", True),
        ("LOCALHOST", True),
        ("127.0.0.1", True),
        ("127.0.1.5", True),
        ("::1", True),
        ("[::1]", True),
        ("localhost.example.com", False),
        ("192.168.1.10", False),
        ("phoenix.internal", False),
        ("not a host!!", False),
        ("", False),
        (None, False),
    ],
)
def test_loopback_detection(host, expected):
    assert _is_loopback_host(host) is expected


# -- Improvement 3: client construction -------------------------------------


def _capture_client(monkeypatch, factory_name="Client"):
    import vibe_debug.phoenix_endpoint as endpoint

    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(endpoint.httpx, factory_name, fake_client)
    return captured


def test_loopback_client_disables_trust_env(monkeypatch):
    captured = _capture_client(monkeypatch)
    make_client("http://localhost:6006")
    assert captured["trust_env"] is False


def test_remote_client_keeps_trust_env(monkeypatch):
    captured = _capture_client(monkeypatch)
    make_client("http://phoenix.example.com:6006")
    assert captured["trust_env"] is True


def test_async_loopback_client_disables_trust_env(monkeypatch):
    captured = _capture_client(monkeypatch, factory_name="AsyncClient")
    make_async_client("http://127.0.0.1:6006")
    assert captured["trust_env"] is False


# -- Improvement 3: dead-proxy objective test -------------------------------


def test_dead_proxy_does_not_break_loopback_call(monkeypatch):
    import json as json_module
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from server import mcp_bridge

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["path"] = self.path
            body = json_module.dumps(_PAYLOAD).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            monkeypatch.setenv(var, "http://127.0.0.1:9")
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("no_proxy", "")
        payload = mcp_bridge.query_phoenix(
            "{ projects }", base_url=f"http://127.0.0.1:{port}"
        )
        assert payload == _PAYLOAD
        assert seen["path"] == "/graphql"
        # The user's proxy env vars are never mutated by the fix.
        import os

        assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:9"
    finally:
        server.shutdown()
        server.server_close()
