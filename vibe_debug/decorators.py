import functools
import inspect
import time

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


def _is_silent_failure(result) -> bool:
    """Return True for None and empty built-in containers/str.

    Never invokes user-defined ``__eq__``/``__bool__``/``__len__`` on
    arbitrary results, and never raises: any inspection error means
    "not flagged". Trade-off: custom empty containers and array-like
    types (e.g. NumPy/pandas) are no longer flagged, because detecting
    them would require calling code that can raise (e.g. ambiguous
    array truth value). This is deliberate.
    """
    try:
        if result is None:
            return True
        if isinstance(result, (list, dict, str, tuple, set, frozenset)):
            return len(result) == 0
        return False
    except Exception:
        return False


def vibetrace(component_type: str):
    """Capture inputs, outputs, failures, and timing for a function call."""

    def decorator(func):
        def apply_metadata(span, args, kwargs):
            span.set_attribute("vibe.type", component_type or "unknown")
            span.set_attribute("vibe.name", func.__name__)

            arg_str = ", ".join(str(arg) for arg in args)
            kwarg_str = ", ".join(
                f"{key}={value}" for key, value in kwargs.items()
            )
            span.set_attribute("vibe.input", f"{arg_str} {kwarg_str}".strip())

            try:
                source_file = inspect.getfile(func)
                _, start_line = inspect.getsourcelines(func)
                span.set_attribute("vibe.file", source_file)
                span.set_attribute("vibe.line", start_line)
                span.set_attribute("vibe.module", func.__module__)
            except (TypeError, OSError):
                span.set_attribute("vibe.file", "unknown")
                span.set_attribute("vibe.line", 0)
                span.set_attribute("vibe.module", "unknown")

            doc = inspect.getdoc(func) or ""
            if doc:
                span.set_attribute("vibe.doc", doc)

        def apply_result(span, result, execution_time):
            span.set_attribute("vibe.output", str(result))
            span.set_attribute("execution_time_ms", execution_time)
            if _is_silent_failure(result):
                span.set_status(
                    Status(StatusCode.ERROR, description="Silent Failure detected")
                )
                span.set_attribute("vibe.status", "ERROR")
            else:
                span.set_status(Status(StatusCode.OK))
                span.set_attribute("vibe.status", "OK")

        def apply_exception(span, error, execution_time):
            span.record_exception(error)
            span.set_status(Status(StatusCode.ERROR, description=str(error)))
            span.set_attribute("vibe.status", "ERROR")
            span.set_attribute("error.type", type(error).__name__)
            span.set_attribute("execution_time_ms", execution_time)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(func.__name__) as span:
                apply_metadata(span, args, kwargs)
                started_at = time.time()
                try:
                    result = await func(*args, **kwargs)
                    apply_result(span, result, (time.time() - started_at) * 1000)
                    return result
                except Exception as error:
                    apply_exception(span, error, (time.time() - started_at) * 1000)
                    raise

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(func.__name__) as span:
                apply_metadata(span, args, kwargs)
                started_at = time.time()
                try:
                    result = func(*args, **kwargs)
                    apply_result(span, result, (time.time() - started_at) * 1000)
                    return result
                except Exception as error:
                    apply_exception(span, error, (time.time() - started_at) * 1000)
                    raise

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper

    return decorator
