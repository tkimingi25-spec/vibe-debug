import json

import httpx
import pytest

from vibe_debug import init_vibedebug, vibetrace

pytestmark = pytest.mark.requires_phoenix

QUERY = """
{
  projects {
    edges {
      node {
        spans(first: 10, sort: {col: startTime, dir: desc}) {
          edges {
            node {
              name
              statusCode
              latencyMs
              startTime
              attributes
              context { spanId traceId }
            }
          }
        }
      }
    }
  }
}
"""


def flatten_attrs(attrs: dict, prefix="") -> dict:
    flat = {}
    for key, value in attrs.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten_attrs(value, full_key))
        else:
            flat[full_key] = value
    return flat


@vibetrace(component_type="test")
def success_func(x):
    """Doubles the input value. Returns x * 2."""
    return x * 2


@vibetrace(component_type="test")
def silent_failure_func():
    """Fetches results from vector store. Returns list of docs."""
    return []


@vibetrace(component_type="test")
def exception_func():
    """Validates security token. Raises on invalid input."""
    raise RuntimeError("integration boom")


def test_spans_reach_phoenix():
    init_vibedebug("integration-test")
    test_span_names = {"success_func", "silent_failure_func", "exception_func"}

    assert success_func(10) == 20
    assert silent_failure_func() == []

    try:
        exception_func()
    except RuntimeError:
        pass

    response = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()

    span_edges = payload["data"]["projects"]["edges"][0]["node"]["spans"]["edges"]
    assert len(span_edges) >= 1

    found_test_span = False
    for item in span_edges:
        node = item["node"]
        if node["name"] not in test_span_names:
            continue
        found_test_span = True
        attrs_raw = node.get("attributes", "{}")
        attrs = flatten_attrs(
            json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
        )
        assert "vibe.file" in attrs, f"Missing vibe.file on {node['name']}"
        assert "vibe.line" in attrs, f"Missing vibe.line on {node['name']}"
        print(f"{node['name']}: {attrs.get('vibe.file')}:{attrs.get('vibe.line')}")
        print(f"{node['name']}: {node['statusCode']}")
    assert found_test_span, "No spans found for current integration test run"
