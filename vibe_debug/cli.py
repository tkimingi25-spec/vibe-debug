"""Command-line interface for operating vibe-debug support services."""

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import httpx

from server import manager
from vibe_debug.config import generate_config, load_config
from vibe_debug.runtime.static_analyzer import ProjectStaticAnalyzer

CACHE_FILENAME = ".vibe_pipeline_cache.json"


def _cache_path(project_root: Path) -> Path:
    return project_root / CACHE_FILENAME


def _load_cache(project_root: Path) -> dict:
    path = _cache_path(project_root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"[vibe-debug] Could not read {path.name}: {error}", file=sys.stderr)
        return {}


def _fetch_spans(limit=10) -> list:
    query = f"""
    {{ projects {{ edges {{ node {{
        spans(first: {limit}, sort: {{col: startTime, dir: desc}}) {{
            edges {{ node {{ name statusCode latencyMs startTime attributes }} }}
        }}
    }} }} }} }}
    """
    try:
        response = httpx.post(
            "http://localhost:6006/graphql", json={"query": query}, timeout=5.0
        )
        response.raise_for_status()
        projects = response.json()["data"]["projects"]["edges"]
        spans = []
        for project in projects:
            spans.extend(project["node"]["spans"]["edges"])
        return sorted(
            spans,
            key=lambda edge: edge["node"].get("startTime", ""),
            reverse=True,
        )[:limit]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return []


def _format_map(cache: dict) -> str:
    components = cache.get("components", {})
    lines = [
        f"PIPELINE MAP - {cache.get('project_name', 'unknown')}",
        f"Components: {len(components)} | Traces: {cache.get('total_traces', 0)} | "
        f"Crashes: {cache.get('total_crashes', 0)}",
    ]
    for entry_point in cache.get("entry_points", []):
        lines.append(f"ENTRY POINT: {entry_point}")
    for component in components.values():
        lines.append(
            f"{component.get('last_status', 'UNKNOWN'):7} "
            f"{component.get('name', 'unknown'):30} "
            f"calls={component.get('times_called', 0)} "
            f"failed={component.get('times_failed', 0)}"
        )
    return "\n".join(lines)


def command_init(args) -> int:
    analyzer = ProjectStaticAnalyzer(str(args.project_root))
    pipeline_map = analyzer.scan()
    output = args.project_root / "vibedebug.toml"
    generate_config(pipeline_map, str(args.project_root), output_path=str(output))
    return 0


def command_up(args) -> int:
    return 0 if manager.launch_server(project_root=str(args.project_root)) else 1


def command_down(args) -> int:
    return 0 if manager.stop_server(str(args.project_root)) else 1


def command_status(args) -> int:
    config_path = args.project_root / "vibedebug.toml"
    config = load_config(str(config_path))
    cache = _load_cache(args.project_root)
    print(f"Phoenix: {'healthy' if manager.is_phoenix_healthy() else 'unavailable'}")
    print(f"Project: {config.project_name}")
    print(f"Config: {'present' if config_path.exists() else 'using defaults'}")
    print(f"Cache components: {len(cache.get('components', {}))}")
    print(f"Cache traces: {cache.get('total_traces', 0)}")
    return 0


def command_map(args) -> int:
    cache = _load_cache(args.project_root)
    if not cache:
        print("No pipeline cache found. Run vibe-debug init or start your application.")
        return 1
    print(_format_map(cache))
    return 0


def command_failures(args) -> int:
    spans = [item["node"] for item in _fetch_spans() if item["node"].get("statusCode") == "ERROR"]
    if not spans:
        print("No recent error spans found.")
        return 0
    for span in spans:
        print(f"ERROR {span.get('name', 'unknown')} ({span.get('latencyMs', 0)}ms)")
    return 0


def command_explain(args) -> int:
    spans = [item["node"] for item in _fetch_spans(30)]
    error = next((span for span in spans if span.get("statusCode") == "ERROR"), None)
    if not error:
        print("No failures in recent traces. All spans completed OK or Phoenix is empty.")
        return 0
    print("=== FAILURE EXPLANATION ===")
    print(f"Component: {error.get('name', 'unknown')}")
    print(f"Status: {error.get('statusCode', 'unknown')}")
    print(f"Latency: {error.get('latencyMs', 0)}ms")
    print("Use vibe-debug failures for a concise list of recent errors.")
    return 0


def command_doctor(args) -> int:
    checks = {
        "Python >= 3.11": sys.version_info >= (3, 11),
        "vibedebug.toml parses": _config_is_valid(args.project_root / "vibedebug.toml"),
        "Phoenix reachable": manager.is_phoenix_healthy(),
        "Phoenix package installed": importlib.util.find_spec("phoenix") is not None,
    }
    for name, passed in checks.items():
        print(f"{'OK' if passed else 'WARN'}  {name}")
    return 0


def _config_is_valid(path: Path) -> bool:
    try:
        load_config(str(path))
        return True
    except Exception:
        return False


def command_mcp(args) -> int:
    os.environ["VIBE_PROJECT_ROOT"] = str(args.project_root)
    from server.mcp_bridge import mcp

    mcp.run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibe-debug")
    parser.add_argument(
        "--project-root", type=Path, default=Path.cwd(),
        help="project to inspect and whose cache/server state to use (default: cwd)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    handlers = {
        "init": command_init, "up": command_up, "status": command_status,
        "map": command_map, "failures": command_failures, "explain": command_explain,
        "doctor": command_doctor, "mcp": command_mcp, "down": command_down,
    }
    for name, handler in handlers.items():
        command = subparsers.add_parser(name)
        command.set_defaults(handler=handler)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.project_root = args.project_root.resolve()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
