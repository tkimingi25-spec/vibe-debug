import asyncio
import httpx
import json
import pytest
import time
from vibe_debug.tracer import init_vibedebug
from vibe_debug.decorators import vibetrace
from vibe_debug.runtime.sys_tracer import UniversalRuntimeTracer
import os

pytestmark = pytest.mark.requires_phoenix

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


@pytest.mark.asyncio
async def test_async_function_captured_as_span():
    """
    Async functions decorated with @vibetrace must produce
    spans just like synchronous functions.
    Currently @vibetrace uses a synchronous wrapper and
    does not handle async functions - await is lost.
    """
    init_vibedebug("gap3-async-test")

    @vibetrace(component_type="agent")
    async def async_knowledge_update(query: str) -> dict:
        """Background knowledge updater - runs async."""
        await asyncio.sleep(0.01)
        return {"updated": True, "query": query}

    result = await async_knowledge_update("transformer model")
    assert result["updated"] is True

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

    async_spans = [
        e["node"] for e in spans
        if e["node"]["name"] == "async_knowledge_update"
    ]

    assert len(async_spans) > 0, (
        "GAP 3: async function not captured as span - "
        "@vibetrace must handle async functions"
    )

    print("Async function captured as span")


@pytest.mark.asyncio
async def test_background_task_captured():
    """
    asyncio.create_task() spawns background workers.
    These must be captured even when they run independently
    of the main request path.
    """
    init_vibedebug("gap3-background-test")

    results = []

    @vibetrace(component_type="background")
    async def background_pipeline_update(data: str) -> str:
        """Runs as a background task to process updates."""
        await asyncio.sleep(0.05)
        results.append(data)
        return f"processed: {data}"

    task = asyncio.create_task(
        background_pipeline_update("new_data_node")
    )
    await task

    assert len(results) == 1

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

    bg_spans = [
        e["node"] for e in spans
        if e["node"]["name"] == "background_pipeline_update"
    ]

    assert len(bg_spans) > 0, (
        "GAP 3: background task not captured as span - "
        "asyncio tasks bypass sys.settrace()"
    )

    print("Background task captured as span")
