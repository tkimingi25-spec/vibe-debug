import json
import os
import threading
import time
from pathlib import Path

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
_provider = None
_state_lock = threading.RLock()

_MAX_ERROR_LENGTH = 200

EXPORT_HEALTH_FILENAME = ".vibe_export_health.json"
EXPORT_HEALTH_STALE_AFTER_SECONDS = 3600


def _export_health_path() -> Path:
    return Path.cwd() / EXPORT_HEALTH_FILENAME


def read_persisted_export_health(path=None):
    """Read the persisted export-health snapshot, or None. Never raises.

    Corrupt/partial files degrade to None (doctor then reports the
    pre-initialisation state). Adds ``age_seconds`` and ``stale``.
    """
    try:
        target = Path(path) if path is not None else _export_health_path()
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "status" not in data:
            return None
        timestamp = float(data.get("timestamp") or 0)
        age = max(0.0, time.time() - timestamp) if timestamp else None
        data["age_seconds"] = age
        data["stale"] = bool(age is not None and age > EXPORT_HEALTH_STALE_AFTER_SECONDS)
        return data
    except Exception:
        return None


class HealthRecordingExporter(SpanExporter):
    """Delegate to a SpanExporter and record actual export outcomes.

    Recording happens here, in ``export()`` on the span-processor
    worker thread, so a recorded success/failure is the exporter's
    real result — never a span merely having been queued. Counters
    count export CALLS (batches), not individual spans. No span
    contents are recorded, only outcome metadata and bounded error
    text. All state is guarded by a lock; snapshots are copies.
    """

    def __init__(self, delegate, *, state_file=None, endpoint=None):
        self._delegate = delegate
        self._state_file = Path(state_file) if state_file is not None else None
        self._endpoint = endpoint
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

    def _persist_locked(self):
        """Persist a metadata-only snapshot atomically. Never raises."""
        if self._state_file is None:
            return
        try:
            snapshot = {
                "timestamp": time.time(),
                "status": self._last_status,
                "successes": self._successes,
                "failures": self._failures,
                "last_error": self._last_error,
                "endpoint": self._endpoint,
                "writer_pid": os.getpid(),
            }
            tmp_path = self._state_file.with_name(self._state_file.name + ".tmp")
            tmp_path.write_text(json.dumps(snapshot), encoding="utf-8")
            os.replace(tmp_path, self._state_file)
        except Exception:
            pass

    def _record_success(self):
        with self._lock:
            self._successes += 1
            self._last_success_at = time.time()
            self._last_status = "ok"
            self._persist_locked()

    def _record_failure(self, error_text):
        text = str(error_text)[:_MAX_ERROR_LENGTH] if error_text else "unknown error"
        with self._lock:
            self._failures += 1
            self._last_failure_at = time.time()
            self._last_error = text
            self._last_status = "failed"
            self._persist_locked()

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


def current_provider():
    """Return the provider stored by ``init_vibedebug``, or None."""
    return _provider


def get_active_tracer(name=None):
    """Return a tracer from the currently stored provider, if any.

    OpenTelemetry's global provider can be set only once per process,
    so after a reset + re-initialisation the global provider still
    points at the first provider. Vibe-Debug's own acquisition path
    therefore prefers the stored provider: while a stored provider is
    active, spans through this function go through it (genuinely, via
    its own processors/exporters). With no stored provider, falls back
    to the OpenTelemetry global provider. Third-party code holding
    tracers from ``trace.get_tracer()`` is unaffected and keeps the
    first global provider.
    """
    provider = _provider
    if provider is not None:
        return provider.get_tracer(name or __name__)
    return trace.get_tracer(name or __name__)


def reset_tracer():
    """Shut down and clear the current tracer state. Harmless no-op
    when never initialised, and safe to call repeatedly.

    After reset, ``get_export_health()`` reports the pre-initialisation
    state, and a subsequent ``init_vibedebug(...)`` performs the full
    setup again (new provider, exporter, and HealthRecordingExporter)
    and genuinely serves Vibe-Debug spans through the new provider via
    ``get_active_tracer``.
    """
    global _initialized, _health_exporter, _provider
    with _state_lock:
        provider = _provider
        _provider = None
        _health_exporter = None
        _initialized = False
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                pass


def init_vibedebug(project_name="vibe-debug", *, exporter=None, endpoint=None):
    global _initialized, _health_exporter, _provider
    with _state_lock:
        if _initialized:
            print(
                "[vibe-debug] Tracer already initialized; new settings "
                "ignored. Call reset_tracer() before re-initializing."
            )
            return get_active_tracer(__name__)

        otlp_endpoint = endpoint or os.getenv(
            "OTLP_ENDPOINT", DEFAULT_OTLP_ENDPOINT
        )

        print(f"[vibe-debug] Initializing tracer -> {otlp_endpoint}")

        resource = Resource(attributes={"service.name": project_name})
        provider = TracerProvider(resource=resource)
        _provider = provider

        try:
            health_exporter = HealthRecordingExporter(
                exporter
                if exporter is not None
                else OTLPSpanExporter(
                    endpoint=otlp_endpoint,
                    headers={}
                ),
                state_file=_export_health_path(),
                endpoint=otlp_endpoint,
            )
            _health_exporter = health_exporter
            processor = BatchSpanProcessor(
                health_exporter,
                export_timeout_millis=5000,
                schedule_delay_millis=500,
                max_export_batch_size=32,
            )
            provider.add_span_processor(processor)
            print("[vibe-debug] Exporter registered successfully")
        except Exception as e:
            print(f"[vibe-debug] ERROR setting up exporter: {e}")

        try:
            trace.set_tracer_provider(provider)
        except Exception:
            pass
        _initialized = True
        return get_active_tracer(__name__)
