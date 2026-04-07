import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = os.getenv(
    "VIBE_PROJECT_ROOT",
    os.getcwd()
)
PIPELINE_CACHE = os.path.join(
    PROJECT_ROOT,
    ".vibe_pipeline_cache.json"
)

# 1. Initialize FastMCP
mcp = FastMCP("vibe-debug")

PHOENIX_BASE_URL = "http://localhost:6006"
PHOENIX_GRAPHQL_URL = "http://localhost:6006/graphql"


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


def _fetch_spans(limit: int = 20) -> list:
    """Fetch recent spans from Phoenix via GraphQL."""
    gql = f"""
    {{
      projects {{
        edges {{
          node {{
            spans(first: {limit}, sort: {{col: startTime, dir: desc}}) {{
              edges {{
                node {{
                  name statusCode latencyMs
                  startTime attributes
                  context {{ spanId traceId }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """
    data = query_phoenix(gql)
    if "error" in data:
        return []
    try:
        edges = (
            data["data"]["projects"]["edges"][0]
            ["node"]["spans"]["edges"]
        )
        return [e["node"] for e in edges]
    except Exception:
        return []


def _parse_attrs(span: dict) -> dict:
    """Parse and flatten span attributes."""
    attrs_raw = span.get("attributes", "{}")
    try:
        attrs_parsed = (
            json.loads(attrs_raw)
            if isinstance(attrs_raw, str)
            else attrs_raw
        )
        return flatten_attrs(attrs_parsed)
    except Exception:
        return {}


def _detect_failure_pattern(span: dict, attrs: dict) -> str:
    """
    Detect failure pattern from span data - no LLM needed.
    Returns one of: SILENT_FAILURE, TIMEOUT, CONNECTION_ERROR,
    TYPE_ERROR, KEY_ERROR, ATTRIBUTE_ERROR, EXCEPTION
    """
    error_type = attrs.get("error.type", "").lower()
    vibe_status = attrs.get("vibe.status", "")
    latency = span.get("latencyMs") or 0
    output = str(attrs.get(
        "vibe.output",
        attrs.get("vibe.output_preview", "")
    ))

    if vibe_status == "SILENT_FAILURE":
        return "SILENT_FAILURE"
    if latency > 3000:
        return "TIMEOUT"
    if "timeout" in error_type or "timeout" in vibe_status.lower():
        return "TIMEOUT"
    if "connect" in error_type or "connection" in error_type:
        return "CONNECTION_ERROR"
    if "typeerror" in error_type:
        return "TYPE_ERROR"
    if "keyerror" in error_type:
        return "KEY_ERROR"
    if "attributeerror" in error_type:
        return "ATTRIBUTE_ERROR"
    if output in ("None", "[]", "{}", "''", "\"\"", ""):
        return "SILENT_FAILURE"
    return "EXCEPTION"


def _get_downstream_impact(
    module: str, component_name: str
) -> list:
    """Get downstream components from pipeline cache."""
    try:
        if not os.path.exists(PIPELINE_CACHE):
            return []
        with open(PIPELINE_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        components = data.get("components", {})
        downstream = []
        for name, c in components.items():
            if (c.get("times_called", 0) == 0 and
                    name != component_name and
                    "test" not in c.get("module", "") and
                    "vibe_debug" not in c.get("module", "")):
                downstream.append(c)
        return downstream[:8]
    except Exception:
        return []


def _get_component_history_data(component_name: str) -> dict:
    """Get historical stats for a specific component."""
    try:
        if not os.path.exists(PIPELINE_CACHE):
            return {}
        with open(PIPELINE_CACHE, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("components", {}).get(
            component_name, {}
        )
    except Exception:
        return {}


# --- MCP TOOLS ---

@mcp.tool()
async def get_last_failure():
    """
    Scans recent traces in Phoenix. Prioritizes ERROR spans,
    but falls back to recent OK spans if no errors are found.
    """
    spans = _fetch_spans(limit=10)
    if not spans:
        return "Phoenix is empty. Run your mock_pipeline.py first!"

    error_spans = [span for span in spans if span.get("statusCode") == "ERROR"]
    latest = error_spans[0] if error_spans else spans[0]
    status_code = latest.get("statusCode", "UNSET")
    attrs = _parse_attrs(latest)

    prefix = "FAILURE DETECTED" if status_code == "ERROR" else "LATEST TRACE (No Errors)"
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
    spans = _fetch_spans(limit=5)
    if not spans:
        return "No traces found."

    summary = ["Trace Waterfall Summary:"]
    for span in spans:
        status_code = span.get("statusCode", "UNSET")
        label = "OK" if status_code == "OK" else status_code
        latency_ms = span.get("latencyMs")
        latency_text = f"{latency_ms:.2f}ms" if isinstance(latency_ms, (int, float)) else "N/A"
        attrs = _parse_attrs(span)
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
    cache_path = PIPELINE_CACHE

    if not os.path.exists(cache_path):
        return (
            "No pipeline map found yet.\n"
            "Run vibe_debug.start() in your project to generate one.\n"
            "The map is built from static code analysis and "
            "grows richer with each run."
        )

    try:
        with open(cache_path, encoding="utf-8") as f:
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


@mcp.tool()
async def why_did_this_fail():
    """
    Returns a fully structured failure analysis built from
    trace data. Includes: crash point, failure pattern type,
    exact file and line, inputs and outputs at failure,
    downstream components that never ran, historical failure
    count for this component, and timing anomalies.
    The IDE assistant uses this to explain failures without
    reading any source files.
    """
    spans = _fetch_spans(limit=30)
    if not spans:
        return (
            "No trace data in Phoenix.\n"
            "Make sure Phoenix is running and your project "
            "has called vibe_debug.start()."
        )

    error_span = None
    ok_spans = []
    for span in spans:
        if span["statusCode"] == "ERROR" and error_span is None:
            error_span = span
        elif span["statusCode"] == "OK":
            ok_spans.append(span)

    if not error_span:
        return "No failures in recent traces. All spans completed OK."

    attrs = _parse_attrs(error_span)
    pattern = _detect_failure_pattern(error_span, attrs)
    downstream = _get_downstream_impact(
        attrs.get("vibe.module", ""),
        attrs.get("vibe.name", error_span["name"])
    )
    history = _get_component_history_data(error_span["name"])

    timing_note = ""
    latency = error_span.get("latencyMs") or 0
    if latency > 3000:
        timing_note = (
            f"⏱ SLOW: {latency:.0f}ms - possible timeout or blocking call"
        )
    elif latency < 1 and attrs.get("vibe.status") == "ERROR":
        timing_note = (
            f"⚡ INSTANT FAIL: {latency:.2f}ms - failed before doing any work"
        )

    lines = [
        "=== FAILURE ANALYSIS ===",
        "",
        f"CRASH POINT:     {error_span['name']}",
        f"FILE:            {attrs.get('vibe.file', 'unknown')}",
        f"LINE:            {attrs.get('vibe.line', '?')}",
        f"MODULE:          {attrs.get('vibe.module', '?')}",
        f"PATTERN:         {pattern}",
        f"DURATION:        {latency:.1f}ms",
    ]

    if timing_note:
        lines.append(f"TIMING NOTE:     {timing_note}")

    lines.extend([
        "",
        "INPUTS AT FAILURE:",
        f"  {attrs.get('vibe.input', attrs.get('vibe.input_preview', 'not captured'))}",
        "",
        "OUTPUT AT FAILURE:",
        f"  {attrs.get('vibe.output', attrs.get('vibe.output_preview', 'not captured'))}",
        "",
        "ERROR DETAIL:",
        f"  type:    {attrs.get('error.type', 'not captured')}",
        f"  message: {attrs.get('vibe.status', 'not captured')}",
        "",
        "DOCSTRING (what this function is supposed to do):",
        f"  {attrs.get('vibe.doc', 'no docstring captured')}",
    ])

    if history:
        lines.extend([
            "",
            "FAILURE HISTORY FOR THIS COMPONENT:",
            f"  times called:   {history.get('times_called', 0)}",
            f"  times failed:   {history.get('times_failed', 0)}",
            f"  times ok:       {history.get('times_succeeded', 0)}",
            f"  avg duration:   {history.get('avg_duration_ms', 0):.1f}ms",
            f"  last 5 status:  {history.get('last_status', 'unknown')}",
        ])

    if downstream:
        lines.extend([
            "",
            "DOWNSTREAM COMPONENTS NEVER RAN (blocked by this failure):",
        ])
        for d in downstream[:8]:
            lines.append(
                f"  ⬜ {d['name']:30} "
                f"{d.get('file', '')}:{d.get('line', '')}"
            )

    if ok_spans:
        lines.extend([
            "",
            "WHAT RAN SUCCESSFULLY BEFORE THE FAILURE:",
        ])
        for s in ok_spans[:5]:
            ok_attrs = _parse_attrs(s)
            lines.append(
                f"  ✅ {s['name']:30} "
                f"{s.get('latencyMs', 0):.1f}ms  "
                f"{ok_attrs.get('vibe.file', '')}:"
                f"{ok_attrs.get('vibe.line', '')}"
            )

    return "\n".join(lines)


@mcp.tool()
async def suggest_fix():
    """
    Returns a targeted fix hint for the most recent failure.
    Derived purely from the error pattern in the trace data -
    no LLM, works offline, always fast.
    Pattern types and their fix hints:
    - SILENT_FAILURE: add post-condition guard
    - TIMEOUT: add timeout handling or retry
    - TYPE_ERROR: add input validation
    - KEY_ERROR: add key existence check
    - ATTRIBUTE_ERROR: add None check
    - IMPORT_ERROR: add dependency check
    - CONNECTION_ERROR: add connection error handling
    - EXCEPTION: add try/except with specific type
    """
    spans = _fetch_spans(limit=10)
    if not spans:
        return "No trace data available."

    error_span = next(
        (s for s in spans if s["statusCode"] == "ERROR"),
        None
    )
    if not error_span:
        return "No recent failures found."

    attrs = _parse_attrs(error_span)
    pattern = _detect_failure_pattern(error_span, attrs)
    name = error_span["name"]
    file_path = attrs.get("vibe.file", "unknown")
    line = attrs.get("vibe.line", "?")
    error_type = attrs.get("error.type", "")
    input_val = attrs.get(
        "vibe.input",
        attrs.get("vibe.input_preview", "")
    )
    output_val = attrs.get(
        "vibe.output",
        attrs.get("vibe.output_preview", "")
    )

    fix_hints = {
        "SILENT_FAILURE": f"""
PATTERN: Function returned empty value when input was non-empty.

LOCATION: {file_path}:{line}

FIX: Add a post-condition guard after the return value:
    result = {name}(...)
    if not result:
        raise ValueError(
            f"{name} returned empty for input: {{input}}"
        )

OR add an assertion inside {name}():
    assert result, f"Expected non-empty result, got: {{result}}"

WHY: The function silently returned {output_val} for input {input_val}.
     This is the root cause of downstream failures.""",

        "TIMEOUT": f"""
PATTERN: Function timed out - took {error_span.get('latencyMs', 0):.0f}ms.

LOCATION: {file_path}:{line}

FIX: Add timeout handling and a fallback:
    try:
        result = {name}(...)
    except (TimeoutError, ConnectionTimeout) as e:
        logger.warning(f"{name} timed out: {{e}}")
        result = fallback_value  # or raise with context

OR increase the timeout threshold in your client config.

WHY: The call at {file_path}:{line} did not complete in time.
     Check if the external service is slow or unreachable.""",

        "CONNECTION_ERROR": f"""
PATTERN: Connection failed - could not reach external service.

LOCATION: {file_path}:{line}

FIX: Add connection error handling:
    try:
        result = {name}(...)
    except (ConnectionError, ConnectTimeout, ConnectionRefused) as e:
        raise RuntimeError(
            f"Service unavailable in {name}: {{e}}"
        ) from e

WHY: {name} failed to connect at {file_path}:{line}.
     The service may be down or the URL/port may be wrong.""",

        "TYPE_ERROR": f"""
PATTERN: Wrong type passed to function.

LOCATION: {file_path}:{line}

FIX: Add input type validation at the start of {name}():
    if not isinstance(input_param, expected_type):
        raise TypeError(
            f"{name} expected {{expected_type}}, "
            f"got {{type(input_param)}}: {{input_param}}"
        )

WHY: Input was {input_val} - check the caller is passing
     the correct type.""",

        "KEY_ERROR": f"""
PATTERN: Dictionary key not found.

LOCATION: {file_path}:{line}

FIX: Use .get() with a default instead of direct key access:
    value = data.get("key", default_value)
    # or add existence check:
    if "key" not in data:
        raise KeyError(f"Required key missing from: {{data.keys()}}")

WHY: A key lookup failed at {file_path}:{line}.
     The input dict may be missing expected fields.""",

        "ATTRIBUTE_ERROR": f"""
PATTERN: Attribute access on None or wrong type.

LOCATION: {file_path}:{line}

FIX: Add None check before attribute access:
    if result is None:
        raise ValueError(
            f"{name} received None - check upstream function"
        )

WHY: Something upstream returned None when {name} expected
     an object with attributes.""",

        "EXCEPTION": f"""
PATTERN: Unhandled {error_type} exception.

LOCATION: {file_path}:{line}

FIX: Wrap the failing call in a specific exception handler:
    try:
        result = {name}(...)
    except {error_type or 'Exception'} as e:
        # log, fallback, or re-raise with context
        raise RuntimeError(
            f"{name} failed: {{e}}"
        ) from e

WHY: {error_type} was raised at {file_path}:{line}.
     Adding specific handling prevents silent propagation.""",
    }

    hint = fix_hints.get(pattern, fix_hints["EXCEPTION"])

    return (
        f"=== SUGGEST FIX ===\n"
        f"Component: {name}\n"
        f"Pattern:   {pattern}\n"
        f"{hint}"
    )


@mcp.tool()
async def trace_summary():
    """
    Returns a complete waterfall view of the most recent
    pipeline execution. Shows every component that ran with
    status, duration, file, line, and input/output preview.
    Gives the IDE assistant the full execution context
    in one call - no file reading needed.
    """
    spans = _fetch_spans(limit=20)
    if not spans:
        return "No trace data found."

    lines = [
        "=== PIPELINE EXECUTION WATERFALL ===",
        f"Showing {len(spans)} most recent spans",
        "",
    ]

    total_ms = 0
    error_count = 0
    ok_count = 0

    for span in reversed(spans):
        attrs = _parse_attrs(span)
        status = span.get("statusCode", "UNSET")
        name = span.get("name", "unknown")
        latency = span.get("latencyMs") or 0
        total_ms += latency
        file_path = attrs.get("vibe.file", "")
        line = attrs.get("vibe.line", "")
        vibe_status = attrs.get("vibe.status", status)

        if status == "ERROR":
            icon = "❌"
            error_count += 1
        elif vibe_status == "SILENT_FAILURE":
            icon = "⚠️"
            error_count += 1
        else:
            icon = "✅"
            ok_count += 1

        short_file = os.path.basename(str(file_path)) if file_path else ""

        lines.append(
            f"{icon} {name:35} "
            f"{latency:8.1f}ms  "
            f"{short_file}:{line}"
        )

        if status == "ERROR":
            inp = attrs.get(
                "vibe.input",
                attrs.get("vibe.input_preview", "")
            )
            out = attrs.get(
                "vibe.output",
                attrs.get("vibe.output_preview", "")
            )
            err = attrs.get("error.type", "")
            if inp:
                lines.append(f"   input:  {str(inp)[:120]}")
            if out:
                lines.append(f"   output: {str(out)[:120]}")
            if err:
                lines.append(f"   error:  {err}")

    lines.extend([
        "",
        "─" * 55,
        f"Total spans:  {len(spans)}",
        f"Succeeded:    {ok_count}",
        f"Failed:       {error_count}",
        f"Total time:   {total_ms:.1f}ms",
    ])

    return "\n".join(lines)


@mcp.tool()
async def get_component_history():
    """
    Returns observed runtime statistics for every component
    in the pipeline. Shows call counts, failure rates, average
    durations, and last known status. Lets the IDE assistant
    identify which components fail consistently vs intermittently,
    which are slow, and which have never been observed running.
    """
    if not os.path.exists(PIPELINE_CACHE):
        return (
            "No pipeline cache found.\n"
            "Run vibe_debug.start() in your project first."
        )

    try:
        with open(PIPELINE_CACHE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return f"Error reading pipeline cache: {e}"

    components = data.get("components", {})
    if not components:
        return "No component history recorded yet."

    failed = []
    healthy = []
    never_observed = []

    for name, c in components.items():
        if "test" in c.get("module", "") or \
           "vibe_debug" in c.get("module", ""):
            continue
        if c.get("times_called", 0) == 0:
            never_observed.append(c)
        elif c.get("times_failed", 0) > 0:
            failed.append(c)
        else:
            healthy.append(c)

    lines = [
        "=== COMPONENT HISTORY ===",
        f"Total components: {len(components)}",
        f"Traces recorded:  {data.get('total_traces', 0)}",
        f"Total crashes:    {data.get('total_crashes', 0)}",
        "",
    ]

    if failed:
        lines.append("⚠️  COMPONENTS WITH FAILURES:")
        lines.append(
            f"  {'NAME':30} {'CALLS':6} "
            f"{'FAILED':7} {'FAIL%':6} "
            f"{'AVG_MS':8} LAST_ERROR"
        )
        lines.append("  " + "─" * 80)
        for c in sorted(
            failed,
            key=lambda x: x.get("times_failed", 0),
            reverse=True
        ):
            calls = c.get("times_called", 0)
            fails = c.get("times_failed", 0)
            rate = (fails / calls * 100) if calls > 0 else 0
            avg = c.get("avg_duration_ms", 0)
            err = c.get("last_error", "")[:40]
            lines.append(
                f"  {c['name']:30} {calls:6} "
                f"{fails:7} {rate:5.0f}%  "
                f"{avg:7.1f}ms  {err}"
            )
        lines.append("")

    if healthy:
        lines.append("✅ HEALTHY COMPONENTS:")
        lines.append(
            f"  {'NAME':30} {'CALLS':6} {'AVG_MS':8} FILE"
        )
        lines.append("  " + "─" * 70)
        for c in sorted(
            healthy,
            key=lambda x: x.get("times_called", 0),
            reverse=True
        )[:10]:
            calls = c.get("times_called", 0)
            avg = c.get("avg_duration_ms", 0)
            short_file = os.path.basename(c.get("file", ""))
            lines.append(
                f"  {c['name']:30} {calls:6} "
                f"{avg:7.1f}ms  "
                f"{short_file}:{c.get('line', '')}"
            )
        lines.append("")

    if never_observed:
        lines.append(
            f"⬜ NEVER OBSERVED "
            f"({len(never_observed)} in code, never ran):"
        )
        for c in never_observed[:8]:
            short_file = os.path.basename(c.get("file", ""))
            lines.append(
                f"  {c['name']:30} "
                f"{short_file}:{c.get('line', '')}"
            )

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
