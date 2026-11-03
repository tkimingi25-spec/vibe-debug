"""Honest export-health reporting (Improvement 5).

No Phoenix required: stub exporters, an in-memory exporter, and one
real OTLP exporter pointed at a closed loopback port.
"""

import socket
import threading
import time
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    SimpleSpanProcessor,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from vibe_debug import cli
from vibe_debug.tracer import HealthRecordingExporter, get_export_health
import vibe_debug.tracer as tracer_module


class StubExporter:
    def __init__(self, result=SpanExportResult.SUCCESS, exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0
        self.shutdown_called = False
        self.flush_calls = []

    def export(self, spans):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result

    def shutdown(self):
        self.shutdown_called = True
        return "shutdown-result"

    def force_flush(self, timeout_millis=None):
        self.flush_calls.append(timeout_millis)
        return True


# 1. Actual success recorded
def test_actual_success_recorded():
    stub = StubExporter(SpanExportResult.SUCCESS)
    wrapper = HealthRecordingExporter(stub)
    assert wrapper.export([]) is SpanExportResult.SUCCESS
    assert stub.calls == 1
    health = wrapper.get_export_health()
    assert health["successes"] == 1
    assert health["failures"] == 0
    assert health["last_success_at"] is not None
    assert health["status"] == "ok"


# 2. Actual failure result recorded
def test_actual_failure_result_recorded():
    stub = StubExporter(SpanExportResult.FAILURE)
    wrapper = HealthRecordingExporter(stub)
    assert wrapper.export([]) is SpanExportResult.FAILURE
    health = wrapper.get_export_health()
    assert health["failures"] == 1
    assert health["successes"] == 0
    assert health["last_failure_at"] is not None
    assert health["last_error"]
    assert health["status"] == "failed"


# 3. Throwing exporter cannot escape
def test_throwing_exporter_cannot_escape():
    stub = StubExporter(exc=RuntimeError("delegate exploded"))
    wrapper = HealthRecordingExporter(stub)
    assert wrapper.export([]) is SpanExportResult.FAILURE
    health = wrapper.get_export_health()
    assert health["failures"] == 1
    assert "RuntimeError" in health["last_error"]
    assert "delegate exploded" in health["last_error"]
    assert len(health["last_error"]) <= 200


# 4. Closed-port objective test (real OTLP exporter, bounded by timeout=1)
def test_closed_port_records_failure_and_no_misleading_text(capsys):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    closed_port = sock.getsockname()[1]
    sock.close()

    exporter = HealthRecordingExporter(
        OTLPSpanExporter(
            endpoint=f"http://127.0.0.1:{closed_port}/v1/traces", timeout=1
        )
    )
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("export-health-test")
    started = time.monotonic()
    with tracer.start_as_current_span("closed_port_span"):
        pass
    elapsed = time.monotonic() - started
    provider.shutdown()

    health = exporter.get_export_health()
    assert health["status"] == "failed"
    assert health["failures"] >= 1
    assert health["last_error"]
    assert elapsed < 30, f"closed-port export took too long: {elapsed:.1f}s"
    assert "Export succeeded" not in capsys.readouterr().out


# 5. No misleading success text on the normal tracer path
def test_no_misleading_success_text(capsys):
    memory = InMemorySpanExporter()
    exporter = HealthRecordingExporter(memory)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("export-health-test")
    with tracer.start_as_current_span("honest_span"):
        pass
    provider.shutdown()
    assert exporter.get_export_health()["successes"] == 1
    out = capsys.readouterr().out
    assert "Export succeeded" not in out


# 6. Shutdown and flush delegation; health not dependent on flush alone
def test_shutdown_and_flush_delegation():
    stub = StubExporter(SpanExportResult.FAILURE)
    wrapper = HealthRecordingExporter(stub)
    assert wrapper.export([]) is SpanExportResult.FAILURE
    assert wrapper.force_flush(1234) is True
    assert stub.flush_calls == [1234]
    # Flush returning True must not rewrite the recorded export failure.
    assert wrapper.get_export_health()["status"] == "failed"
    wrapper.shutdown()
    assert stub.shutdown_called is True


# 7. Initial state / no export yet (+ snapshot is a copy)
def test_initial_state_no_export_yet(monkeypatch):
    wrapper = HealthRecordingExporter(StubExporter())
    health = wrapper.get_export_health()
    assert health["status"] == "no_export_yet"
    assert health["successes"] == 0 and health["failures"] == 0
    health["successes"] = 999
    assert wrapper.get_export_health()["successes"] == 0

    monkeypatch.setattr(tracer_module, "_health_exporter", None)
    module_health = get_export_health()
    assert module_health["status"] == "no_export_yet"
    assert module_health["initialized"] is False


# 8. CLI diagnostics output: three states
@pytest.mark.parametrize(
    "snapshot,expected",
    [
        (
            {
                "status": "ok",
                "successes": 4,
                "failures": 0,
                "last_success_at": time.time(),
                "last_failure_at": None,
                "last_error": None,
            },
            "Telemetry export: OK (last success ",
        ),
        (
            {
                "status": "failed",
                "successes": 0,
                "failures": 1,
                "last_success_at": None,
                "last_failure_at": time.time(),
                "last_error": "boom error",
            },
            "Telemetry export: FAILED (last error: boom error; successes=0, failures=1)",
        ),
        (
            {
                "status": "no_export_yet",
                "successes": 0,
                "failures": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "last_error": None,
            },
            "Telemetry export: no export attempted yet",
        ),
    ],
)
def test_cli_doctor_reports_export_health(monkeypatch, tmp_path, capsys, snapshot, expected):
    monkeypatch.setattr(cli.manager, "is_phoenix_healthy", lambda *a, **k: False)
    monkeypatch.setattr(cli, "get_export_health", lambda: dict(snapshot))
    rc = cli.command_doctor(SimpleNamespace(project_root=tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert expected in out


# 9. Thread-safety: 4 threads x 25 calls
def test_thread_safety():
    stub = StubExporter(SpanExportResult.SUCCESS)
    wrapper = HealthRecordingExporter(stub)

    def call_many():
        for _ in range(25):
            wrapper.export([])

    threads = [threading.Thread(target=call_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    health = wrapper.get_export_health()
    assert health["successes"] == 100
    assert health["failures"] == 0
    assert stub.calls == 100


# 11. Persistent export health (Stage 1 of limitations task)
def test_persisted_snapshot_written_on_success_and_failure(tmp_path):
    import json as _json

    state = tmp_path / ".vibe_export_health.json"
    ok = HealthRecordingExporter(StubExporter(SpanExportResult.SUCCESS), state_file=state, endpoint="http://x:1")
    ok.export([])
    data = _json.loads(state.read_text(encoding="utf-8"))
    assert data["status"] == "ok" and data["successes"] == 1 and data["writer_pid"] > 0
    assert data["endpoint"] == "http://x:1"
    bad = HealthRecordingExporter(StubExporter(SpanExportResult.FAILURE), state_file=state, endpoint="http://x:1")
    bad.export([])
    data = _json.loads(state.read_text(encoding="utf-8"))
    assert data["status"] == "failed" and data["failures"] == 1 and data["last_error"]


def test_persisted_snapshot_fresh_reader_and_stale(tmp_path):
    import time as _time

    from vibe_debug.tracer import read_persisted_export_health

    state = tmp_path / "health.json"
    wrapper = HealthRecordingExporter(StubExporter(SpanExportResult.SUCCESS), state_file=state)
    wrapper.export([])
    snap = read_persisted_export_health(state)
    assert snap["status"] == "ok" and snap["stale"] is False
    # Age it artificially.
    import json as _json

    data = _json.loads(state.read_text(encoding="utf-8"))
    data["timestamp"] = _time.time() - 7200
    state.write_text(_json.dumps(data), encoding="utf-8")
    snap = read_persisted_export_health(state)
    assert snap["stale"] is True

    from vibe_debug.cli import _format_persisted_export_health

    assert "stale snapshot" in _format_persisted_export_health(snap)


def test_persisted_snapshot_no_secrets_and_corrupt_degrades(tmp_path):
    from vibe_debug.cli import _export_health_line
    from vibe_debug.tracer import read_persisted_export_health

    state = tmp_path / "health.json"
    wrapper = HealthRecordingExporter(StubExporter(SpanExportResult.SUCCESS), state_file=state)
    wrapper.export([{"secret": "sk-test-" + "should-not-persist"}])
    text = state.read_text(encoding="utf-8")
    assert "should-not-persist" not in text
    assert "sk-test" not in text

    state.write_text("{corrupt", encoding="utf-8")
    assert read_persisted_export_health(state) is None


def test_persisted_snapshot_concurrent_writers(tmp_path):
    state = tmp_path / "health.json"
    wrapper = HealthRecordingExporter(StubExporter(SpanExportResult.SUCCESS), state_file=state)

    def call_many():
        for _ in range(25):
            wrapper.export([])

    threads = [threading.Thread(target=call_many) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    from vibe_debug.tracer import read_persisted_export_health

    snap = read_persisted_export_health(state)
    assert snap is not None and snap["successes"] == 100


# 10. No regression: normal traced function still spans correctly
def test_traced_function_still_spans():
    memory = InMemorySpanExporter()
    processor = SimpleSpanProcessor(memory)
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(processor)
    else:
        new_provider = TracerProvider()
        new_provider.add_span_processor(processor)
        trace.set_tracer_provider(new_provider)
    memory.clear()
    try:
        from vibe_debug.decorators import vibetrace

        @vibetrace(component_type="test")
        def normal_work(value):
            return {"value": value}

        assert normal_work(7) == {"value": 7}
        spans = [s for s in memory.get_finished_spans() if s.name == "normal_work"]
        assert len(spans) == 1
        assert spans[0].attributes["vibe.status"] == "OK"
    finally:
        memory.clear()
        processor.shutdown()
