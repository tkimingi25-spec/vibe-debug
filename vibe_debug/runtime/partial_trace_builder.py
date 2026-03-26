import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


class ComponentStatus(Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NEVER_REACHED = "NEVER_REACHED"
    RUNNING = "RUNNING"


@dataclass
class ComponentTrace:
    name: str
    module: str
    file: str
    line: int
    status: ComponentStatus
    input_preview: str = ""
    output_preview: str = ""
    error_type: str = ""
    error_message: str = ""
    duration_ms: float = 0.0
    blocked_by: str = ""


@dataclass
class PartialTrace:
    trace_id: str
    project_name: str
    started_at: float
    completed: bool = False
    crash_point: Optional[str] = None
    components: Dict[str, ComponentTrace] = field(default_factory=dict)

    def is_partial(self) -> bool:
        return not self.completed or self.crash_point is not None

    def get_never_reached(self) -> List[ComponentTrace]:
        return [
            c for c in self.components.values()
            if c.status == ComponentStatus.NEVER_REACHED
        ]

    def get_failed(self) -> List[ComponentTrace]:
        return [
            c for c in self.components.values()
            if c.status == ComponentStatus.FAILED
        ]

    def get_completed(self) -> List[ComponentTrace]:
        return [
            c for c in self.components.values()
            if c.status == ComponentStatus.COMPLETED
        ]


class PartialTraceBuilder:
    """
    Assembles incomplete traces by merging:
    1. Runtime observations (what actually ran)
    2. Static pipeline map (what should have run)

    Never discards crashed or incomplete traces.
    Marks components as NEVER_REACHED when crash blocks them.
    """

    def __init__(self, project_name: str = "vibe-debug"):
        self.project_name = project_name
        self.active_trace: Optional[PartialTrace] = None
        self.completed_traces: List[PartialTrace] = []
        self.static_map: Dict = {}
        self.import_graph: Dict = {}
        self._tracer = trace.get_tracer(__name__)

    def load_static_map(self, pipeline_map_dict: dict):
        """
        Load the static pipeline map from the analyzer.
        Used to fill in components that never ran due to crash.
        """
        self.static_map = pipeline_map_dict.get("functions", {})
        print(
            f"[vibe-debug] Partial trace builder loaded "
            f"{len(self.static_map)} functions from static map"
        )

    def load_import_graph(self, import_graph_dict: dict):
        """
        Load the import dependency graph from ImportMapper.
        Used to accurately identify which components are NEVER_REACHED
        based on real import dependencies - not just everything in
        the static map.
        """
        self.import_graph = import_graph_dict
        print(
            f"[vibe-debug] Import graph loaded: "
            f"{len(import_graph_dict)} modules mapped"
        )

    def start_trace(self, trace_id: str = None) -> PartialTrace:
        """Begin tracking a new query through the pipeline."""
        tid = trace_id or f"trace_{int(time.time() * 1000)}"
        self.active_trace = PartialTrace(
            trace_id=tid,
            project_name=self.project_name,
            started_at=time.time(),
        )
        return self.active_trace

    def record_start(self, component_name: str, module: str,
                    file: str, line: int, input_preview: str = ""):
        """Record that a component started executing."""
        if not self.active_trace:
            self.start_trace()

        self.active_trace.components[component_name] = ComponentTrace(
            name=component_name,
            module=module,
            file=file,
            line=line,
            status=ComponentStatus.RUNNING,
            input_preview=input_preview,
        )

    def record_success(self, component_name: str,
                      output_preview: str = "",
                      duration_ms: float = 0.0):
        """Record that a component completed successfully."""
        if not self.active_trace:
            return
        if component_name in self.active_trace.components:
            comp = self.active_trace.components[component_name]
            comp.status = ComponentStatus.COMPLETED
            comp.output_preview = output_preview
            comp.duration_ms = duration_ms

    def record_failure(self, component_name: str,
                      error_type: str, error_message: str,
                      duration_ms: float = 0.0):
        """
        Record that a component crashed.
        Automatically marks all downstream components as NEVER_REACHED
        using the static map to identify what depended on this component.
        """
        if not self.active_trace:
            return

        if component_name in self.active_trace.components:
            comp = self.active_trace.components[component_name]
            comp.status = ComponentStatus.FAILED
            comp.error_type = error_type
            comp.error_message = error_message
            comp.duration_ms = duration_ms

        self.active_trace.crash_point = component_name

        self._mark_downstream_unreached(component_name)

    def _mark_downstream_unreached(self, crash_component: str):
        """
        Use import graph when available for accurate downstream detection.
        Falls back to static map if import graph not loaded.
        """
        if not self.active_trace:
            return

        crash_module = None
        if crash_component in self.active_trace.components:
            crash_module = self.active_trace.components[
                crash_component
            ].module

        if self.import_graph and crash_module:
            affected_modules = set()
            if crash_module in self.import_graph:
                affected_modules.update(
                    self.import_graph[crash_module].get("imported_by", [])
                )

            for func_key, func_info in self.static_map.items():
                func_module = func_info.get("module", "")
                func_name = func_info["name"]

                if func_name in self.active_trace.components:
                    continue

                if func_module in affected_modules:
                    self.active_trace.components[func_name] = ComponentTrace(
                        name=func_name,
                        module=func_module,
                        file=func_info.get("file", "unknown"),
                        line=func_info.get("line", 0),
                        status=ComponentStatus.NEVER_REACHED,
                        blocked_by=crash_component,
                    )
        else:
            pipeline_keywords = {
                "route", "retriev", "generat", "lookup",
                "extract", "agent", "process", "handle",
                "ingest", "embed", "search", "query", "fetch"
            }
            for func_key, func_info in self.static_map.items():
                func_name = func_info["name"]
                if func_name in self.active_trace.components:
                    continue
                if any(kw in func_name.lower() for kw in pipeline_keywords):
                    self.active_trace.components[func_name] = ComponentTrace(
                        name=func_name,
                        module=func_info.get("module", "unknown"),
                        file=func_info.get("file", "unknown"),
                        line=func_info.get("line", 0),
                        status=ComponentStatus.NEVER_REACHED,
                        blocked_by=crash_component,
                    )

    def finish_trace(self, success: bool = True):
        """
        Close the active trace. If incomplete, run partial assembly
        to fill in any components that static analysis knows about
        but never ran.
        """
        if not self.active_trace:
            return None

        self.active_trace.completed = success

        if success and self.static_map:
            for func_key, func_info in self.static_map.items():
                func_name = func_info["name"]
                if func_name not in self.active_trace.components:
                    self.active_trace.components[func_name] = ComponentTrace(
                        name=func_name,
                        module=func_info.get("module", "unknown"),
                        file=func_info.get("file", "unknown"),
                        line=func_info.get("line", 0),
                        status=ComponentStatus.NEVER_REACHED,
                        blocked_by="not_called_in_this_trace",
                    )

        current_trace = self.active_trace
        self.completed_traces.append(current_trace)
        self.active_trace = None
        return current_trace

    def get_last_trace_report(self) -> str:
        """
        Generate a human-readable report of the most recent trace.
        This is what the MCP bridge will surface to the IDE.
        """
        if not self.completed_traces:
            return "No traces recorded yet."

        t = self.completed_traces[-1]
        lines = [
            f"{'PARTIAL TRACE - CRASH DETECTED' if t.is_partial() else 'COMPLETE TRACE'}",
            f"Trace ID: {t.trace_id}",
            f"Project:  {t.project_name}",
            "",
        ]

        if t.crash_point:
            lines.append(f"CRASH POINT: {t.crash_point}")
            lines.append("")

        completed = t.get_completed()
        if completed:
            lines.append("COMPLETED:")
            for c in completed:
                lines.append(
                    f"   {c.name:30} {c.duration_ms:.1f}ms"
                    f"  {c.file}:{c.line}"
                )
            lines.append("")

        failed = t.get_failed()
        if failed:
            lines.append("FAILED:")
            for c in failed:
                lines.append(f"   {c.name:30} {c.file}:{c.line}")
                lines.append(f"   Error: {c.error_type}: {c.error_message}")
            lines.append("")

        unreached = t.get_never_reached()
        pipeline_unreached = [
            c for c in unreached
            if "test" not in c.module
            and "vibe_debug" not in c.module
            and c.blocked_by != "not_called_in_this_trace"
        ]
        if pipeline_unreached:
            lines.append(
                f"NEVER REACHED ({len(pipeline_unreached)} components "
                f"blocked by crash):"
            )
            for c in pipeline_unreached[:10]:
                lines.append(
                    f"   {c.name:30} {c.file}:{c.line}"
                    f"  [blocked by: {c.blocked_by}]"
                )

        return "\n".join(lines)
