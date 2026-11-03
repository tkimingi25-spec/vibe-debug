"""Offline evaluation pack over the fixed 8 fixtures (Stage 8).

Deterministic properties only: evidence correctness, redaction, and
schema handling via mocks. Live scoring is opt-in and lives in
tests/test_explainer.py::test_live_evaluation_scorecard_opt_in.
"""

import json
from types import SimpleNamespace

import pytest

from tests.explainer_eval_fixtures import EVAL_FIXTURES, SECRET_KEY, SECRET_PW
from vibe_debug.explainer import build_evidence_package, explain_failure

VALID = {
    "summary": "route_query failed.",
    "likely_cause": "A downstream step failed.",
    "evidence": ["'route_query' status ERROR"],
    "suggested_fix": ["Check route_query."],
    "confidence": "medium",
    "missing_information": [],
}


def _cfg(**kw):
    base = {
        "explainer_enabled": True,
        "explainer_model": "test-model",
        "explainer_endpoint": "http://provider.invalid/v1",
        "explainer_timeout_seconds": 2.0,
        "explainer_language": "en",
    }
    base.update(kw)
    return SimpleNamespace(**base)


class _Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return self.payload


def test_fixture_pack_has_eight_categories():
    assert len(EVAL_FIXTURES) == 8
    assert len({f["label"] for f in EVAL_FIXTURES}) == 8


@pytest.mark.parametrize("fixture", EVAL_FIXTURES, ids=[f["label"] for f in EVAL_FIXTURES])
def test_fixture_deterministic_properties(fixture):
    pkg = build_evidence_package(fixture["span"], fixture["related"])
    assert pkg["function"] == "route_query"
    assert pkg["error_type"] == fixture["expected_error_type"]
    assert pkg["silent_failure"] is fixture["expected_silent"]
    blob = json.dumps(pkg)
    assert SECRET_KEY not in blob and SECRET_PW not in blob

    client = _Client(VALID)
    result = explain_failure(pkg, client=client, config=_cfg(), fallback_text="DET")
    assert result["source"] == "llm"
    sent = json.dumps(client.calls[0])
    assert SECRET_KEY not in sent and SECRET_PW not in sent

    bad = explain_failure(pkg, client=_Client({"bad": 1}), config=_cfg(), fallback_text="DET")
    assert bad["source"] == "fallback" and bad["text"] == "DET"

    unknown = explain_failure(
        pkg,
        client=_Client({**VALID, "evidence": ["'invented_span' failed"]}),
        config=_cfg(),
        fallback_text="DET",
    )
    assert unknown["source"] == "fallback"
