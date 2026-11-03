"""Optional Muse Spark explainer: evidence, provider, validation, fallback.

All provider responses are mocked. No live API calls in this suite.
Sentinel secrets are built by concatenation so they stay test-local.
"""

import copy
import json

import pytest

from vibe_debug.explainer import build_evidence_package, select_failure_span

SECRET_KEY = "sk-test-" + "secret-123"
SECRET_TOKEN = "Bearer test-" + "token-456"
SECRET_PW = "hunter2-test-" + "password"


def make_span(name="route_query", status="ERROR", latency=5012, attrs=None, trace="t1", span_id="s1"):
    base = {
        "vibe.input": "query=Summarize this document",
        "vibe.output": "",
        "vibe.file": "/app/pipeline.py",
        "vibe.line": 42,
        "vibe.module": "pipeline",
        "vibe.status": "ERROR",
        "error.type": "TimeoutError",
    }
    if attrs:
        base.update(attrs)
    return {
        "name": name,
        "statusCode": status,
        "latencyMs": latency,
        "startTime": "2026-11-01T10:00:00",
        "attributes": json.dumps(base),
        "context": {"spanId": span_id, "traceId": trace},
    }


# -- Stage 1: evidence package ------------------------------------------------


def test_normal_error_span_evidence_structure():
    pkg = build_evidence_package(make_span(), [])
    assert pkg["function"] == "route_query"
    assert pkg["status"] == "ERROR"
    assert pkg["error_type"] == "TimeoutError"
    assert pkg["duration_ms"] == 5012
    assert pkg["file"] == "/app/pipeline.py"
    assert pkg["line"] == 42
    assert pkg["silent_failure"] is False
    assert "Summarize" in pkg["input_preview"]


def test_silent_failure_represented():
    span = make_span(attrs={"vibe.output": "{}", "error.type": ""})
    pkg = build_evidence_package(span, [])
    assert pkg["silent_failure"] is True
    assert pkg["error_type"] is None


def test_related_spans_deterministic_order_and_unrelated_excluded():
    selected = make_span()
    child1 = make_span(name="fetch_documents", status="OK", latency=320, span_id="s2")
    child2 = make_span(name="call_llm", status="ERROR", latency=4680, span_id="s3")
    unrelated = make_span(name="other_trace", status="OK", latency=1, trace="t2", span_id="s9")
    pkg = build_evidence_package(selected, [selected, child1, child2, unrelated])
    assert [r["name"] for r in pkg["related_spans"]] == ["fetch_documents", "call_llm"]


def test_evidence_size_bounded():
    span = make_span(attrs={"vibe.input": "x" * 20000})
    pkg = build_evidence_package(span, [], max_evidence_size=500)
    assert len(json.dumps(pkg)) <= 500


def test_nested_secrets_and_authorization_redacted():
    span = make_span(attrs={
        "vibe.input": json.dumps({
            "headers": {"Authorization": SECRET_TOKEN},
            "credentials": {"api_key": SECRET_KEY, "password": SECRET_PW},
            "q": "keep-me",
        }),
    })
    pkg = build_evidence_package(span, [])
    blob = json.dumps(pkg)
    assert SECRET_KEY not in blob
    assert "test-token-456" not in blob
    assert SECRET_PW not in blob
    assert "keep-me" in blob
    assert "***" in blob


def test_long_values_truncated():
    span = make_span(attrs={"vibe.input": "y" * 5000})
    pkg = build_evidence_package(span, [])
    assert len(pkg["input_preview"]) <= 1024


def test_missing_and_malformed_spans_do_not_crash():
    assert build_evidence_package({}, [])["function"] == "unknown"
    assert build_evidence_package({"name": 123, "attributes": "{not json"}, [])["function"] == "123"
    assert build_evidence_package(None, None)["status"] == "unknown"
    assert build_evidence_package(make_span(attrs={"vibe.line": "NaN"}), [])["duration_ms"] == 5012


def test_original_span_not_mutated():
    span = make_span(attrs={"vibe.input": "z" * 3000})
    original = copy.deepcopy(span)
    build_evidence_package(span, [span])
    assert span == original


# -- Stage 2: language, provider, validation, fallback -------------------------

from types import SimpleNamespace

from vibe_debug.explainer import (
    explain_failure,
    normalize_language,
    render_explanation,
    validate_explanation,
)

VALID = {
    "summary": "The call to call_llm timed out.",
    "likely_cause": "call_llm exceeded the timeout seen in route_query.",
    "evidence": ["route_query status ERROR", "call_llm duration 4680ms"],
    "suggested_fix": ["Check the timeout configuration for call_llm."],
    "confidence": "medium",
    "missing_information": ["Provider-side latency logs"],
}


def cfg(**kw):
    base = {
        "explainer_enabled": True,
        "explainer_model": "test-model",
        "explainer_endpoint": "http://provider.invalid/v1",
        "explainer_timeout_seconds": 2.0,
        "explainer_language": "en",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def evidence():
    return build_evidence_package(make_span(), [])


class RecordingClient:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        if self.exc is not None:
            raise self.exc
        return self.payload


def test_language_mapping_and_defaults():
    assert normalize_language("en") == "en"
    assert normalize_language("English") == "en"
    assert normalize_language("sw") == "sw"
    assert normalize_language("Swahili") == "sw"
    assert normalize_language("Kiswahili") == "sw"
    assert normalize_language("fr") is None
    result = explain_failure(evidence(), client=RecordingClient(VALID), config=cfg())
    assert result["language"] == "en"
    assert result["source"] == "llm"


def test_explicit_language_overrides_config():
    client = RecordingClient(VALID)
    result = explain_failure(evidence(), language="Kiswahili", client=client, config=cfg(explainer_language="en"))
    assert result["language"] == "sw"
    assert client.calls[0]["language"] == "sw"
    assert json.loads(client.calls[0]["user"])["language"] == "sw"


def test_unsupported_language_falls_back_safely():
    client = RecordingClient(VALID)
    result = explain_failure(evidence(), language="fr", client=client, config=cfg(), fallback_text="DET")
    assert result["source"] == "fallback"
    assert result["text"] == "DET"
    assert "unsupported language" in result["error"]
    assert client.calls == []


def test_request_contains_evidence_and_no_secrets():
    client = RecordingClient(VALID)
    ev = build_evidence_package(
        make_span(attrs={"vibe.input": json.dumps({"api_key": SECRET_KEY, "q": "keep"})}), []
    )
    result = explain_failure(ev, client=client, config=cfg())
    assert result["source"] == "llm"
    assert len(client.calls) == 1
    blob = json.dumps(client.calls[0])
    assert SECRET_KEY not in blob
    assert "keep" in blob
    assert client.calls[0]["evidence"] == ev


def test_valid_response_parsed_and_rendered():
    result = explain_failure(evidence(), client=RecordingClient(VALID), config=cfg())
    assert result["structured"] == VALID
    assert "call_llm" in result["text"]
    assert "Confidence: medium" in result["text"]


@pytest.mark.parametrize("bad", [
    "not json at all",
    {"summary": "x"},
    {"summary": 1, "likely_cause": "y", "evidence": [], "suggested_fix": [], "confidence": "low"},
    {**VALID, "confidence": "certain"},
    {**VALID, "evidence": "not-a-list"},
])
def test_invalid_responses_fall_back(bad):
    payload = {"choices": [{"message": {"content": bad if isinstance(bad, str) else json.dumps(bad)}}]} if not isinstance(bad, dict) or "summary" not in bad else bad
    result = explain_failure(evidence(), client=RecordingClient(payload), config=cfg(), fallback_text="DET")
    assert result["source"] == "fallback"
    assert result["text"] == "DET"


def test_http_error_timeout_and_exception_fall_back():
    for exc in (TimeoutError("slow"), RuntimeError("http 500")):
        result = explain_failure(evidence(), client=RecordingClient(exc=exc), config=cfg(), fallback_text="DET")
        assert result["source"] == "fallback"
        assert result["text"] == "DET"


def test_missing_key_and_disabled_mean_no_call(monkeypatch):
    monkeypatch.delenv("MUSE_SPARK_API_KEY", raising=False)
    result = explain_failure(evidence(), config=cfg(), fallback_text="DET")
    assert result["source"] == "fallback"
    calls = []
    result2 = explain_failure(
        evidence(), client=lambda req: calls.append(req) or VALID,
        config=cfg(explainer_enabled=False), fallback_text="DET",
    )
    assert result2["source"] == "fallback"
    assert calls == []


def test_no_api_key_in_outputs(monkeypatch):
    fake_key = "test-key-" + "should-not-appear"
    monkeypatch.setenv("MUSE_SPARK_API_KEY", fake_key)
    result = explain_failure(
        evidence(), client=RecordingClient(exc=RuntimeError("boom")), config=cfg()
    )
    assert fake_key not in json.dumps(result)
    ok = explain_failure(evidence(), client=RecordingClient(VALID), config=cfg())
    assert fake_key not in json.dumps(ok)


def test_validate_and_render_units():
    assert validate_explanation(VALID) == VALID
    assert validate_explanation({**VALID, "confidence": "high"})["confidence"] == "high"
    text = render_explanation(VALID, "sw")
    assert "Muhtasari" in text
    assert "route_query" in text or "call_llm" in text


# -- Stage 3: mocked Swahili fixture + failure categories ----------------------

SW_RESPONSE = {
    "summary": "route_query ilishindwa kwa sababu call_llm ilizidi muda uliowekwa (TimeoutError).",
    "likely_cause": "call_llm ilichukua muda mrefu zaidi kuliko timeout ya route_query.",
    "evidence": ["route_query status ERROR", "call_llm duration_ms 4680", "TimeoutError katika call_llm"],
    "suggested_fix": ["Kagua timeout ya call_llm katika pipeline.py.", "Hakikisha route_query inashughulikia TimeoutError."],
    "confidence": "medium",
    "missing_information": ["Kumbukumbu za upande wa mtoa huduma"],
}

EN_RESPONSE = {
    "summary": "route_query failed because call_llm timed out (TimeoutError).",
    "likely_cause": "call_llm took longer than the route_query timeout.",
    "evidence": ["route_query status ERROR", "call_llm duration 4680ms", "TimeoutError in call_llm"],
    "suggested_fix": ["Check the call_llm timeout in pipeline.py.", "Ensure route_query handles TimeoutError."],
    "confidence": "medium",
    "missing_information": ["Provider-side latency logs"],
}


def test_mocked_swahili_rendering_preserves_identifiers():
    client = RecordingClient(SW_RESPONSE)
    result = explain_failure(evidence(), language="sw", client=client, config=cfg())
    assert result["source"] == "llm"
    assert result["language"] == "sw"
    text = result["text"]
    assert "Muhtasari:" in text
    assert "route_query" in text
    assert "call_llm" in text
    assert "TimeoutError" in text
    assert "pipeline.py" in text
    assert "Kiwango cha uhakika: medium" in text


def test_same_evidence_for_english_and_swahili():
    en_client = RecordingClient(EN_RESPONSE)
    sw_client = RecordingClient(SW_RESPONSE)
    ev = evidence()
    explain_failure(ev, language="en", client=en_client, config=cfg())
    explain_failure(ev, language="sw", client=sw_client, config=cfg())
    assert en_client.calls[0]["evidence"] == sw_client.calls[0]["evidence"] == ev
    assert en_client.calls[0]["language"] == "en"
    assert sw_client.calls[0]["language"] == "sw"


FAILURE_FIXTURES = [
    ("timeout", {"error.type": "TimeoutError", "vibe.output": ""}, "TimeoutError"),
    ("silent failure/empty result", {"error.type": "", "vibe.output": "{}"}, None),
    ("invalid JSON from downstream", {"error.type": "JSONDecodeError", "vibe.output": "{not json"}, "JSONDecodeError"),
    ("database connection failure", {"error.type": "ConnectionError", "vibe.output": ""}, "ConnectionError"),
    ("downstream HTTP 500", {"error.type": "HTTPStatusError", "vibe.output": "500"}, "HTTPStatusError"),
    ("empty retrieval result", {"error.type": "", "vibe.output": "[]"}, None),
]


@pytest.mark.parametrize("label,attrs,expected_type", FAILURE_FIXTURES)
def test_failure_fixture_categories(label, attrs, expected_type):
    span = make_span(attrs={**attrs, "vibe.input": json.dumps({"api_key": SECRET_KEY, "password": SECRET_PW})})
    spans = [span, make_span(name="call_llm", status="ERROR", latency=4680, span_id="s3")]
    selected = select_failure_span(spans)
    assert selected is span
    pkg = build_evidence_package(selected, spans)
    assert pkg["function"] == "route_query"
    if expected_type:
        assert pkg["error_type"] == expected_type
    client = RecordingClient(VALID)
    result = explain_failure(pkg, client=client, config=cfg(), fallback_text="DET")
    assert result["source"] == "llm"
    blob = json.dumps(client.calls[0])
    assert SECRET_KEY not in blob and SECRET_PW not in blob
    bad = explain_failure(pkg, client=RecordingClient({"bad": 1}), config=cfg(), fallback_text="DET")
    assert bad["source"] == "fallback" and bad["text"] == "DET"


def test_nested_child_exception_fixture():
    child = make_span(
        name="call_llm", status="ERROR", latency=4680, span_id="s3",
        attrs={"error.type": "TimeoutError"},
    )
    parent = make_span(name="route_query", status="ERROR", latency=5012, span_id="s1")
    pkg = build_evidence_package(parent, [parent, child])
    assert pkg["related_spans"][0]["name"] == "call_llm"
    client = RecordingClient(SW_RESPONSE)
    result = explain_failure(pkg, language="sw", client=client, config=cfg())
    assert result["source"] == "llm"
    assert "TimeoutError" in result["text"]


def test_missing_api_key_fixture_secret_never_sent(monkeypatch):
    monkeypatch.delenv("MUSE_SPARK_API_KEY", raising=False)
    span = make_span(attrs={"vibe.input": json.dumps({"api_key": SECRET_KEY})})
    pkg = build_evidence_package(span, [])
    result = explain_failure(pkg, config=cfg(), fallback_text="DET")
    assert result["source"] == "fallback"
    assert SECRET_KEY not in json.dumps(pkg)
    assert SECRET_KEY not in result["text"]


import os


@pytest.mark.skipif(
    os.getenv("VIBE_EXPLAINER_LIVE") != "1" or not os.getenv("MUSE_SPARK_API_KEY"),
    reason="Live Muse Spark evaluation is opt-in only: requires VIBE_EXPLAINER_LIVE=1, verified endpoint/model config, and the credential env var. Skipped by default.",
)
def test_live_evaluation_scorecard_opt_in(capsys):
    """Opt-in live scorecard: en+sw over the 8-fixture pack.

    Machine results only (schema valid, failing span identified, no
    invented span names via citation validation). Not human review.
    Makes 16 live calls when enabled; skipped by default.
    """
    endpoint = os.getenv("MUSE_SPARK_ENDPOINT")
    model = os.getenv("MUSE_SPARK_MODEL")
    if not endpoint or not model:
        pytest.skip("Live evaluation needs MUSE_SPARK_ENDPOINT and MUSE_SPARK_MODEL")
    from tests.explainer_eval_fixtures import EVAL_FIXTURES

    config = cfg(
        explainer_endpoint=endpoint, explainer_model=model, explainer_timeout_seconds=60.0
    )
    rows = []
    for fixture in EVAL_FIXTURES:
        pkg = build_evidence_package(fixture["span"], fixture["related"])
        for lang in ("en", "sw"):
            result = explain_failure(pkg, language=lang, config=config, fallback_text="DET")
            rows.append(
                {
                    "fixture": fixture["label"],
                    "language": lang,
                    "schema_valid": result.get("source") == "llm",
                    "span_identified": result.get("source") == "llm"
                    and "route_query" in (result.get("text") or ""),
                }
            )
    with capsys.disabled():
        print("\nLIVE EVAL SCORECARD (machine results only):")
        for row in rows:
            print(
                f"  {row['fixture']} [{row['language']}] "
                f"schema_valid={row['schema_valid']} span_identified={row['span_identified']}"
            )
        passed = sum(1 for r in rows if r["schema_valid"] and r["span_identified"])
        print(f"  score: {passed}/{len(rows)} (calls={len(rows)})")
    assert rows and all(r["language"] in ("en", "sw") for r in rows)


# -- Stage 4: CLI integration ---------------------------------------------------

from vibe_debug import cli


def _cli_span_edges():
    return [{"node": make_span()}]


@pytest.mark.parametrize("lang", ["sw", "en"])
def test_cli_language_reaches_explainer(monkeypatch, capsys, lang):
    captured = {}

    def fake_explain(evidence, *, language=None, config=None, fallback_text=None, **kw):
        captured["language"] = language
        captured["evidence"] = evidence
        return {"language": lang, "source": "llm", "structured": VALID, "text": f"AI-{lang}", "error": None}

    monkeypatch.setattr(cli, "_fetch_spans", lambda limit=30: _cli_span_edges())
    monkeypatch.setattr(cli, "_local_config", lambda: cfg())
    monkeypatch.setattr(cli, "explain_failure", fake_explain)
    rc = cli.main(["explain", "--language", lang])
    assert rc == 0
    out = capsys.readouterr().out
    assert captured["language"] == lang
    assert f"AI-{lang}" in out
    assert "=== FAILURE EXPLANATION ===" in out
    assert "AI explanation (Muse Spark" in out


def test_cli_default_is_deterministic_unchanged(monkeypatch, capsys):
    called = []
    monkeypatch.setattr(cli, "_fetch_spans", lambda limit=30: _cli_span_edges())
    monkeypatch.setattr(cli, "_local_config", lambda: None)
    monkeypatch.setattr(cli, "explain_failure", lambda *a, **k: called.append(1))
    rc = cli.main(["explain"])
    assert rc == 0
    out = capsys.readouterr().out
    assert called == []
    assert "Component: route_query" in out
    assert "Status: ERROR" in out
    assert "AI explanation" not in out
    assert "Export succeeded" not in out


def test_cli_swahili_mocked_rendered(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_fetch_spans", lambda limit=30: _cli_span_edges())
    monkeypatch.setattr(cli, "_local_config", lambda: cfg())
    monkeypatch.setattr(
        cli, "explain_failure",
        lambda evidence, **kw: {"language": "sw", "source": "llm", "structured": SW_RESPONSE,
                                 "text": "Muhtasari: route_query call_llm TimeoutError", "error": None},
    )
    assert cli.main(["explain", "--language", "sw"]) == 0
    out = capsys.readouterr().out
    assert "Muhtasari: route_query call_llm TimeoutError" in out


@pytest.mark.parametrize("mode", ["missing_key", "provider_failure", "invalid_json"])
def test_cli_fallbacks_produce_deterministic_output(monkeypatch, capsys, mode):
    monkeypatch.setattr(cli, "_fetch_spans", lambda limit=30: _cli_span_edges())
    monkeypatch.setattr(cli, "_local_config", lambda: cfg())
    monkeypatch.delenv("MUSE_SPARK_API_KEY", raising=False)
    if mode == "missing_key":
        fake = lambda evidence, **kw: {"language": "en", "source": "fallback", "structured": None, "text": kw.get("fallback_text", ""), "error": None}
    elif mode == "provider_failure":
        fake = lambda evidence, **kw: {"language": "en", "source": "fallback", "structured": None, "text": kw.get("fallback_text", ""), "error": None}
    else:
        fake = lambda evidence, **kw: {"language": "en", "source": "fallback", "structured": None, "text": kw.get("fallback_text", ""), "error": "invalid provider response"}
    monkeypatch.setattr(cli, "explain_failure", fake)
    assert cli.main(["explain", "--language", "en"]) == 0
    out = capsys.readouterr().out
    assert "Component: route_query" in out
    assert "AI explanation" not in out


def test_cli_no_secret_in_output(monkeypatch, capsys):
    span = make_span(attrs={"vibe.input": json.dumps({"api_key": SECRET_KEY, "password": SECRET_PW})})
    monkeypatch.setattr(cli, "_fetch_spans", lambda limit=30: [{"node": span}])
    monkeypatch.setattr(cli, "_local_config", lambda: cfg())
    monkeypatch.setattr(
        cli, "explain_failure",
        lambda evidence, **kw: {"language": "en", "source": "llm", "structured": VALID, "text": "clean", "error": None},
    )
    assert cli.main(["explain", "--language", "en"]) == 0
    captured = capsys.readouterr()
    assert SECRET_KEY not in captured.out and SECRET_PW not in captured.out
    assert SECRET_KEY not in captured.err


# -- Stage 5: MCP integration ---------------------------------------------------

import asyncio

from server import mcp_bridge


def _mcp_spans():
    return [make_span(), make_span(name="call_llm", status="OK", latency=320, span_id="s2")]


def _run_why(**kwargs):
    fn = getattr(mcp_bridge.why_did_this_fail, "fn", mcp_bridge.why_did_this_fail)
    return asyncio.run(fn(**kwargs))


@pytest.mark.parametrize("lang", ["sw", "en"])
def test_mcp_language_reaches_shared_explainer(monkeypatch, lang):
    captured = {}

    def fake_explain(evidence, *, language=None, config=None, fallback_text=None, **kw):
        captured["language"] = language
        return {"language": lang, "source": "llm", "structured": VALID, "text": f"MCP-AI-{lang}", "error": None}

    monkeypatch.setattr(mcp_bridge, "_fetch_spans", lambda limit=30: _mcp_spans())
    monkeypatch.setattr(mcp_bridge, "_bridge_config", lambda: cfg())
    monkeypatch.setattr(mcp_bridge, "explain_failure", fake_explain)
    out = _run_why(language=lang)
    assert captured["language"] == lang
    assert f"MCP-AI-{lang}" in out
    assert "FAILURE ANALYSIS" in out


def test_cli_and_mcp_share_same_explainer_function():
    import vibe_debug.explainer as explainer_module

    assert cli.explain_failure is explainer_module.explain_failure
    assert mcp_bridge.explain_failure is explainer_module.explain_failure
    assert cli.build_evidence_package is explainer_module.build_evidence_package
    assert mcp_bridge.build_evidence_package is explainer_module.build_evidence_package


def test_mcp_without_language_unchanged(monkeypatch):
    called = []
    monkeypatch.setattr(mcp_bridge, "_fetch_spans", lambda limit=30: _mcp_spans())
    monkeypatch.setattr(mcp_bridge, "_bridge_config", lambda: None)
    monkeypatch.setattr(mcp_bridge, "explain_failure", lambda *a, **k: called.append(1))
    out = _run_why()
    assert called == []
    assert "CRASH POINT:" in out
    assert "AI explanation" not in out


def test_mcp_swahili_mocked_output(monkeypatch):
    monkeypatch.setattr(mcp_bridge, "_fetch_spans", lambda limit=30: _mcp_spans())
    monkeypatch.setattr(mcp_bridge, "_bridge_config", lambda: cfg())
    monkeypatch.setattr(
        mcp_bridge, "explain_failure",
        lambda evidence, **kw: {"language": "sw", "source": "llm", "structured": SW_RESPONSE,
                                "text": "Muhtasari: route_query call_llm TimeoutError", "error": None},
    )
    out = _run_why(language="sw")
    assert "Muhtasari: route_query call_llm TimeoutError" in out


@pytest.mark.parametrize("mode", ["missing_config", "invalid_output", "unsupported"])
def test_mcp_fallbacks(monkeypatch, mode):
    monkeypatch.setattr(mcp_bridge, "_fetch_spans", lambda limit=30: _mcp_spans())
    monkeypatch.setattr(mcp_bridge, "_bridge_config", lambda: cfg() if mode != "missing_config" else None)
    if mode == "missing_config":
        fake = lambda evidence, **kw: {"language": "en", "source": "fallback", "structured": None, "text": kw.get("fallback_text", ""), "error": None}
    elif mode == "invalid_output":
        fake = lambda evidence, **kw: {"language": "en", "source": "fallback", "structured": None, "text": kw.get("fallback_text", ""), "error": "invalid provider response"}
    else:
        fake = lambda evidence, **kw: {"language": "en", "source": "fallback", "structured": None, "text": kw.get("fallback_text", ""), "error": "unsupported language: fr"}
    monkeypatch.setattr(mcp_bridge, "explain_failure", fake)
    out = _run_why(language="fr" if mode == "unsupported" else "en")
    assert "CRASH POINT:" in out
    assert "AI explanation" not in out


def test_mcp_no_secret_in_output(monkeypatch):
    span = make_span(attrs={"vibe.input": json.dumps({"api_key": SECRET_KEY, "password": SECRET_PW})})
    monkeypatch.setattr(mcp_bridge, "_fetch_spans", lambda limit=30: [span])
    monkeypatch.setattr(mcp_bridge, "_bridge_config", lambda: cfg())
    monkeypatch.setattr(
        mcp_bridge, "explain_failure",
        lambda evidence, **kw: {"language": "en", "source": "llm", "structured": VALID, "text": "clean", "error": None},
    )
    out = _run_why(language="en")
    assert SECRET_KEY not in out and SECRET_PW not in out


# -- Stage B (final): verified transport, mocked HTTP ----------------------------

import vibe_debug.explainer as explainer_module
from vibe_debug.config.config import VibeConfig


class _FakeHttpResponse:
    def __init__(self, payload=None, raise_error=False):
        self._payload = payload
        self._raise = raise_error

    def raise_for_status(self):
        if self._raise:
            import httpx as _httpx

            raise _httpx.HTTPError("401 Unauthorized")

    def json(self):
        return self._payload


def _install_fake_http(monkeypatch, response):
    import httpx as _httpx

    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return response

    monkeypatch.setattr(_httpx, "Client", FakeClient)
    return captured


def test_final_transport_verified_endpoint_model_auth(monkeypatch):
    fake_key = "fake-test-" + "credential"
    monkeypatch.setenv("MUSE_SPARK_API_KEY", fake_key)
    envelope = {"choices": [{"message": {"content": json.dumps(VALID)}, "finish_reason": "stop"}], "model": "muse-spark-1.3"}
    captured = _install_fake_http(monkeypatch, _FakeHttpResponse(envelope))
    config = VibeConfig(explainer_enabled=True)
    result = explain_failure(evidence(), config=config)
    assert result["source"] == "llm"
    assert captured["url"] == "https://api.meta.ai/v1/chat/completions"
    assert captured["json"]["model"] == "muse-spark-1.3"
    assert captured["headers"]["Authorization"] == f"Bearer {fake_key}"
    assert captured["json"]["response_format"]["type"] == "json_schema"
    assert captured["json"]["messages"][0]["role"] == "developer"
    assert result["structured"] == VALID


def test_final_transport_error_falls_back_without_leaking(monkeypatch):
    fake_key = "fake-test-" + "credential"
    monkeypatch.setenv("MUSE_SPARK_API_KEY", fake_key)
    _install_fake_http(monkeypatch, _FakeHttpResponse(raise_error=True))
    result = explain_failure(evidence(), config=VibeConfig(explainer_enabled=True), fallback_text="DET")
    assert result["source"] == "fallback"
    assert result["text"] == "DET"
    assert fake_key not in json.dumps(result)


def test_flagship_defaults_in_config():
    config = VibeConfig()
    assert config.explainer_model == "muse-spark-1.3"
    assert config.explainer_endpoint == "https://api.meta.ai/v1/chat/completions"
    assert explainer_module.DEFAULT_MODEL == "muse-spark-1.3"


# -- Stage 3 (limitations): timeout enforcement + citation validation -----------

def test_timeout_enforced_by_http_client(monkeypatch):
    import threading
    import time as _time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from vibe_debug.config.config import VibeConfig as _VC

    class SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            _time.sleep(3)
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = _VC(
            explainer_enabled=True,
            explainer_endpoint=f"http://127.0.0.1:{port}/chat/completions",
            explainer_timeout_seconds=0.5,
        )
        monkeypatch.setenv("MUSE_SPARK_API_KEY", "fake-test-" + "credential")
        started = _time.monotonic()
        result = explain_failure(evidence(), config=config, fallback_text="DET")
        elapsed = _time.monotonic() - started
        assert result["source"] == "fallback"
        assert elapsed < 2.5, f"timeout not enforced: {elapsed:.1f}s"
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("lang", ["en", "sw"])
def test_unknown_span_citation_falls_back(lang):
    bad = {**VALID, "evidence": ["'nonexistent_span' failed first"]}
    result = explain_failure(evidence(), language=lang, client=RecordingClient(bad), config=cfg(), fallback_text="DET")
    assert result["source"] == "fallback"
    assert result["error"] == "explanation cited unknown spans"


@pytest.mark.parametrize("lang", ["en", "sw"])
def test_real_span_citation_accepted(lang):
    good = {**VALID, "evidence": ["'route_query' status ERROR", "'call_llm' duration 4680ms"]}
    pkg = build_evidence_package(
        make_span(),
        [make_span(name="call_llm", status="ERROR", latency=4680, span_id="s3")],
    )
    result = explain_failure(pkg, language=lang, client=RecordingClient(good), config=cfg())
    assert result["source"] == "llm"


@pytest.mark.parametrize("lang", ["en", "sw"])
def test_evidence_field_and_status_citations_accepted(lang):
    # Correction: citing submitted evidence fields / status values is valid.
    good = {**VALID, "evidence": ["'input_preview' shows the request", "'ERROR' status on 'route_query'"]}
    result = explain_failure(evidence(), language=lang, client=RecordingClient(good), config=cfg())
    assert result["source"] == "llm"


@pytest.mark.parametrize("lang", ["en", "sw"])
def test_submitted_json_key_citations_accepted(lang):
    span = make_span(attrs={"vibe.input": json.dumps({"api_key": "k", "password": "p", "q": "Summarize"})})
    pkg = build_evidence_package(span, [])
    good = {**VALID, "evidence": ["keys 'api_key', 'password', 'q' present; 'q' is 'Summarize'"]}
    result = explain_failure(pkg, language=lang, client=RecordingClient(good), config=cfg())
    assert result["source"] == "llm"


def test_select_failure_span():
    ok = make_span(name="ok_span", status="OK")
    err = make_span(name="bad_span", status="ERROR")
    assert select_failure_span([ok, err]) is err
    assert select_failure_span([ok]) is None
    assert select_failure_span([]) is None
