import json
import os
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from vibe_debug.runtime.import_mapper import ImportGraph, ImportMapper
from vibe_debug.runtime.partial_trace_builder import (
    ComponentStatus, PartialTraceBuilder
)
from vibe_debug.runtime.static_analyzer import (
    ProjectStaticAnalyzer, StaticPipelineMap
)


@dataclass
class PipelineComponent:
    name: str
    module: str
    file: str
    line: int
    calls: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    docstring: str = ""
    times_called: int = 0
    times_failed: int = 0
    times_succeeded: int = 0
    avg_duration_ms: float = 0.0
    last_status: str = "UNKNOWN"
    last_error: str = ""


@dataclass
class PipelineMap:
    project_name: str
    project_root: str
    components: Dict[str, PipelineComponent] = field(default_factory=dict)
    entry_points: List[str] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    total_traces: int = 0
    total_crashes: int = 0

    def get_crash_prone(self) -> List[PipelineComponent]:
        """Components that have failed at least once."""
        return [
            c for c in self.components.values()
            if c.times_failed > 0
        ]

    def get_never_observed(self) -> List[PipelineComponent]:
        """Components known from static analysis but never run."""
        return [
            c for c in self.components.values()
            if c.times_called == 0
        ]

    def get_critical_path(self) -> List[str]:
        """
        Components that appear in most traces - likely the main pipeline.
        """
        if not self.components:
            return []
        max_calls = max(
            c.times_called for c in self.components.values()
        )
        if max_calls == 0:
            return list(self.components.keys())
        threshold = max_calls * 0.5
        return [
            name for name, c in self.components.items()
            if c.times_called >= threshold
        ]


class PipelineDiscovery:
    """
    Merges all intelligence sources into one unified pipeline map.
    Updates incrementally with every run or crash.
    Persists the map to disk so it survives restarts.
    """

    CACHE_FILENAME = ".vibe_pipeline_cache.json"

    def __init__(
        self,
        project_root: str,
        project_name: str = "vibe-debug"
    ):
        self.project_root = os.path.abspath(project_root)
        self.project_name = project_name
        self.pipeline_map = PipelineMap(
            project_name=project_name,
            project_root=self.project_root,
        )
        self._cache_path = os.path.join(
            self.project_root, self.CACHE_FILENAME
        )

    def initialize(
        self,
        static_map: StaticPipelineMap,
        import_graph: ImportGraph,
    ):
        """
        Build initial pipeline map from static analysis and
        import graph - before any runtime observations.
        """
        print("[vibe-debug] Building unified pipeline map...")

        self._load_cache()

        for func_key, func_info in static_map.functions.items():
            name = func_info.name
            if name not in self.pipeline_map.components:
                self.pipeline_map.components[name] = PipelineComponent(
                    name=name,
                    module=func_info.module,
                    file=func_info.file,
                    line=func_info.line,
                    calls=func_info.calls,
                    docstring=func_info.docstring,
                )
            else:
                comp = self.pipeline_map.components[name]
                comp.file = func_info.file
                comp.line = func_info.line
                comp.calls = func_info.calls

        for mod_name, dep in import_graph.modules.items():
            for comp_name, comp in self.pipeline_map.components.items():
                if comp.module == mod_name:
                    comp.imports = dep.imports
                    comp.imported_by = dep.imported_by

        self.pipeline_map.entry_points = static_map.entry_points

        print(
            f"[vibe-debug] Pipeline map initialized: "
            f"{len(self.pipeline_map.components)} components"
        )
        self._save_cache()
        return self.pipeline_map

    def record_trace(self, partial_trace):
        """
        Update pipeline map with observations from a completed trace.
        Called after every run - successful or crashed.
        """
        self.pipeline_map.total_traces += 1

        if partial_trace.crash_point:
            self.pipeline_map.total_crashes += 1

        for comp_name, comp_trace in partial_trace.components.items():
            if comp_name not in self.pipeline_map.components:
                self.pipeline_map.components[comp_name] = PipelineComponent(
                    name=comp_name,
                    module=comp_trace.module,
                    file=comp_trace.file,
                    line=comp_trace.line,
                )

            comp = self.pipeline_map.components[comp_name]
            comp.times_called += 1

            if comp_trace.status == ComponentStatus.COMPLETED:
                comp.times_succeeded += 1
                comp.last_status = "OK"
                if comp_trace.duration_ms > 0:
                    comp.avg_duration_ms = (
                        (comp.avg_duration_ms * (comp.times_called - 1)
                         + comp_trace.duration_ms)
                        / comp.times_called
                    )

            elif comp_trace.status == ComponentStatus.FAILED:
                comp.times_failed += 1
                comp.last_status = "ERROR"
                comp.last_error = (
                    f"{comp_trace.error_type}: {comp_trace.error_message}"
                )

        self._save_cache()

    def get_pipeline_report(self) -> str:
        """
        Generate a complete human-readable pipeline map.
        This is what get_pipeline_map() MCP tool returns.
        """
        m = self.pipeline_map
        lines = [
            f"PIPELINE MAP - {m.project_name}",
            f"Components: {len(m.components)} | "
            f"Traces: {m.total_traces} | "
            f"Crashes: {m.total_crashes}",
            "",
        ]

        if m.entry_points:
            lines.append("ENTRY POINTS:")
            for ep in m.entry_points:
                lines.append(f"   {ep}")
            lines.append("")

        critical = m.get_critical_path()
        if critical:
            lines.append("CRITICAL PATH (most-used components):")
            for name in critical[:8]:
                comp = m.components[name]
                lines.append(
                    f"   {name:30} "
                    f"called={comp.times_called} "
                    f"failed={comp.times_failed}"
                )
            lines.append("")

        crash_prone = m.get_crash_prone()
        if crash_prone:
            lines.append("CRASH-PRONE COMPONENTS:")
            for comp in crash_prone:
                fail_rate = (
                    comp.times_failed / comp.times_called * 100
                    if comp.times_called > 0 else 0
                )
                lines.append(
                    f"   {comp.name:30} "
                    f"fail_rate={fail_rate:.0f}% "
                    f"last_error={comp.last_error[:60]}"
                )
            lines.append("")

        never_observed = m.get_never_observed()
        pipeline_never_observed = [
            c for c in never_observed
            if "test" not in c.module
            and "vibe_debug" not in c.module
        ]
        if pipeline_never_observed:
            lines.append(
                f"NEVER OBSERVED "
                f"({len(pipeline_never_observed)} components "
                f"found in code but never ran):"
            )
            for comp in pipeline_never_observed[:8]:
                lines.append(
                    f"   {comp.name:30} "
                    f"{comp.file}:{comp.line}"
                )

        return "\n".join(lines)

    def _save_cache(self):
        """Persist pipeline map to disk."""
        try:
            data = {
                "project_name": self.pipeline_map.project_name,
                "project_root": self.pipeline_map.project_root,
                "total_traces": self.pipeline_map.total_traces,
                "total_crashes": self.pipeline_map.total_crashes,
                "entry_points": self.pipeline_map.entry_points,
                "components": {
                    name: {
                        "name": c.name,
                        "module": c.module,
                        "file": c.file,
                        "line": c.line,
                        "calls": c.calls,
                        "imports": c.imports,
                        "imported_by": c.imported_by,
                        "docstring": c.docstring,
                        "times_called": c.times_called,
                        "times_failed": c.times_failed,
                        "times_succeeded": c.times_succeeded,
                        "avg_duration_ms": c.avg_duration_ms,
                        "last_status": c.last_status,
                        "last_error": c.last_error,
                    }
                    for name, c in self.pipeline_map.components.items()
                }
            }
            with open(self._cache_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[vibe-debug] Cache save failed: {e}")

    def _load_cache(self):
        """Load persisted pipeline map from disk if it exists."""
        if not os.path.exists(self._cache_path):
            return
        try:
            with open(self._cache_path) as f:
                data = json.load(f)
            self.pipeline_map.total_traces = data.get("total_traces", 0)
            self.pipeline_map.total_crashes = data.get("total_crashes", 0)
            self.pipeline_map.entry_points = data.get("entry_points", [])
            for name, c in data.get("components", {}).items():
                self.pipeline_map.components[name] = PipelineComponent(
                    name=c["name"],
                    module=c.get("module", ""),
                    file=c.get("file", ""),
                    line=c.get("line", 0),
                    calls=c.get("calls", []),
                    imports=c.get("imports", []),
                    imported_by=c.get("imported_by", []),
                    docstring=c.get("docstring", ""),
                    times_called=c.get("times_called", 0),
                    times_failed=c.get("times_failed", 0),
                    times_succeeded=c.get("times_succeeded", 0),
                    avg_duration_ms=c.get("avg_duration_ms", 0.0),
                    last_status=c.get("last_status", "UNKNOWN"),
                    last_error=c.get("last_error", ""),
                )
            print(
                f"[vibe-debug] Pipeline cache loaded: "
                f"{len(self.pipeline_map.components)} components, "
                f"{self.pipeline_map.total_traces} previous traces"
            )
        except Exception as e:
            print(f"[vibe-debug] Cache load failed: {e}")
