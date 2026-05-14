# tests/test_system_health.py
"""
Full system health check for vibe-debug.
Run this to diagnose connection issues between all components.

Usage:
    python tests/test_system_health.py
"""

import sys
import os
import json
import socket
import time

# Allow running from either root
VIBE_ROOT = r"C:\Users\hp\Desktop\vibe-debug"
IKR_ROOT = r"C:\Users\hp\Desktop\testikr"
PHOENIX_URL = "http://localhost:6006"
PHOENIX_GRAPHQL = "http://localhost:6006/graphql"
PIPELINE_CACHE_IKR = os.path.join(
    IKR_ROOT, ".vibe_pipeline_cache.json"
)
PIPELINE_CACHE_VIBE = os.path.join(
    VIBE_ROOT, ".vibe_pipeline_cache.json"
)

results = []

def check(name: str, passed: bool, detail: str = ""):
    icon = "✅" if passed else "❌"
    line = f"{icon} {name}"
    if detail:
        line += f"\n   {detail}"
    print(line)
    results.append((name, passed, detail))
    return passed

def section(title: str):
    print(f"\n{'='*55}")
    print(f"  {title}")
    print(f"{'='*55}")

# ─────────────────────────────────────────────────────────
section("1. PYTHON ENVIRONMENT")
# ─────────────────────────────────────────────────────────

check(
    "Python version >= 3.11",
    sys.version_info >= (3, 11),
    f"Found: {sys.version}"
)

check(
    "vibe-debug root exists",
    os.path.isdir(VIBE_ROOT),
    VIBE_ROOT
)

check(
    "IKR root exists",
    os.path.isdir(IKR_ROOT),
    IKR_ROOT
)

# ─────────────────────────────────────────────────────────
section("2. VIBE-DEBUG PACKAGE IMPORTS")
# ─────────────────────────────────────────────────────────

if VIBE_ROOT not in sys.path:
    sys.path.insert(0, VIBE_ROOT)

try:
    from vibe_debug.tracer import init_vibedebug
    check("vibe_debug.tracer imports", True)
except Exception as e:
    check("vibe_debug.tracer imports", False, str(e))

try:
    from vibe_debug.decorators import vibetrace
    check("vibe_debug.decorators imports", True)
except Exception as e:
    check("vibe_debug.decorators imports", False, str(e))

try:
    from vibe_debug.runtime.static_analyzer import ProjectStaticAnalyzer
    check("vibe_debug.runtime.static_analyzer imports", True)
except Exception as e:
    check("vibe_debug.runtime.static_analyzer imports", False, str(e))

try:
    from vibe_debug.runtime.io_interceptor import IOInterceptor
    check("vibe_debug.runtime.io_interceptor imports", True)
except Exception as e:
    check("vibe_debug.runtime.io_interceptor imports", False, str(e))

try:
    from vibe_debug.runtime.partial_trace_builder import PartialTraceBuilder
    check("vibe_debug.runtime.partial_trace_builder imports", True)
except Exception as e:
    check("vibe_debug.runtime.partial_trace_builder imports", False, str(e))

try:
    from vibe_debug.runtime.pipeline_discovery import PipelineDiscovery
    check("vibe_debug.runtime.pipeline_discovery imports", True)
except Exception as e:
    check("vibe_debug.runtime.pipeline_discovery imports", False, str(e))

# ─────────────────────────────────────────────────────────
section("3. REQUIRED PACKAGES")
# ─────────────────────────────────────────────────────────

packages = [
    ("opentelemetry.sdk", "opentelemetry-sdk"),
    ("opentelemetry.exporter.otlp.proto.http", "opentelemetry-exporter-otlp"),
    ("mcp", "mcp"),
    ("httpx", "httpx"),
    ("wrapt", "wrapt"),
    ("phoenix", "arize-phoenix"),
    ("dotenv", "python-dotenv"),
]

for module, pip_name in packages:
    try:
        __import__(module)
        check(f"Package: {pip_name}", True)
    except ImportError:
        check(
            f"Package: {pip_name}",
            False,
            f"Install with: pip install {pip_name}"
        )

# ─────────────────────────────────────────────────────────
section("4. PHOENIX CONNECTIVITY")
# ─────────────────────────────────────────────────────────

try:
    import httpx
    r = httpx.get(f"{PHOENIX_URL}/healthz", timeout=5.0)
    check(
        "Phoenix health endpoint",
        r.status_code == 200,
        f"Status: {r.status_code}"
    )
except Exception as e:
    check(
        "Phoenix health endpoint",
        False,
        f"Phoenix not running. Start with: "
        f"python -m phoenix.server.main serve\nError: {e}"
    )

try:
    import httpx
    r = httpx.post(
        PHOENIX_GRAPHQL,
        json={"query": "{ projects { edges { node { name } } } }"},
        timeout=5.0,
    )
    data = r.json()
    projects = data.get("data", {}).get("projects", {}).get("edges", [])
    check(
        "Phoenix GraphQL responds",
        len(projects) >= 0,
        f"Projects found: {[p['node']['name'] for p in projects]}"
    )
except Exception as e:
    check("Phoenix GraphQL responds", False, str(e))

try:
    import httpx
    r = httpx.post(
        PHOENIX_GRAPHQL,
        json={"query": """
        { projects { edges { node {
            spans(first: 5, sort: {col: startTime, dir: desc}) {
                edges { node { name statusCode } }
            }
        } } } }
        """},
        timeout=5.0,
    )
    spans = (
        r.json()["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )
    check(
        "Phoenix spans queryable",
        True,
        f"Recent spans found: {len(spans)}"
    )
except Exception as e:
    check("Phoenix spans queryable", False, str(e))

# ─────────────────────────────────────────────────────────
section("5. SPAN PIPELINE — END TO END")
# ─────────────────────────────────────────────────────────

try:
    from vibe_debug.tracer import init_vibedebug
    from vibe_debug.decorators import vibetrace
    import httpx as _httpx

    tracer = init_vibedebug("health-check")

    @vibetrace(component_type="health")
    def health_check_func():
        """Health check test function."""
        return {"status": "ok"}

    result = health_check_func()
    check(
        "Span creation works",
        result["status"] == "ok",
        "Decorated function executed"
    )

    # Wait briefly then verify span arrived
    time.sleep(1)
    r = _httpx.post(
        PHOENIX_GRAPHQL,
        json={"query": """
        { projects { edges { node {
            spans(first: 5, sort: {col: startTime, dir: desc}) {
                edges { node { name statusCode } }
            }
        } } } }
        """},
        timeout=5.0,
    )
    recent = r.json()["data"]["projects"]["edges"][0]["node"]["spans"]["edges"]
    names = [e["node"]["name"] for e in recent]
    check(
        "Span reached Phoenix",
        "health_check_func" in names,
        f"Recent span names: {names[:3]}"
    )
except Exception as e:
    check("Span pipeline end to end", False, str(e))

# ─────────────────────────────────────────────────────────
section("6. MCP BRIDGE FILE")
# ─────────────────────────────────────────────────────────

mcp_bridge_path = os.path.join(VIBE_ROOT, "server", "mcp_bridge.py")
check(
    "mcp_bridge.py exists",
    os.path.isfile(mcp_bridge_path),
    mcp_bridge_path
)

try:
    with open(mcp_bridge_path) as f:
        content = f.read()
    tools = [
        "get_last_failure",
        "why_did_this_fail",
        "suggest_fix",
        "trace_summary",
        "get_component_history",
        "get_pipeline_map",
        "explain_trace",
    ]
    missing = [t for t in tools if t not in content]
    check(
        "All MCP tools present in bridge",
        len(missing) == 0,
        f"Missing tools: {missing}" if missing else "All 7 tools found"
    )
except Exception as e:
    check("MCP bridge readable", False, str(e))

try:
    import subprocess
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, r'{VIBE_ROOT}'); "
         f"import server.mcp_bridge; print('OK')"],
        capture_output=True, text=True, timeout=10,
        cwd=VIBE_ROOT
    )
    check(
        "MCP bridge imports without error",
        "OK" in result.stdout,
        result.stderr[:200] if result.stderr else "Clean import"
    )
except Exception as e:
    check("MCP bridge imports without error", False, str(e))

# ─────────────────────────────────────────────────────────
section("7. PIPELINE CACHE")
# ─────────────────────────────────────────────────────────

check(
    "Pipeline cache exists in IKR root",
    os.path.isfile(PIPELINE_CACHE_IKR),
    PIPELINE_CACHE_IKR if os.path.isfile(PIPELINE_CACHE_IKR)
    else "Not found — run vibe_debug.start() in IKR first"
)

check(
    "Pipeline cache exists in vibe-debug root",
    os.path.isfile(PIPELINE_CACHE_VIBE),
    PIPELINE_CACHE_VIBE if os.path.isfile(PIPELINE_CACHE_VIBE)
    else "Not found here"
)

if os.path.isfile(PIPELINE_CACHE_IKR):
    try:
        with open(PIPELINE_CACHE_IKR) as f:
            cache = json.load(f)
        components = len(cache.get("components", {}))
        traces = cache.get("total_traces", 0)
        check(
            "IKR pipeline cache readable",
            components > 0,
            f"Components: {components}, Traces recorded: {traces}"
        )
    except Exception as e:
        check("IKR pipeline cache readable", False, str(e))

# ─────────────────────────────────────────────────────────
section("8. IKR PROJECT STRUCTURE")
# ─────────────────────────────────────────────────────────

ikr_entry = os.path.join(IKR_ROOT, "src", "examples", "unified_server.py")
check(
    "IKR unified_server.py exists",
    os.path.isfile(ikr_entry),
    ikr_entry
)

if os.path.isfile(ikr_entry):
    with open(ikr_entry) as f:
        content = f.read()
    check(
        "vibe_debug.start() in unified_server.py",
        "vibe_debug" in content and "start" in content,
        "vibe-debug injection found" if "vibe_debug" in content
        else "vibe_debug not found — add import to top of file"
    )

ikr_config = os.path.join(IKR_ROOT, "vibedebug.toml")
check(
    "vibedebug.toml exists in IKR root",
    os.path.isfile(ikr_config),
    ikr_config if os.path.isfile(ikr_config)
    else "Not found — create vibedebug.toml in testikr root"
)

# ─────────────────────────────────────────────────────────
section("9. PORT AVAILABILITY")
# ─────────────────────────────────────────────────────────

ports_to_check = [
    (6006, "Phoenix UI"),
    (4317, "Phoenix gRPC"),
    (8000, "IKR server"),
    (7687, "Neo4j"),
    (27017, "MongoDB"),
    (11434, "Ollama"),
]

for port, name in ports_to_check:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("localhost", port))
        s.close()
        if result == 0:
            check(f"Port {port} ({name})", True, "Listening")
        else:
            check(
                f"Port {port} ({name})",
                False,
                f"Nothing listening — {name} may be down"
            )
    except Exception as e:
        check(f"Port {port} ({name})", False, str(e))

# ─────────────────────────────────────────────────────────
section("SUMMARY")
# ─────────────────────────────────────────────────────────

passed = sum(1 for _, p, _ in results if p)
failed = sum(1 for _, p, _ in results if not p)
total = len(results)

print(f"\n  Total checks: {total}")
print(f"  Passed:       {passed}")
print(f"  Failed:       {failed}")

if failed > 0:
    print("\n  Failed checks:")
    for name, passed, detail in results:
        if not passed:
            print(f"    ❌ {name}")
            if detail:
                print(f"       {detail}")

if failed == 0:
    print("\n  ✅ All systems operational")
elif failed <= 3:
    print("\n  ⚠️  Minor issues — check failed items above")
else:
    print("\n  🚨 Multiple failures — review output above")

print()
