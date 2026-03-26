import json
import os
import time

import httpx
import pytest

from vibe_debug.runtime.io_interceptor import IOInterceptor
from vibe_debug.tracer import init_vibedebug

PROJECT_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)

QUERY = """
{ projects { edges { node {
    spans(first: 30, sort: {col: startTime, dir: desc}) {
        edges { node { name statusCode attributes } }
    }
} } } }
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


def test_interceptor_patches_available_libraries():
    """
    IOInterceptor.start() must successfully patch
    at least one HTTP library and the socket layer.
    Returns dict of results - none should raise exceptions.
    """
    init_vibedebug("io-interceptor-test")
    interceptor = IOInterceptor(project_name="io-interceptor-test")

    results = interceptor.start()

    assert isinstance(results, dict)
    assert "requests" in results
    assert "httpx" in results
    assert "socket" in results

    assert results["socket"] is True, "Socket patch must always succeed"

    interceptor.stop()

    print("\n--- Patch Results ---")
    for lib, success in results.items():
        status = "patched" if success else "not installed"
        print(f"  {lib:12} {status}")
    print("\nIOInterceptor patches available libraries")


def test_interceptor_captures_http_call_as_span():
    """
    Make a real HTTP call while interceptor is active.
    Verify a span appears in Phoenix with correct attributes.
    """
    init_vibedebug("io-interceptor-test")
    interceptor = IOInterceptor(project_name="io-interceptor-test")
    interceptor.start()

    try:
        import httpx as httpx_lib
        response = httpx_lib.get(
            "https://httpbin.org/get",
            timeout=10.0
        )
        call_made = True
    except Exception as e:
        print(f"  External call failed (network): {e}")
        call_made = False
    finally:
        interceptor.stop()

    if not call_made:
        pytest.skip("Network unavailable - skipping HTTP capture test")

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    payload = r.json()
    span_edges = (
        payload["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    http_spans = []
    for item in span_edges:
        node = item["node"]
        if node["name"].startswith("http."):
            attrs_raw = node.get("attributes", "{}")
            attrs = flatten_attrs(
                json.loads(attrs_raw)
                if isinstance(attrs_raw, str)
                else attrs_raw
            )
            if "httpbin" in attrs.get("http.url", ""):
                http_spans.append({
                    "name": node["name"],
                    "status": node["statusCode"],
                    "url": attrs.get("http.url", ""),
                    "method": attrs.get("http.method", ""),
                    "latency": attrs.get("http.latency_ms", 0),
                    "status_code": attrs.get("http.status_code", 0),
                })

    print("\n--- Captured HTTP Spans ---")
    for s in http_spans:
        print(
            f"  {s['status']:6} | {s['name']} | "
            f"{s['status_code']} | {s['latency']:.1f}ms"
        )

    assert len(http_spans) >= 1, (
        "No HTTP spans captured - interceptor not working"
    )
    assert http_spans[0]["method"] == "GET"

    print("\nHTTP call captured as span without any adapter")


def test_interceptor_skips_phoenix_calls():
    """
    Interceptor must never create spans for Phoenix OTLP calls.
    This prevents infinite loops where tracing the tracer
    creates more spans which need more tracing.
    """
    init_vibedebug("io-interceptor-test")
    interceptor = IOInterceptor(project_name="io-interceptor-test")
    interceptor.start()

    try:
        import httpx as httpx_lib
        httpx_lib.post(
            "http://localhost:6006/graphql",
            json={"query": "{ projects { edges { node { name } } } }"},
            timeout=5.0,
        )
    finally:
        interceptor.stop()

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    payload = r.json()
    span_edges = (
        payload["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    phoenix_spans = []
    for item in span_edges:
        node = item["node"]
        attrs_raw = node.get("attributes", "{}")
        attrs = flatten_attrs(
            json.loads(attrs_raw)
            if isinstance(attrs_raw, str)
            else attrs_raw
        )
        url = attrs.get("http.url", "")
        if "6006" in url or "4317" in url:
            phoenix_spans.append(node["name"])

    assert len(phoenix_spans) == 0, (
        f"Phoenix calls were captured as spans - infinite loop risk: {phoenix_spans}"
    )

    print("Phoenix OTLP calls correctly excluded from tracing")
