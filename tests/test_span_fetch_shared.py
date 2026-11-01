"""Characterisation tests for the duplicated Phoenix span-fetch (Improvement 6).

Written BEFORE the refactor against the current CLI and MCP copies.
They pin: fixture parity, exact sort order, returned shapes, per-entry
error behaviour, timeouts, and the Improvement 4+3 endpoint/transport.
HTTP is stubbed at ``httpx.Client`` (the construction point used by
``make_client`` before and after the refactor), so no Phoenix is needed.

The ``test_post_refactor_*`` tests at the bottom are deliberately for
the post-refactor structure only; they are expected to fail before it.
"""

import copy

import httpx
import pytest

from server import mcp_bridge
from vibe_debug import cli

# One representative fixture: two projects, spans out of order,
# an ERROR span, a span missing optional fields, one missing startTime.
FIXTURE = {
    "data": {
        "projects": {
            "edges": [
                {
                    "node": {
                        "spans": {
                            "edges": [
                                {
                                    "node": {
                                        "name": "mid_span",
                                        "statusCode": "OK",
                                        "latencyMs": 5.0,
                                        "startTime": "2026-11-01T10:00:02",
                                        "attributes": "{}",
                                        "context": {"spanId": "s2", "traceId": "t1"},
                                    }
                                },
                                {
                                    "node": {
                                        "name": "oldest_span",
                                        "statusCode": "OK",
                                        "latencyMs": 1.0,
                                        "startTime": "2026-11-01T10:00:01",
                                        "attributes": "{}",
                                        "context": {"spanId": "s1", "traceId": "t1"},
                                    }
                                },
                            ]
                        }
                    }
                },
                {
                    "node": {
                        "spans": {
                            "edges": [
                                {
                                    "node": {
                                        "name": "newest_error_span",
                                        "statusCode": "ERROR",
                                        "latencyMs": 9.0,
                                        "startTime": "2026-11-01T10:00:03",
                                        "attributes": "{}",
                                        "context": {"spanId": "s3", "traceId": "t1"},
                                    }
                                },
                                {
                                    "node": {
                                        "name": "no_start_span",
                                        "statusCode": "OK",
                                        "context": {"spanId": "s4", "traceId": "t1"},
                                    }
                                },
                            ]
                        }
                    }
                },
            ]
        }
    }
}

EXPECTED_ORDER = [
    "newest_error_span",
    "mid_span",
    "oldest_span",
    "no_start_span",
]


class _FakeResponse:
    def __init__(self, payload=None, status_error=False):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise httpx.HTTPError("500 Server Error")

    def json(self):
        return copy.deepcopy(self._payload)


@pytest.fixture
def stub_http(monkeypatch):
    """Replace httpx.Client with a recording fake; returns the record list."""
    records = []
    state = {"payload": FIXTURE, "status_error": False}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            records.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, path, json=None):
            records.append({"post_path": path, "base_url": self.kwargs.get("base_url")})
            return _FakeResponse(state["payload"], state["status_error"])

    monkeypatch.setattr(httpx, "Client", FakeClient)
    return records, state


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PHOENIX_HOST", raising=False)
    monkeypatch.delenv("PHOENIX_PORT", raising=False)
    yield


# -- Parity, shapes, sorting -------------------------------------------------


def test_fixture_parity_and_shapes(stub_http):
    cli_edges = cli._fetch_spans(limit=10)
    mcp_nodes = mcp_bridge._fetch_spans(limit=10)

    # CLI returns edges; MCP returns nodes (pinned type-(b) shape difference).
    assert all(set(edge.keys()) == {"node"} for edge in cli_edges)
    assert all("name" in node for node in mcp_nodes)

    cli_nodes = [edge["node"] for edge in cli_edges]
    assert cli_nodes == mcp_nodes
    assert [n["name"] for n in mcp_nodes] == EXPECTED_ORDER
    assert [e["node"]["name"] for e in cli_edges] == EXPECTED_ORDER


def test_sorting_exact_order_and_limit(stub_http):
    mcp_nodes = mcp_bridge._fetch_spans(limit=2)
    assert [n["name"] for n in mcp_nodes] == EXPECTED_ORDER[:2]
    cli_edges = cli._fetch_spans(limit=3)
    assert [e["node"]["name"] for e in cli_edges] == EXPECTED_ORDER[:3]


def test_error_span_present_and_unfiltered_by_fetch(stub_http):
    mcp_nodes = mcp_bridge._fetch_spans(limit=10)
    by_name = {n["name"]: n for n in mcp_nodes}
    assert by_name["newest_error_span"]["statusCode"] == "ERROR"
    # Missing optional fields are tolerated exactly as the fixture provides.
    assert "latencyMs" not in by_name["no_start_span"]


# -- Error behaviour (per entry point, as today) ------------------------------


def test_http500_returns_empty_for_both(stub_http):
    _, state = stub_http
    state["status_error"] = True
    assert cli._fetch_spans(limit=10) == []
    assert mcp_bridge._fetch_spans(limit=10) == []
    result = mcp_bridge.query_phoenix("{ projects }")
    assert "error" in result


def test_graphql_errors_payload_returns_empty_for_both(stub_http):
    _, state = stub_http
    state["payload"] = {"errors": [{"message": "schema boom"}]}
    assert cli._fetch_spans(limit=10) == []
    assert mcp_bridge._fetch_spans(limit=10) == []


def test_query_phoenix_success_returns_raw_payload(stub_http):
    result = mcp_bridge.query_phoenix("{ projects }")
    assert result == FIXTURE


# -- Endpoint / transport / timeouts (Improvements 4+3) -----------------------


def test_both_hit_resolved_custom_port_with_loopback_client(stub_http, monkeypatch):
    records, _ = stub_http
    monkeypatch.setenv("PHOENIX_PORT", "6007")
    cli._fetch_spans(limit=10)
    mcp_bridge._fetch_spans(limit=10)
    bases = [r["base_url"] for r in records if "trust_env" in r]
    assert bases == ["http://127.0.0.1:6007", "http://127.0.0.1:6007"]
    clients = [r for r in records if "trust_env" in r]
    assert all(c["trust_env"] is False for c in clients)


def test_timeouts_pinned_cli5_mcp10(stub_http):
    records, _ = stub_http
    cli._fetch_spans(limit=10)
    mcp_bridge._fetch_spans(limit=10)
    timeouts = [r["timeout"] for r in records if "timeout" in r]
    assert timeouts == [5.0, 10.0]


# -- Post-refactor structure (expected to fail BEFORE the refactor) -----------


def test_post_refactor_single_implementation_guard():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    client_src = (root / "vibe_debug" / "phoenix_client.py").read_text(
        encoding="utf-8"
    )
    assert "def fetch_spans" in client_src
    assert "spans(first:" in client_src
    for rel in ("vibe_debug/cli.py", "server/mcp_bridge.py"):
        src = (root / rel).read_text(encoding="utf-8")
        assert "spans(first:" not in src, rel
        assert "def fetch_spans" not in src, rel


def test_post_refactor_entry_points_delegate(monkeypatch):
    import vibe_debug.phoenix_client as phoenix_client

    sentinel = {"name": "sentinel_span", "startTime": "2026-11-01T10:00:09"}
    calls = []

    def fake_fetch(limit=20, **kwargs):
        calls.append((limit, kwargs))
        return [dict(sentinel)]

    monkeypatch.setattr(phoenix_client, "fetch_spans", fake_fetch)
    assert cli._fetch_spans(limit=5) == [{"node": sentinel}]
    assert mcp_bridge._fetch_spans(limit=5) == [sentinel]
    assert len(calls) == 2
