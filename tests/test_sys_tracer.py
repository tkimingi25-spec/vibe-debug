import json
import os
import time

import httpx

from vibe_debug.runtime.sys_tracer import (
    RelevanceFilter, UniversalRuntimeTracer
)
from vibe_debug.tracer import init_vibedebug

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

QUERY = """
{ projects { edges { node {
    spans(first: 20, sort: {col: startTime, dir: desc}) {
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


def test_relevance_filter_skips_libraries():
    """
    RelevanceFilter must skip site-packages and stdlib,
    and only pass project files.
    """
    f = RelevanceFilter(project_root=PROJECT_ROOT)

    assert not f.is_relevant(
        "C:\\Python313\\Lib\\site-packages\\httpx\\_client.py",
        "send"
    )
    assert not f.is_relevant(
        "/usr/lib/python3.13/json/__init__.py",
        "loads"
    )

    assert not f.is_relevant(
        os.path.join(PROJECT_ROOT, "tests", "mock_pipeline.py"),
        "__init__"
    )

    assert f.is_relevant(
        os.path.join(PROJECT_ROOT, "tests", "mock_pipeline.py"),
        "route_query"
    )
    assert f.is_relevant(
        os.path.join(PROJECT_ROOT, "tests", "mock_pipeline.py"),
        "retrieve_documents"
    )

    print("Relevance filter correctly separates project from library code")


def test_universal_tracer_captures_project_functions():
    """
    UniversalRuntimeTracer must automatically capture function calls
    from project files without any manual decoration.
    """
    init_vibedebug("sys-tracer-test")

    tracer = UniversalRuntimeTracer(
        project_root=PROJECT_ROOT,
        project_name="sys-tracer-test",
    )

    tracer.start()

    from tests.mock_pipeline import (
        generate_response,
        lookup_knowledge_graph,
        retrieve_documents,
        route_query,
    )

    result1 = route_query("test query for sys tracer")
    result2 = retrieve_documents("test query for sys tracer")
    result3 = lookup_knowledge_graph([])
    result4 = generate_response(result2, "test query")

    tracer.stop()

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    r.raise_for_status()
    payload = r.json()
    span_edges = (
        payload["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    pipeline_funcs = {
        "route_query", "retrieve_documents",
        "lookup_knowledge_graph", "generate_response"
    }
    found = {}
    for item in span_edges:
        node = item["node"]
        if node["name"] in pipeline_funcs and node["name"] not in found:
            attrs_raw = node.get("attributes", "{}")
            attrs_parsed = (
                json.loads(attrs_raw)
                if isinstance(attrs_raw, str)
                else attrs_raw
            )
            attrs = flatten_attrs(attrs_parsed)
            found[node["name"]] = {
                "status": node["statusCode"],
                "file": attrs.get("vibe.file", "unknown"),
                "line": attrs.get("vibe.line", "unknown"),
                "type": attrs.get("vibe.type", "unknown"),
            }

    print("\n--- Universal Runtime Tracer Results ---")
    for name, info in found.items():
        print(
            f"  {info['status']:6} | {name:30} | "
            f"{info['file']}:{info['line']} | type={info['type']}"
        )

    assert len(found) >= 3, (
        f"Expected at least 3 pipeline spans, got {len(found)}"
    )

    print(
        f"\nUniversal tracer captured {len(found)} functions "
        f"with zero manual decoration"
    )
