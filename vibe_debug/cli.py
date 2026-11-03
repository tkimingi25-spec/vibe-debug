"""Command-line interface for operating vibe-debug support services."""

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from server import manager
from vibe_debug import phoenix_client
from vibe_debug.config import generate_config, load_config
from vibe_debug.explainer import build_evidence_package, explain_failure
from vibe_debug.phoenix_endpoint import make_client
from vibe_debug.tracer import (
    EXPORT_HEALTH_FILENAME,
    get_export_health,
    read_persisted_export_health,
)
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


def _local_config():
    """Load vibedebug.toml from the cwd if present (no output otherwise)."""
    try:
        candidate = Path.cwd() / "vibedebug.toml"
        if candidate.exists():
            return load_config(str(candidate))
    except Exception:
        pass
    return None


def _fetch_spans(limit=10, *, base_url=None, config=None, wait_seconds=0) -> list:
    """CLI span fetch: shared fetch, nodes re-wrapped as GraphQL edges."""
    nodes = phoenix_client.fetch_spans(
        limit,
        base_url=base_url,
        config=config if config is not None else _local_config(),
        timeout=5.0,
        include_context=False,
        client_factory=make_client,
        wait_seconds=wait_seconds,
    )
    return [{"node": node} for node in nodes]


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
    if getattr(args, "force", False):
        return 0 if manager.stop_server(str(args.project_root), force=True) else 1
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
    wait = getattr(args, "wait", 0) or 0
    fetched = _fetch_spans(wait_seconds=wait) if wait else _fetch_spans()
    spans = [item["node"] for item in fetched if item["node"].get("statusCode") == "ERROR"]
    if not spans:
        print("No recent error spans found.")
        return 0
    for span in spans:
        print(f"ERROR {span.get('name', 'unknown')} ({span.get('latencyMs', 0)}ms)")
    return 0


def command_explain(args) -> int:
    wait = getattr(args, "wait", 0) or 0
    fetched = _fetch_spans(30, wait_seconds=wait) if wait else _fetch_spans(30)
    spans = [item["node"] for item in fetched]
    error = next((span for span in spans if span.get("statusCode") == "ERROR"), None)
    if not error:
        print("No failures in recent traces. All spans completed OK or Phoenix is empty.")
        return 0
    deterministic = [
        "=== FAILURE EXPLANATION ===",
        f"Component: {error.get('name', 'unknown')}",
        f"Status: {error.get('statusCode', 'unknown')}",
        f"Latency: {error.get('latencyMs', 0)}ms",
        "Use vibe-debug failures for a concise list of recent errors.",
    ]
    for line in deterministic:
        print(line)

    language = getattr(args, "language", None)
    config = _local_config()
    enabled = bool(getattr(config, "explainer_enabled", False)) if config else False
    if enabled or language is not None:
        evidence = build_evidence_package(
            error,
            spans,
            max_evidence_size=getattr(config, "explainer_max_evidence_size", 8000),
            privacy_profile=getattr(config, "privacy_profile", "permissive") if config else "permissive",
        )
        result = explain_failure(
            evidence,
            language=language,
            config=config,
            fallback_text="\n".join(deterministic),
        )
        if result.get("error"):
            print(result["error"] + "; showing deterministic explanation.")
        elif result.get("source") == "llm":
            print(
                f"--- AI explanation (Muse Spark; language={result.get('language')}) ---"
            )
            print(result.get("text", ""))
    return 0


def _format_persisted_export_health(snapshot) -> str:
    age = snapshot.get("age_seconds")
    pid = snapshot.get("writer_pid")
    successes = snapshot.get("successes", 0)
    failures = snapshot.get("failures", 0)
    age_text = f"{int(age)}s ago" if age is not None else "unknown time"
    label = "stale snapshot" if snapshot.get("stale") else "recorded"
    if snapshot.get("status") == "ok":
        return (
            f"Telemetry export: OK (last success {age_text}, {label} by pid {pid}; "
            f"successes={successes}, failures={failures})"
        )
    if snapshot.get("status") == "failed":
        return (
            f"Telemetry export: FAILED (last error: {snapshot.get('last_error') or 'unknown error'}, "
            f"{label} by pid {pid}; successes={successes}, failures={failures})"
        )
    return "Telemetry export: no export attempted yet"


def _export_health_line(project_root=None) -> str:
    snapshot = get_export_health()
    if snapshot.get("initialized"):
        return _format_export_health(snapshot)
    if project_root is not None:
        persisted_path = Path(project_root) / EXPORT_HEALTH_FILENAME
    else:
        persisted_path = None
    persisted = read_persisted_export_health(persisted_path)
    if persisted:
        return _format_persisted_export_health(persisted)
    return _format_export_health(snapshot)


def _format_export_health(snapshot, now=None) -> str:
    """One diagnostics line from an export-health snapshot."""
    try:
        status = snapshot.get("status", "no_export_yet")
        successes = snapshot.get("successes", 0)
        failures = snapshot.get("failures", 0)
        if status == "ok":
            ts = snapshot.get("last_success_at")
            if ts is not None:
                ago = max(0, int((now if now is not None else time.time()) - ts))
                return (
                    f"Telemetry export: OK (last success {ago}s ago; "
                    f"successes={successes}, failures={failures})"
                )
            return (
                f"Telemetry export: OK (successes={successes}, "
                f"failures={failures})"
            )
        if status == "failed":
            error = snapshot.get("last_error") or "unknown error"
            return (
                f"Telemetry export: FAILED (last error: {error}; "
                f"successes={successes}, failures={failures})"
            )
        return "Telemetry export: no export attempted yet"
    except Exception:
        return "Telemetry export: no export attempted yet"


def _coverage_lines(project_root) -> list:
    """Local tracing-coverage diagnostics; never queries Phoenix.

    Doctor runs in its own process, so runtime states are reported as
    configured / in-this-process, not as claims about another process.
    """
    lines = ["Tracing coverage:"]
    try:
        config = None
        cfg_path = Path(project_root) / "vibedebug.toml"
        if cfg_path.exists():
            try:
                config = load_config(str(cfg_path))
            except Exception:
                config = None
        watched = list(getattr(config, "watch_modules", None) or [])
        lines.append(
            "  Watched/configured modules: "
            + (", ".join(watched) if watched else "none configured")
        )
        missing = []
        for module in watched:
            try:
                if importlib.util.find_spec(module) is None:
                    missing.append(module)
            except Exception:
                missing.append(module)
        lines.append(
            "  Configured modules not found: "
            + (", ".join(missing) if missing else "none")
        )
        lines.append(
            "  Auto-instrumentation: "
            + (
                f"configured for {len(watched)} module(s) (active only in a process that calls start()/auto_instrument_all)"
                if watched
                else "no watched modules configured; manual @vibetrace still works"
            )
        )
        try:
            from vibe_debug.tracer import current_provider

            provider_active = current_provider() is not None
        except Exception:
            provider_active = False
        lines.append(
            "  Universal runtime tracing: "
            + (
                "a tracer provider is active in this process"
                if provider_active
                else "not active in this process (doctor runs separately; enable via start(universal_trace=True) in the app process)"
            )
        )
        cache = _load_cache(Path(project_root))
        observed = sorted((cache.get("components") or {}).keys())
        lines.append(
            "  Recently observed traced functions (pipeline cache): "
            + (", ".join(observed[:20]) if observed else "none recorded")
        )
        lines.append(
            "  Coverage source: local config/cache only; Phoenix reachability is reported separately above."
        )
    except Exception:
        lines.append("  Coverage unavailable in this process.")
    return lines


def command_doctor(args) -> int:
    checks = {
        "Python >= 3.11": sys.version_info >= (3, 11),
        "vibedebug.toml parses": _config_is_valid(args.project_root / "vibedebug.toml"),
        "Phoenix reachable": manager.is_phoenix_healthy(),
        "Phoenix package installed": importlib.util.find_spec("phoenix") is not None,
    }
    for name, passed in checks.items():
        print(f"{'OK' if passed else 'WARN'}  {name}")
    try:
        print(_export_health_line(args.project_root))
    except Exception:
        print("Telemetry export: no export attempted yet")
    try:
        for line in _coverage_lines(args.project_root):
            print(line)
    except Exception:
        print("Tracing coverage:\n  Coverage unavailable in this process.")
    # Stale/unverifiable Phoenix PID guidance (Improvement 8 + recovery).
    try:
        from server import manager as _manager

        pid_file = _manager._pid_path(args.project_root)
        if pid_file.exists():
            record = _manager._read_pid_file(pid_file)
            identity = _manager._verify_process_identity(record) if record.get("pid") else "unavailable"
            if identity == "mismatch":
                print(
                    "WARN  Stale Phoenix PID file (PID appears reused): run "
                    "'vibe-debug down' to remove it; the process will not be signalled."
                )
            elif identity == "unavailable":
                print(
                    "WARN  Stale/unverifiable Phoenix PID file: run "
                    "'vibe-debug down --force' only if you are sure that PID is Phoenix."
                )
    except Exception:
        pass
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
        if name == "explain":
            command.add_argument(
                "--language", default=None,
                help="AI explanation language: en or sw (default: config or en)",
            )
        if name in ("explain", "failures"):
            command.add_argument(
                "--wait", type=float, default=0,
                help="seconds to poll for fresh spans (ingestion lag); 0 = no wait",
            )
        if name == "down":
            command.add_argument(
                "--force", action="store_true",
                help="signal the recorded Phoenix PID even if its identity cannot be verified (dangerous)",
            )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    args.project_root = args.project_root.resolve()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
