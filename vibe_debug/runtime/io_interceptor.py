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
        self._active = False

    def start(self, patch_socket: bool = False):
        if self._active:
            return
        self._active = True
        patched = []

        if self._patch_requests():
            patched.append("requests")
        if self._patch_httpx():
            patched.append("httpx")
        if patch_socket and self._patch_socket():
            patched.append("socket")

        label = ", ".join(patched) if patched else "none"
        print(f"[vibe-debug] I/O interceptor active: {label}")

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
        Patch socket.connect() as universal TCP fallback.
        Captures any outbound TCP connection regardless of library.
        Does NOT patch Phoenix's own OTLP connection to avoid loops.
        """
        try:
            original_connect = socket.socket.connect
            interceptor = self
            tracer = self._tracer
            interceptor._network_calls = getattr(
                interceptor, "_network_calls", 0
            )

            @functools.wraps(original_connect)
            def patched_connect(sock_self, address):
                host = address[0] if address else "unknown"
                port = address[1] if len(address) > 1 else 0

                with tracer.start_as_current_span("tcp.connect") as span:
                    span.set_attribute("vibe.type", "network")
                    span.set_attribute("tcp.host", str(host))
                    span.set_attribute("tcp.port", int(port))
                    span.set_attribute(
                        "tcp.destination", f"{host}:{port}"
                    )

                    SERVICE_PORTS = {
                        7687: "neo4j",
                        27017: "mongodb",
                        6379: "redis",
                        5432: "postgresql",
                        3306: "mysql",
                        9200: "elasticsearch",
                        6006: "phoenix",
                        11434: "ollama",
                        8080: "http-alt",
                        443: "https",
                        80: "http",
                    }
                    service = SERVICE_PORTS.get(int(port), "unknown")
                    if service != "unknown":
                        span.set_attribute("tcp.service", service)

                    if host in ("localhost", "127.0.0.1") and \
                       int(port) in (6006, 4317):
                        span.set_attribute("tcp.excluded", True)
                        return original_connect(sock_self, address)

                    try:
                        result = original_connect(sock_self, address)
                        span.set_status(Status(StatusCode.OK))
                        span.set_attribute("tcp.status", "connected")
                        interceptor._network_calls += 1
                        return result

                    except ConnectionRefusedError as e:
                        span.set_status(
                            Status(
                                StatusCode.ERROR,
                                f"Connection refused to {host}:{port}"
                            )
                        )
                        span.set_attribute(
                            "error.type", "ConnectionRefusedError"
                        )
                        span.set_attribute(
                            "tcp.hint",
                            f"Service at {host}:{port} is not running. "
                            f"Service: {service}"
                        )
                        span.record_exception(e)
                        interceptor._network_calls += 1
                        span.set_status(
                            Status(StatusCode.ERROR, str(e))
                        )
                        raise

                    except OSError as e:
                        span.set_status(
                            Status(StatusCode.ERROR, str(e))
                        )
                        span.set_attribute("error.type", type(e).__name__)
                        span.set_attribute("tcp.error_code", e.errno or 0)
                        span.record_exception(e)
                        interceptor._network_calls += 1
                        raise

                    except Exception as e:
                        span.set_status(
                            Status(StatusCode.ERROR, str(e))
                        )
                        span.set_attribute("error.type", type(e).__name__)
                        span.record_exception(e)
                        interceptor._network_calls += 1
                        raise

            socket.socket.connect = patched_connect
            self._active_patches.append(
                lambda: setattr(
                    socket.socket, "connect",
                    original_connect
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
