import functools
import inspect
import time
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

def vibetrace(component_type: str):
    """
    Universal decorator to capture AI pipeline spans.
    Captures: function name, inputs (str), outputs, silent failures (None, [], ""), 
    exceptions, and timing.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(func.__name__) as span:
                # Set metadata
                span.set_attribute("vibe.type", component_type or "unknown")
                span.set_attribute("vibe.name", func.__name__)
                
                # Capture inputs as strings
                arg_str = ", ".join([str(a) for a in args])
                kwarg_str = ", ".join([f"{k}={v}" for k, v in kwargs.items()])
                span.set_attribute("vibe.input", f"{arg_str} {kwarg_str}".strip())

                # Capture source location
                try:
                    source_file = inspect.getfile(func)
                    source_lines, start_line = inspect.getsourcelines(func)
                    span.set_attribute("vibe.file", source_file)
                    span.set_attribute("vibe.line", start_line)
                    span.set_attribute("vibe.module", func.__module__)
                except (TypeError, OSError):
                    span.set_attribute("vibe.file", "unknown")
                    span.set_attribute("vibe.line", 0)
                    span.set_attribute("vibe.module", "unknown")

                # Capture docstring
                doc = inspect.getdoc(func) or ""
                if doc:
                    span.set_attribute("vibe.doc", doc)
                
                start_time = time.time()
                try:
                    result = func(*args, **kwargs)
                    execution_time = (time.time() - start_time) * 1000  # ms
                    
                    span.set_attribute("vibe.output", str(result))
                    span.set_attribute("execution_time_ms", execution_time)
                    
                    # 🧩 Silent Failure Detection
                    if result is None or result == [] or result == "" or result == {}:
                        span.set_status(Status(StatusCode.ERROR, description="Silent Failure detected"))
                        span.set_attribute("vibe.status", "ERROR")
                    else:
                        span.set_status(Status(StatusCode.OK))
                        span.set_attribute("vibe.status", "OK")
                        
                    return result
                    
                except Exception as e:
                    execution_time = (time.time() - start_time) * 1000
                    span.record_exception(e)
                    span.set_status(Status(StatusCode.ERROR, description=str(e)))
                    span.set_attribute("vibe.status", "ERROR")
                    span.set_attribute("error.type", type(e).__name__)
                    span.set_attribute("execution_time_ms", execution_time)
                    raise e
                    
        return wrapper
    return decorator
