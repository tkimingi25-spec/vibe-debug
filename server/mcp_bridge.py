import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

# 1. Initialize FastMCP
mcp = FastMCP("vibe-debug")

PHOENIX_BASE_URL = "http://localhost:6006"
PHOENIX_GRAPHQL_URL = "http://localhost:6006/graphql"

GET_LAST_FAILURE_QUERY = """
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

EXPLAIN_TRACE_QUERY = """
{
  projects {
    edges {
      node {
        spans(first: 5, sort: {col: startTime, dir: desc}) {
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


def _normalize_attributes(attributes):
    if isinstance(attributes, str):
        try:
            return json.loads(attributes)
        except json.JSONDecodeError:
            return {"raw": attributes}
    if isinstance(attributes, dict):
        return attributes
    return {}


def flatten_attrs(attrs: dict, prefix="") -> dict:
    flat = {}
    for key, value in attrs.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten_attrs(value, full_key))
        else:
            flat[full_key] = value
    return flat


def query_phoenix(query: str):
    """Query Phoenix GraphQL and return the raw JSON payload."""
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(
                PHOENIX_GRAPHQL_URL,
                json={"query": query},
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {"error": f"Could not connect to Phoenix GraphQL: {str(e)}"}


def _extract_spans(payload):
    data = payload.get("data", {})
    edges = data.get("projects", {}).get("edges", [])
    if not edges:
        return []

    span_edges = edges[0].get("node", {}).get("spans", {}).get("edges", [])
    spans = []
    for item in span_edges:
        if not isinstance(item, dict):
            continue
        node = item.get("node", {})
        if not isinstance(node, dict):
            continue
        spans.append(
            {
                "name": node.get("name", "Unknown"),
                "statusCode": node.get("statusCode", "UNSET"),
                "latencyMs": node.get("latencyMs"),
                "startTime": node.get("startTime", "N/A"),
                "attributes": _normalize_attributes(node.get("attributes", {})),
                "context": node.get("context", {}) if isinstance(node.get("context"), dict) else {},
            }
        )
    return spans


# --- MCP TOOLS ---

@mcp.tool()
async def get_last_failure():
    """
    Scans recent traces in Phoenix. Prioritizes ERROR spans,
    but falls back to recent OK spans if no errors are found.
    """
    data = query_phoenix(GET_LAST_FAILURE_QUERY)

    if not data or "error" in data:
        return f"Phoenix Connection Error: {data.get('error', 'Unknown error')}"
    if data.get("errors"):
        return f"Phoenix GraphQL Error: {data['errors']}"

    spans = _extract_spans(data)
    if not spans:
        return "Phoenix is empty. Run your mock_pipeline.py first!"

    error_spans = [span for span in spans if span.get("statusCode") == "ERROR"]
    latest = error_spans[0] if error_spans else spans[0]
    status_code = latest.get("statusCode", "UNSET")

    prefix = "FAILURE DETECTED" if status_code == "ERROR" else "LATEST TRACE (No Errors)"
    attrs_raw = latest.get("attributes", "{}")
    attrs_parsed = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
    attrs = flatten_attrs(attrs_parsed)

    report = [
        prefix,
        f"Component: {latest.get('name', 'Unknown')}",
        f"Status: {status_code}",
        f"File: {attrs.get('vibe.file', 'unknown')}",
        f"Line: {attrs.get('vibe.line', 'unknown')}",
        f"Module: {attrs.get('vibe.module', 'unknown')}",
        f"Time: {latest.get('startTime', 'N/A')}",
        "-" * 20,
        "Details:",
    ]

    found_attrs = False
    for key in ["vibe.type", "vibe.input", "vibe.output", "vibe.status"]:
        if key in attrs:
            report.append(f"{key}: {attrs[key]}")
            found_attrs = True

    if not found_attrs:
        report.append("(No 'vibe.' specific attributes found in this span)")

    if attrs.get("vibe.doc"):
        report.append(f"Purpose: {attrs.get('vibe.doc', '')}")

    return "\n".join(report)


@mcp.tool()
async def explain_trace(trace_id: str = None):
    """Summarizes the last 5 execution spans."""
    data = query_phoenix(EXPLAIN_TRACE_QUERY)
    if not data or "error" in data:
        return f"Phoenix Connection Error: {data.get('error', 'Unknown error')}"
    if data.get("errors"):
        return f"Phoenix GraphQL Error: {data['errors']}"

    spans = _extract_spans(data)
    if not spans:
        return "No traces found."

    summary = ["Trace Waterfall Summary:"]
    for span in spans:
        status_code = span.get("statusCode", "UNSET")
        label = "OK" if status_code == "OK" else status_code
        latency_ms = span.get("latencyMs")
        latency_text = f"{latency_ms:.2f}ms" if isinstance(latency_ms, (int, float)) else "N/A"
        attrs_raw = span.get("attributes", "{}")
        attrs_parsed = json.loads(attrs_raw) if isinstance(attrs_raw, str) else attrs_raw
        attrs = flatten_attrs(attrs_parsed)
        file_name = os.path.basename(attrs.get("vibe.file", "unknown"))
        line_number = attrs.get("vibe.line", "unknown")
        summary.append(
            f"{label} {span.get('name', 'Unknown')} ({latency_text}) - {file_name}:{line_number}"
        )

    return "\n".join(summary)


@mcp.tool()
async def get_pipeline_map():
    """
    Returns the complete discovered pipeline map for the project.
    Shows all components, their relationships, crash history,
    and components found in code that have never been observed running.
    Useful for understanding project structure without reading any code.
    """
    cache_path = ".vibe_pipeline_cache.json"

    if not os.path.exists(cache_path):
        return (
            "No pipeline map found yet.\n"
            "Run vibe_debug.start() in your project to generate one.\n"
            "The map is built from static code analysis and "
            "grows richer with each run."
        )

    try:
        with open(cache_path) as f:
            data = json.load(f)
    except Exception as e:
        return f"Error reading pipeline cache: {e}"

    components = data.get("components", {})
    total_traces = data.get("total_traces", 0)
    total_crashes = data.get("total_crashes", 0)
    entry_points = data.get("entry_points", [])
    project_name = data.get("project_name", "unknown")

    lines = [
        f"PIPELINE MAP - {project_name}",
        f"Components: {len(components)} | "
        f"Traces observed: {total_traces} | "
        f"Crashes: {total_crashes}",
        "",
    ]

    if entry_points:
        lines.append("ENTRY POINTS:")
        for ep in entry_points:
            lines.append(f"   {ep}")
        lines.append("")

    crash_prone = [
        c for c in components.values()
        if c.get("times_failed", 0) > 0
    ]
    if crash_prone:
        lines.append("CRASH-PRONE COMPONENTS:")
        for c in crash_prone:
            rate = (
                c["times_failed"] / c["times_called"] * 100
                if c["times_called"] > 0 else 0
            )
            lines.append(
                f"   {c['name']:30} "
                f"fail_rate={rate:.0f}% "
                f"file={c['file']}:{c['line']}"
            )
        lines.append("")

    never_observed = [
        c for c in components.values()
        if c.get("times_called", 0) == 0
        and "test" not in c.get("module", "")
        and "vibe_debug" not in c.get("module", "")
    ]
    if never_observed:
        lines.append(
            f"NEVER OBSERVED "
            f"({len(never_observed)} found in code, never ran):"
        )
        for c in never_observed[:10]:
            lines.append(
                f"   {c['name']:30} "
                f"{c.get('file', '')}:{c.get('line', '')}"
            )

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
