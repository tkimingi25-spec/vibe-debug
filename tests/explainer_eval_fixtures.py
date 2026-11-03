"""Reusable Muse Spark evaluation fixtures (Stage 8, limitations task).

Fixed set of 8 failure categories as evidence-package fixtures. The
normal suite asserts only deterministic properties (evidence
correctness, redaction, schema handling) via mocks. Live scoring is
opt-in (VIBE_EXPLAINER_LIVE=1) and reported separately; mocked/live
machine results are not human Swahili review.

Sentinel secrets are built by concatenation so they stay test-local.
"""

import json

SECRET_KEY = "sk-eval-" + "secret-999"
SECRET_PW = "eval-test-" + "password"


def make_eval_span(name="route_query", status="ERROR", latency=5012, attrs=None, span_id="s1"):
    base = {
        "vibe.input": json.dumps({"api_key": SECRET_KEY, "password": SECRET_PW, "q": "Summarize"}),
        "vibe.output": "",
        "vibe.file": "/app/pipeline.py",
        "vibe.line": 42,
        "vibe.module": "pipeline",
        "vibe.status": status,
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
        "context": {"spanId": span_id, "traceId": "t1"},
    }


def _child():
    return make_eval_span(
        name="call_llm", status="ERROR", latency=4680, span_id="s3",
        attrs={"error.type": "TimeoutError"},
    )


# Each fixture: label, failing span, related spans, expected fields.
EVAL_FIXTURES = [
    {
        "label": "timeout",
        "span": make_eval_span(attrs={"error.type": "TimeoutError", "vibe.output": ""}),
        "related": [_child()],
        "expected_error_type": "TimeoutError",
        "expected_silent": False,
    },
    {
        "label": "silent failure/empty result",
        "span": make_eval_span(attrs={"error.type": "", "vibe.output": "{}"}),
        "related": [],
        "expected_error_type": None,
        "expected_silent": True,
    },
    {
        "label": "missing API key (secret redacted)",
        "span": make_eval_span(attrs={"error.type": "AuthenticationError"}),
        "related": [],
        "expected_error_type": "AuthenticationError",
        "expected_silent": False,
    },
    {
        "label": "invalid JSON from downstream",
        "span": make_eval_span(attrs={"error.type": "JSONDecodeError", "vibe.output": "{not json"}),
        "related": [],
        "expected_error_type": "JSONDecodeError",
        "expected_silent": False,
    },
    {
        "label": "database connection failure",
        "span": make_eval_span(attrs={"error.type": "ConnectionError", "vibe.output": ""}),
        "related": [],
        "expected_error_type": "ConnectionError",
        "expected_silent": False,
    },
    {
        "label": "downstream HTTP 500",
        "span": make_eval_span(attrs={"error.type": "HTTPStatusError", "vibe.output": "500"}),
        "related": [],
        "expected_error_type": "HTTPStatusError",
        "expected_silent": False,
    },
    {
        "label": "empty retrieval result",
        "span": make_eval_span(attrs={"error.type": "", "vibe.output": "[]"}),
        "related": [],
        "expected_error_type": None,
        "expected_silent": True,
    },
    {
        "label": "exception in nested child span",
        "span": make_eval_span(),
        "related": [_child()],
        "expected_error_type": "TimeoutError",
        "expected_silent": False,
    },
]
