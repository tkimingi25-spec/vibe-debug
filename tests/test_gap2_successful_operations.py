import httpx
import json
import pytest
from vibe_debug.tracer import init_vibedebug
from vibe_debug.decorators import vibetrace

pytestmark = pytest.mark.requires_phoenix

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


def test_successful_write_operation_captured_with_output():
    """
    When a function successfully writes data - adds nodes,
    updates a record, stores a result - the output must be
    captured in the span so the IDE knows what changed.
    Currently spans capture output but it is truncated to
    str(result)[:200] which loses structured data.
    """
    init_vibedebug("gap2-test")

    @vibetrace(component_type="storage")
    def add_knowledge_node(entity: str, relations: list) -> dict:
        """Adds a new knowledge node to the graph."""
        return {
            "status": "created",
            "entity": entity,
            "relations_added": len(relations),
            "node_id": "node_abc123",
        }

    result = add_knowledge_node(
        "transformer",
        ["attention", "encoder", "decoder"]
    )
    assert result["status"] == "created"

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    r.raise_for_status()
    spans = (
        r.json()["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    node_spans = [
        e["node"] for e in spans
        if e["node"]["name"] == "add_knowledge_node"
    ]

    assert len(node_spans) > 0, \
        "add_knowledge_node span not found"

    attrs = flatten_attrs(
        json.loads(node_spans[0].get("attributes", "{}"))
    )

    output = attrs.get("vibe.output", "")

    assert "node_abc123" in str(output), \
        "GAP 2: node_id not captured in output"
    assert "relations_added" in str(output), \
        "GAP 2: relations_added count not captured in output"

    assert attrs.get("vibe.type") == "storage", \
        "GAP 2: component type not captured for write operations"

    print("Successful write operation captured with output")
    print(f"   output: {output}")


def test_operation_count_tracked_across_multiple_calls():
    """
    When the same component is called multiple times -
    like a graph updater adding multiple nodes - each call
    must be individually tracked so the IDE can report
    '5 nodes added, 3 failed' rather than just the last state.
    """
    init_vibedebug("gap2-count-test")

    @vibetrace(component_type="storage")
    def update_graph_node(node_id: str, data: dict) -> bool:
        """Updates an existing graph node."""
        return True

    for i in range(5):
        update_graph_node(f"node_{i}", {"weight": i * 0.1})

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    r.raise_for_status()
    spans = (
        r.json()["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    update_spans = [
        e["node"] for e in spans
        if e["node"]["name"] == "update_graph_node"
        and e["node"]["statusCode"] == "OK"
    ]

    assert len(update_spans) >= 5, (
        f"GAP 2: Expected 5 update spans, got {len(update_spans)}"
    )

    print(
        f"All {len(update_spans)} update operations "
        f"individually tracked"
    )
