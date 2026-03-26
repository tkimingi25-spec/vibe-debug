import functools
import os
import socket
import time
from typing import Optional

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode


class IOInterceptor:
    """
    Universal I/O interceptor for outbound network calls.
    Patches at three levels for maximum coverage:

    Level 1 - requests library (if installed)
    Level 2 - httpx library (if installed)
    Level 3 - socket.create_connection (universal TCP fallback)

    Each level is independent. All three can be active simultaneously.
    Captures: URL, method, status code, latency, host, error.
    """

    def __init__(self, project_name: str = "vibe-debug"):
        self.project_name = project_name
        self._tracer = trace.get_tracer(
            "vibe_debug.runtime.io_interceptor"
        )
        self._active_patches = []
        self._call_count = 0

    def start(self) -> dict:
        """
        Activate all available interceptors.
        Returns dict of what was patched successfully.
        """
        results = {}
        results["requests"] = self._patch_requests()
        results["httpx"] = self._patch_httpx()
        results["socket"] = self._patch_socket()

        active = [k for k, v in results.items() if v]
        print(
            f"[vibe-debug] I/O interceptor active: "
            f"{', '.join(active)}"
        )
        return results

    def stop(self):
        """Restore all original functions."""
        for restore_fn in self._active_patches:
            try:
                restore_fn()
            except Exception:
                pass
        self._active_patches.clear()
        print(
            f"[vibe-debug] I/O interceptor stopped - "
            f"{self._call_count} network calls captured"
        )

    def _patch_requests(self) -> bool:
        """Patch requests.Session.request() to capture all HTTP calls."""
        try:
            import requests
            original = requests.Session.request

            interceptor = self

            @functools.wraps(original)
            def patched_request(session_self, method, url, **kwargs):
                return interceptor._traced_http_call(
                    original, session_self, method, url, **kwargs
                )

            requests.Session.request = patched_request
            self._active_patches.append(
                lambda: setattr(
                    requests.Session, "request", original
                )
            )
            return True
        except ImportError:
            return False
        except Exception as e:
            print(f"[vibe-debug] requests patch failed: {e}")
            return False

    def _patch_httpx(self) -> bool:
        """Patch httpx.Client.request() to capture all HTTP calls."""
        try:
            import httpx
            original = httpx.Client.request

            interceptor = self

            @functools.wraps(original)
            def patched_request(client_self, method, url, **kwargs):
                return interceptor._traced_http_call(
                    original, client_self, method, url, **kwargs
                )

            httpx.Client.request = patched_request
            self._active_patches.append(
                lambda: setattr(httpx.Client, "request", original)
            )
            return True
        except ImportError:
            return False
        except Exception as e:
            print(f"[vibe-debug] httpx patch failed: {e}")
            return False

    def _patch_socket(self) -> bool:
        """
        Patch socket.create_connection() as universal TCP fallback.
        Captures any outbound TCP connection regardless of library.
        Does NOT patch Phoenix's own OTLP connection to avoid loops.
        """
        try:
            original_create_connection = socket.create_connection
            interceptor = self

            @functools.wraps(original_create_connection)
            def patched_create_connection(address, timeout=None,
                                         source_address=None, *args,
                                         **kwargs):
                host, port = address[0], address[1]

                if port in (6006, 4317):
                    return original_create_connection(
                        address, timeout, source_address,
                        *args, **kwargs
                    )

                with interceptor._tracer.start_as_current_span(
                    "tcp.connect"
                ) as span:
                    span.set_attribute("vibe.type", "network")
                    span.set_attribute("net.host", host)
                    span.set_attribute("net.port", port)
                    span.set_attribute(
                        "net.address", f"{host}:{port}"
                    )

                    start = time.time()
                    try:
                        conn = original_create_connection(
                            address, timeout, source_address,
                            *args, **kwargs
                        )
                        elapsed = (time.time() - start) * 1000
                        span.set_attribute("net.latency_ms", elapsed)
                        span.set_status(Status(StatusCode.OK))
                        interceptor._call_count += 1
                        return conn
                    except Exception as e:
                        elapsed = (time.time() - start) * 1000
                        span.set_attribute("net.latency_ms", elapsed)
                        span.record_exception(e)
                        span.set_status(
                            Status(StatusCode.ERROR, str(e))
                        )
                        raise

            socket.create_connection = patched_create_connection
            self._active_patches.append(
                lambda: setattr(
                    socket, "create_connection",
                    original_create_connection
                )
            )
            return True
        except Exception as e:
            print(f"[vibe-debug] socket patch failed: {e}")
            return False

    def _traced_http_call(
        self, original_fn, client_self,
        method: str, url, **kwargs
    ):
        """
        Shared span creation logic for HTTP calls.
        Used by both requests and httpx patches.
        """
        url_str = str(url)

        try:
            from urllib.parse import urlparse
            parsed = urlparse(url_str)
            host = parsed.netloc or url_str
            path = parsed.path or "/"
        except Exception:
            host = url_str
            path = "/"

        if "6006" in host or "4317" in host:
            return original_fn(client_self, method, url, **kwargs)

        span_name = f"http.{method.upper()} {host}{path}"

        with self._tracer.start_as_current_span(span_name) as span:
            span.set_attribute("vibe.type", "http_call")
            span.set_attribute("http.method", method.upper())
            span.set_attribute("http.url", url_str[:500])
            span.set_attribute("http.host", host)

            body = kwargs.get("json") or kwargs.get("data") or ""
            if body:
                span.set_attribute(
                    "http.request_preview", str(body)[:200]
                )

            start = time.time()
            try:
                response = original_fn(
                    client_self, method, url, **kwargs
                )
                elapsed = (time.time() - start) * 1000

                span.set_attribute("http.latency_ms", elapsed)

                status_code = getattr(
                    response, "status_code",
                    getattr(response, "status", 0)
                )
                span.set_attribute("http.status_code", status_code)

                if elapsed > 3000:
                    span.set_attribute("http.slow", True)

                if status_code and status_code >= 400:
                    span.set_status(
                        Status(
                            StatusCode.ERROR,
                            f"HTTP {status_code}"
                        )
                    )
                else:
                    span.set_status(Status(StatusCode.OK))

                self._call_count += 1
                return response

            except Exception as e:
                elapsed = (time.time() - start) * 1000
                span.set_attribute("http.latency_ms", elapsed)
                span.record_exception(e)
                span.set_status(
                    Status(StatusCode.ERROR, str(e))
                )
                raise
