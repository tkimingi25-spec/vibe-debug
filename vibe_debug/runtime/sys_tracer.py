import os
import sys
import threading
import time
from typing import Optional, Set

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


class RelevanceFilter:
    """
    Determines which function calls are worth tracing.
    Separates project code from library internals and noise.
    """

    SKIP_PATH_FRAGMENTS = {
        "site-packages",
        "lib\\python",
        "lib/python",
        "\\lib\\",
        "/lib/",
        "__pycache__",
        "importlib",
        "encodings",
        "_pytest",
        "pytest",
        "pluggy",
        "vibe_debug",
    }

    SKIP_FUNCTION_NAMES = {
        "__init__", "__repr__", "__str__", "__len__",
        "__getattr__", "__setattr__", "__delattr__",
        "__enter__", "__exit__", "__iter__", "__next__",
        "__hash__", "__eq__", "__ne__", "__lt__", "__gt__",
        "<module>", "<genexpr>", "<listcomp>", "<dictcomp>",
        "<lambda>", "<setcomp>",
    }

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)

    def is_relevant(self, filename: str, function_name: str) -> bool:
        """
        Return True if this function call should be traced.
        Fast path: check path fragments first, then function names.
        """
        if not filename:
            return False

        abs_file = os.path.abspath(filename)
        if not abs_file.startswith(self.project_root):
            return False

        for fragment in self.SKIP_PATH_FRAGMENTS:
            if fragment in filename:
                return False

        if function_name in self.SKIP_FUNCTION_NAMES:
            return False

        if function_name.startswith("__") and function_name.endswith("__"):
            return False

        return True


class UniversalRuntimeTracer:
    """
    Universal runtime tracer using sys.settrace().
    Captures every function call in the project automatically.
    No manual decoration needed. Works for any Python project.

    Uses thread-local state so concurrent calls don't interfere.
    Minimal overhead - fast path skips irrelevant calls immediately.
    """

    def __init__(
        self,
        project_root: str,
        project_name: str = "vibe-debug",
        min_duration_ms: float = 0.0,
    ):
        self.project_root = os.path.abspath(project_root)
        self.project_name = project_name
        self.min_duration_ms = min_duration_ms
        self.filter = RelevanceFilter(project_root)
        self._tracer = trace.get_tracer("vibe_debug.runtime.sys_tracer")
        self._active = False
        self._local = threading.local()
        self._call_count = 0
        self._span_count = 0

    def start(self):
        """Activate universal tracing."""
        if self._active:
            return
        self._active = True
        sys.settrace(self._trace_dispatch)
        threading.settrace(self._trace_dispatch)
        print(
            f"[vibe-debug] Universal runtime tracer active - "
            f"watching: {self.project_root}"
        )

    def stop(self):
        """Deactivate universal tracing."""
        if not self._active:
            return
        self._active = False
        sys.settrace(None)
        threading.settrace(None)
        print(
            f"[vibe-debug] Runtime tracer stopped - "
            f"{self._span_count} spans created from "
            f"{self._call_count} calls observed"
        )

    def _trace_dispatch(self, frame, event, arg):
        """
        Core sys.settrace() callback.
        Called by Python interpreter on every function event.
        Must be extremely fast - runs on every function call.
        """
        if not self._active:
            return None

        filename = frame.f_code.co_filename
        func_name = frame.f_code.co_name

        if not self.filter.is_relevant(filename, func_name):
            return None

        self._call_count += 1

        if event == "call":
            return self._on_call(frame, func_name, filename)
        return self._trace_dispatch

    def _on_call(self, frame, func_name: str, filename: str):
        """
        Handle a function call event.
        Creates an OTel span and stores start time in frame locals.
        """
        try:
            rel_file = os.path.relpath(filename, self.project_root)
        except ValueError:
            rel_file = filename

        line = frame.f_code.co_firstlineno
        module = frame.f_globals.get("__name__", "unknown")

        try:
            locals_preview = {
                k: str(v)[:100]
                for k, v in list(frame.f_locals.items())[:3]
                if not k.startswith("_")
            }
            input_preview = str(locals_preview)[:200]
        except Exception:
            input_preview = ""

        span = self._tracer.start_span(func_name)
        try:
            span.set_attribute("vibe.type", "auto_traced")
            span.set_attribute("vibe.name", func_name)
            span.set_attribute("vibe.file", rel_file)
            span.set_attribute("vibe.line", line)
            span.set_attribute("vibe.module", module)
            span.set_attribute("vibe.input_preview", input_preview)
            span.set_attribute("vibe.start_time", time.time())
            self._span_count += 1
        except Exception:
            pass

        frame.f_locals["__vibe_span__"] = span
        frame.f_locals["__vibe_start__"] = time.time()

        def local_trace(f, e, a):
            if e == "return":
                self._on_return(f, a, span)
            elif e == "exception":
                self._on_exception(f, a, span)
            return local_trace

        return local_trace

    def _on_return(self, frame, return_value, span):
        """Handle function return - finalize span."""
        try:
            start_time = frame.f_locals.get("__vibe_start__", time.time())
            duration_ms = (time.time() - start_time) * 1000

            span.set_attribute("execution_time_ms", duration_ms)
            span.set_attribute("vibe.output_preview", str(return_value)[:200])

            if return_value is None or return_value == [] \
                    or return_value == "" or return_value == {}:
                span.set_status(
                    Status(StatusCode.ERROR, "Silent failure detected")
                )
                span.set_attribute("vibe.status", "SILENT_FAILURE")
            else:
                span.set_status(Status(StatusCode.OK))
                span.set_attribute("vibe.status", "OK")
        except Exception:
            pass
        finally:
            try:
                span.end()
            except Exception:
                pass

    def _on_exception(self, frame, exc_info, span):
        """Handle exception in function - mark span as error."""
        try:
            exc_type, exc_value, _ = exc_info
            if exc_type and exc_value:
                span.record_exception(exc_value)
                span.set_status(
                    Status(StatusCode.ERROR, str(exc_value))
                )
                span.set_attribute("vibe.status", "ERROR")
                span.set_attribute(
                    "error.type",
                    exc_type.__name__ if exc_type else "Unknown"
                )
        except Exception:
            pass
