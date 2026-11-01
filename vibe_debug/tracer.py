import os
import threading
import time

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)

from vibe_debug.config.config import DEFAULT_OTLP_ENDPOINT

load_dotenv()

_initialized = False
_health_exporter = None

_MAX_ERROR_LENGTH = 200


class HealthRecordingExporter(SpanExporter):
    """Delegate to a SpanExporter and record actual export outcomes.

    Recording happens here, in ``export()`` on the span-processor
    worker thread, so a recorded success/failure is the exporter's
    real result — never a span merely having been queued. Counters
    count export CALLS (batches), not individual spans. No span
    contents are recorded, only outcome metadata and bounded error
    text. All state is guarded by a lock; snapshots are copies.
    """

    def __init__(self, delegate):
        self._delegate = delegate
        self._lock = threading.Lock()
        self._successes = 0
        self._failures = 0
        self._last_success_at = None
        self._last_failure_at = None
        self._last_error = None
        self._last_status = None

    @property
    def delegate(self):
        return self._delegate

    def export(self, spans):
        try:
            result = self._delegate.export(spans)
        except Exception as e:
            self._record_failure(f"{type(e).__name__}: {e}")
            return SpanExportResult.FAILURE
        if result is SpanExportResult.SUCCESS:
            self._record_success()
        else:
            name = getattr(result, "name", None) or str(result)
            self._record_failure(f"export returned {name}")
        return result

    def _record_success(self):
        with self._lock:
            self._successes += 1
            self._last_success_at = time.time()
            self._last_status = "ok"

    def _record_failure(self, error_text):
        text = str(error_text)[:_MAX_ERROR_LENGTH] if error_text else "unknown error"
        with self._lock:
            self._failures += 1
            self._last_failure_at = time.time()
            self._last_error = text
            self._last_status = "failed"

    def get_export_health(self) -> dict:
        """Return a thread-safe snapshot copy of the export health."""
        with self._lock:
            return {
                "initialized": True,
                "status": self._last_status or "no_export_yet",
                "successes": self._successes,
                "failures": self._failures,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "last_error": self._last_error,
            }

    def shutdown(self):
        return self._delegate.shutdown()

    def force_flush(self, timeout_millis=None):
        delegate_flush = getattr(self._delegate, "force_flush", None)
        if delegate_flush is None:
            return True
        if timeout_millis is None:
            return delegate_flush()
        return delegate_flush(timeout_millis)


def get_export_health() -> dict:
    """Snapshot of this process's tracer export health.

    Scoped to the exporter configured by ``init_vibedebug`` in this
    process. Before initialisation, reports ``no_export_yet``.
    """
    exporter = _health_exporter
    if exporter is None:
        return {
            "initialized": False,
            "status": "no_export_yet",
            "successes": 0,
            "failures": 0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": None,
        }
    return exporter.get_export_health()


def init_vibedebug(project_name="vibe-debug"):
    global _initialized, _health_exporter
    if _initialized:
        return trace.get_tracer(__name__)

    otlp_endpoint = os.getenv("OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT)

    print(f"[vibe-debug] Initializing tracer -> {otlp_endpoint}")

    resource = Resource(attributes={"service.name": project_name})
    provider = TracerProvider(resource=resource)

    try:
        exporter = HealthRecordingExporter(
            OTLPSpanExporter(
                endpoint=otlp_endpoint,
                headers={}
            )
        )
        _health_exporter = exporter
        processor = BatchSpanProcessor(
            exporter,
            export_timeout_millis=5000,
            schedule_delay_millis=500,
            max_export_batch_size=32,
        )
        provider.add_span_processor(processor)
        print("[vibe-debug] Exporter registered successfully")
    except Exception as e:
        print(f"[vibe-debug] ERROR setting up exporter: {e}")

    trace.set_tracer_provider(provider)
    _initialized = True
    return trace.get_tracer(__name__)
