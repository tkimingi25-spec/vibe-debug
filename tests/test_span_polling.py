"""Bounded span polling for Phoenix ingestion lag (Stage 2)."""

import time

import vibe_debug.phoenix_client as phoenix_client
from vibe_debug import cli
from server import mcp_bridge

SPAN = {"name": "late_span", "startTime": "2026-11-01T10:00:01", "statusCode": "ERROR"}
PAYLOAD = {"data": {"projects": {"edges": [{"node": {"spans": {"edges": [{"node": SPAN}]}}}]}}}
EMPTY = {"data": {"projects": {"edges": []}}}


def _sequence(monkeypatch, payloads):
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        idx = min(calls["n"], len(payloads) - 1)
        calls["n"] += 1
        return payloads[idx]

    monkeypatch.setattr(phoenix_client, "_post_graphql", fake_post)
    return calls


def test_wait_finds_spans_after_empty(monkeypatch):
    calls = _sequence(monkeypatch, [EMPTY, EMPTY, PAYLOAD])
    started = time.monotonic()
    result = phoenix_client.fetch_spans(limit=10, wait_seconds=5, poll_interval_seconds=0.05)
    elapsed = time.monotonic() - started
    assert [s["name"] for s in result] == ["late_span"]
    assert calls["n"] == 3
    assert elapsed < 5


def test_no_wait_returns_first_empty_immediately(monkeypatch):
    calls = _sequence(monkeypatch, [EMPTY, PAYLOAD])
    started = time.monotonic()
    assert phoenix_client.fetch_spans(limit=10, wait_seconds=0) == []
    assert time.monotonic() - started < 1
    assert calls["n"] == 1


def test_deadline_expiry_returns_empty_without_raising(monkeypatch):
    _sequence(monkeypatch, [EMPTY])
    started = time.monotonic()
    assert phoenix_client.fetch_spans(limit=10, wait_seconds=0.3, poll_interval_seconds=0.05) == []
    assert 0.3 <= time.monotonic() - started < 3


def test_cli_and_mcp_honour_wait(monkeypatch):
    captured = {}

    def fake_fetch(limit=20, **kwargs):
        captured["wait"] = kwargs.get("wait_seconds")
        return [dict(SPAN)]

    monkeypatch.setattr(phoenix_client, "fetch_spans", fake_fetch)
    edges = cli._fetch_spans(limit=10, wait_seconds=7)
    assert edges == [{"node": SPAN}]
    assert captured["wait"] == 7
    nodes = mcp_bridge._fetch_spans(limit=10, wait_seconds=3)
    assert nodes == [SPAN]
    assert captured["wait"] == 3
