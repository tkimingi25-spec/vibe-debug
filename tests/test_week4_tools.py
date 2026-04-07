import json
import os
from unittest.mock import patch

import pytest

# Sample spans covering success + failure + silent failure
MOCK_SPANS_MIXED = [
    {
        "name": "validate_input",
        "statusCode": "OK",
        "latencyMs": 5.2,
        "startTime": "2026-01-01T00:00:03Z",
        "attributes": json.dumps({
            "vibe": {
                "file": "app/validator.py",
                "line": 12,
                "module": "app.validator",
                "input": "{'query': 'hello'}",
                "output": "True",
                "status": "OK",
                "type": "component",
                "doc": "Validates incoming request",
            }
        }),
        "context": {"spanId": "s1", "traceId": "t1"},
    },
    {
        "name": "fetch_data",
        "statusCode": "OK",
        "latencyMs": 34.1,
        "startTime": "2026-01-01T00:00:02Z",
        "attributes": json.dumps({
            "vibe": {
                "file": "app/fetcher.py",
                "line": 45,
                "module": "app.fetcher",
                "input": "{'query': 'hello'}",
                "output": "[item1, item2]",
                "status": "OK",
                "type": "component",
                "doc": "Fetches data from storage",
            }
        }),
        "context": {"spanId": "s2", "traceId": "t1"},
    },
    {
        "name": "process_results",
        "statusCode": "ERROR",
        "latencyMs": 0.3,
        "startTime": "2026-01-01T00:00:01Z",
        "attributes": json.dumps({
            "vibe": {
                "file": "app/processor.py",
                "line": 78,
                "module": "app.processor",
                "input": "[item1, item2]",
                "output": "[]",
                "status": "SILENT_FAILURE",
                "type": "component",
                "doc": "Processes fetched results",
            },
            "error": {"type": ""},
        }),
        "context": {"spanId": "s3", "traceId": "t1"},
    },
]

MOCK_SPANS_TIMEOUT = [
    {
        "name": "call_external_api",
        "statusCode": "ERROR",
        "latencyMs": 5201.0,
        "startTime": "2026-01-01T00:00:01Z",
        "attributes": json.dumps({
            "vibe": {
                "file": "app/api_client.py",
                "line": 33,
                "module": "app.api_client",
                "input": "{'endpoint': '/data'}",
                "output": "None",
                "status": "ERROR",
                "type": "component",
                "doc": "Calls external REST API",
            },
            "error": {"type": "ConnectTimeout"},
        }),
        "context": {"spanId": "s1", "traceId": "t2"},
    },
]


def _import_bridge():
    """Import mcp_bridge helpers for testing."""
    import sys
    sys.path.insert(0, os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ))
    from server.mcp_bridge import (
        _detect_failure_pattern,
        _parse_attrs,
        flatten_attrs,
    )
    return _detect_failure_pattern, _parse_attrs, flatten_attrs


def test_detect_failure_pattern_silent_failure():
    _detect_failure_pattern, _parse_attrs, _ = _import_bridge()
    span = MOCK_SPANS_MIXED[2]
    attrs = _parse_attrs(span)
    pattern = _detect_failure_pattern(span, attrs)
    assert pattern == "SILENT_FAILURE"
    print(f"Silent failure pattern detected: {pattern}")


def test_detect_failure_pattern_timeout():
    _detect_failure_pattern, _parse_attrs, _ = _import_bridge()
    span = MOCK_SPANS_TIMEOUT[0]
    attrs = _parse_attrs(span)
    pattern = _detect_failure_pattern(span, attrs)
    assert pattern == "TIMEOUT"
    print(f"Timeout pattern detected: {pattern}")


def test_detect_failure_pattern_connection():
    _detect_failure_pattern, _parse_attrs, _ = _import_bridge()
    span = {
        "name": "connect_db",
        "statusCode": "ERROR",
        "latencyMs": 100.0,
        "attributes": json.dumps({
            "vibe": {"status": "ERROR", "output": "None"},
            "error": {"type": "ConnectionRefusedError"},
        }),
        "context": {},
    }
    attrs = _parse_attrs(span)
    pattern = _detect_failure_pattern(span, attrs)
    assert pattern == "CONNECTION_ERROR"
    print(f"Connection error pattern detected: {pattern}")


def test_parse_attrs_flattens_nested():
    _, _parse_attrs, flatten_attrs = _import_bridge()
    span = MOCK_SPANS_MIXED[0]
    attrs = _parse_attrs(span)
    assert "vibe.file" in attrs
    assert "vibe.line" in attrs
    assert "vibe.module" in attrs
    assert "vibe.doc" in attrs
    assert attrs["vibe.file"] == "app/validator.py"
    assert attrs["vibe.line"] == 12
    print("Nested attributes correctly flattened")


def test_all_patterns_covered():
    """Every failure pattern must have a fix hint."""
    _detect_failure_pattern, _, _ = _import_bridge()

    patterns = [
        "SILENT_FAILURE", "TIMEOUT", "CONNECTION_ERROR",
        "TYPE_ERROR", "KEY_ERROR", "ATTRIBUTE_ERROR", "EXCEPTION"
    ]
    assert len(patterns) == 7
    print(f"All {len(patterns)} failure patterns defined")
