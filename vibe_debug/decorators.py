import functools
import inspect
import time

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from vibe_debug.redaction import redact_value


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


def _parameter_names(func):
    """Best-effort positional parameter names; ([], None) if unknown."""
    try:
        params = list(inspect.signature(func).parameters.values())
    except (TypeError, ValueError):
        return [], None
    names = []
    var_positional = None
    for param in params:
        if param.kind is inspect.Parameter.VAR_POSITIONAL:
            var_positional = param.name
        elif param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            names.append(param.name)
    return names, var_positional


def vibetrace(component_type: str, config=None, *, redact_keys=(), max_attr_length=None):
    """Capture inputs, outputs, failures, and timing for a function call.

    ``vibe.input``/``vibe.output`` pass through redaction (see
    ``vibe_debug.redaction``): key-name-based only, no content scanning,
    so bare positional/free-text secrets are not detected.
    ``config`` is an optional VibeConfig; its redact_keys extend the
    defaults and its max_attr_length caps attribute length. Explicit
    decorator arguments override the config values.
    """

    def decorator(func):
        param_names, var_positional = _parameter_names(func)
        extra_keys = []
        if config is not None:
            extra_keys.extend(getattr(config, "redact_keys", ()) or ())
        extra_keys.extend(redact_keys or ())
        if max_attr_length is not None:
            effective_max = max_attr_length
        elif config is not None:
            effective_max = getattr(config, "max_attr_length", 1024)
        else:
            effective_max = 1024

        def apply_metadata(span, args, kwargs):
            span.set_attribute("vibe.type", component_type or "unknown")
            span.set_attribute("vibe.name", func.__name__)

            parts = []
            for index, arg in enumerate(args):
                if index < len(param_names):
                    name = param_names[index]
                else:
                    name = var_positional
                rendered = redact_value(
                    arg,
                    key=name,
                    extra_keys=extra_keys,
                    max_length=effective_max,
                )
                parts.append(f"{name}={rendered}" if name else rendered)
            for key, value in kwargs.items():
                rendered = redact_value(
                    value,
                    key=key,
                    extra_keys=extra_keys,
                    max_length=effective_max,
                )
                parts.append(f"{key}={rendered}")
            joined = ", ".join(parts)
            span.set_attribute(
                "vibe.input",
                redact_value(joined, max_length=effective_max),
            )

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
            span.set_attribute(
                "vibe.output",
                redact_value(
                    result,
                    extra_keys=extra_keys,
                    max_length=effective_max,
                ),
            )
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
