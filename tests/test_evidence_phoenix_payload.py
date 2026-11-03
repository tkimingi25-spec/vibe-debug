"""Defect B: evidence completeness for real Phoenix payloads.

Fixtures mimic the REAL Phoenix shape characterised in Stage 1:
Phoenix stores OTel dotted attributes as nested JSON
({"vibe": {...}, "error": {"type": ...}}), exception detail lives in
span `exception` events + `statusMessage`, and an exception span has
no `vibe.output`. All provider-free; no live calls.
"""

import json

from vibe_debug import phoenix_client
from vibe_debug.explainer import build_evidence_package

SECRET_KEY = "sk-test-" + "secret-123"

# Verbatim shape probed from Phoenix (GraphQL attributes string +
# events + statusMessage) for `sample_app/main.py --fail`.
REAL_ATTRS = {
    "execution_time_ms": 0.007152557373046875,
    "vibe": {
        "type": "component",
        "file": "C:\\Users\\hp\\Desktop\\vibe-debug\\sample_app\\processor.py",
        "status": "ERROR",
        "doc": "Normalize retrieved titles or raise a deliberate diagnostic error.",
        "line": 4,
        "input": "documents=[{'id': 'doc-1', 'title': 'Tracing pipelines', 'query': 'How does tracing work?'}, {'id': 'doc-2', 'title': 'Debugging failures', 'query': 'How does tracing work?'}], fail=True",
        "module": "processor",
        "name": "process_documents",
    },
    "error": {"type": "ValueError"},
}

REAL_EVENTS = [
    {
        "name": "exception",
        "timestamp": "2026-11-03T13:48:41.279155+00:00",
        "attributes": {
            "exception.type": "ValueError",
            "exception.message": "sample processor deliberately failed",
            "exception.escaped": "False",
            "exception.stacktrace": "Traceback (most recent call last):\nValueError: sample processor deliberately failed\n",
        },
    }
]


def real_failure_span(**overrides):
    span = {
        "name": "process_documents",
        "statusCode": "ERROR",
        "statusMessage": "ValueError: sample processor deliberately failed",
        "latencyMs": 4.0,
        "startTime": "2026-11-03T13:48:41",
        "attributes": json.dumps(REAL_ATTRS),
        "events": json.loads(json.dumps(REAL_EVENTS)),
    }
    span.update(overrides)
    return span


def test_real_phoenix_failure_evidence_complete():
    pkg = build_evidence_package(real_failure_span(), [])
    assert pkg["function"] == "process_documents"
    assert pkg["status"] == "ERROR"
    assert pkg["error_type"] == "ValueError"
    assert pkg["error_message"] == "sample processor deliberately failed"
    assert pkg["file"].endswith("processor.py")
    assert pkg["line"] == 4
    assert pkg["module"] == "processor"
    assert "documents=" in pkg["input_preview"]
    assert "Tracing pipelines" in pkg["input_preview"]
    # The function raised, so no output exists: null stays null.
    assert pkg["output_preview"] is None
    assert pkg["silent_failure"] is False


def test_status_message_fallback_without_events():
    span = real_failure_span(events=[])
    pkg = build_evidence_package(span, [])
    assert pkg["error_type"] == "ValueError"
    assert pkg["error_message"] == "sample processor deliberately failed"
    assert pkg["silent_failure"] is False


def test_genuine_silent_failure_still_silent():
    span = {
        "name": "search_index",
        "statusCode": "ERROR",
        "statusMessage": "Silent Failure detected",
        "latencyMs": 1.0,
        "startTime": "2026-11-03T13:48:41",
        "attributes": json.dumps({
            "vibe": {"type": "component", "module": "search", "file": "/app/search.py",
                     "line": 9, "input": "query=hello", "output": "[]", "status": "ERROR"},
        }),
        "events": [],
    }
    pkg = build_evidence_package(span, [])
    assert pkg["silent_failure"] is True
    assert pkg["error_type"] is None
    assert pkg["module"] == "search"
    assert "query=hello" in pkg["input_preview"]


def test_redaction_still_applied_to_nested_input():
    attrs = json.loads(json.dumps(REAL_ATTRS))
    attrs["vibe"]["input"] = json.dumps({"api_key": SECRET_KEY, "q": "keep-me"})
    pkg = build_evidence_package(real_failure_span(attributes=json.dumps(attrs)), [])
    blob = json.dumps(pkg)
    assert SECRET_KEY not in blob
    assert "keep-me" in blob
    assert "***" in blob


def test_shared_fetch_requests_events_and_status_message():
    query = phoenix_client._span_query(5, include_context=False)
    assert "statusMessage" in query
    assert "events" in query
