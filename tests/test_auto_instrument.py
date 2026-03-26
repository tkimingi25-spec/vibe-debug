import json

import httpx
import vibe_debug

# One line - no manual decorators anywhere
vibe_debug.start("auto-instrument-test")

# Import AFTER start() so patching takes effect
from tests.mock_pipeline import (
    route_query,
    retrieve_documents,
    lookup_knowledge_graph,
    generate_response,
)

QUERY = """
{ projects { edges { node {
    spans(first: 20, sort: {col: startTime, dir: desc}) {
        edges { node { name statusCode attributes } }
    }
} } } }
"""


def test_auto_instrumentation():
    """Verify all pipeline functions are traced without any decorators."""
    destination = route_query("explain transformer architecture")
    assert destination == "rag"

    docs = retrieve_documents("transformer architecture")
    assert len(docs) == 2

    kg = lookup_knowledge_graph([])  # intentional silent failure
    assert kg == {}

    response = generate_response(docs, "transformer architecture")
    assert "2 documents" in response

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
        "route_query",
        "retrieve_documents",
        "lookup_knowledge_graph",
        "generate_response"
    }

    found = {}
    for item in span_edges:
        node = item["node"]
        if node["name"] in pipeline_funcs:
            attrs_raw = node.get("attributes", "{}")
            attrs_parsed = (
                json.loads(attrs_raw)
                if isinstance(attrs_raw, str)
                else attrs_raw
            )
            flat = {}

            def flatten(d, prefix=""):
                for k, v in d.items():
                    key = f"{prefix}.{k}" if prefix else k
                    if isinstance(v, dict):
                        flatten(v, key)
                    else:
                        flat[key] = v

            flatten(attrs_parsed)
            if node["name"] not in found:
                found[node["name"]] = {
                    "status": node["statusCode"],
                    "file": flat.get("vibe.file", "unknown"),
                    "line": flat.get("vibe.line", "unknown"),
                }

    print("\n--- Auto-Instrumented Pipeline Spans ---")
    for func_name, info in found.items():
        print(
            f"{info['status']:6} | {func_name:30} | "
            f"{info['file']}:{info['line']}"
        )

    assert "route_query" in found, "route_query was not traced"
    assert "retrieve_documents" in found, "retrieve_documents was not traced"
    assert "lookup_knowledge_graph" in found, "lookup_knowledge_graph was not traced"
    assert "generate_response" in found, "generate_response was not traced"

    assert found["lookup_knowledge_graph"]["status"] == "ERROR", (
        "Silent failure on lookup_knowledge_graph not detected"
    )

    print("\nAuto-instrumentation working - zero manual decorators needed")
