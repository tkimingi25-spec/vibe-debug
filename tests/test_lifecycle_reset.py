"""Safe server lifecycle + resettable tracer state (Improvement 8).

Deterministic; no Phoenix required except the clearly-marked manager
up->down integration test on port 6007. Every process spawned here is
terminated in cleanup.
"""

import json
import subprocess
import sys
import time

import httpx
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from server import manager
from vibe_debug.tracer import (
    current_provider,
    get_export_health,
    init_vibedebug,
    reset_tracer,
)


@pytest.fixture
def sleeper():
    """Spawn a harmless owned process; always cleaned up."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"]
    )
    yield proc
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


@pytest.fixture
def clean_tracer_state():
    reset_tracer()
    yield
    reset_tracer()


def _write_pid_file(tmp_path, payload):
    path = tmp_path / manager.PID_FILENAME
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# -- Lifecycle: identity gate -------------------------------------------------


def test_pid_reuse_refused_and_process_survives(tmp_path, sleeper, capsys):
    created = manager._process_creation_time(sleeper.pid)
    assert created is not None
    _write_pid_file(
        tmp_path,
        {
            "version": 1,
            "pid": sleeper.pid,
            "created": created + 100000.0,
            "marker": "phoenix.server.main",
        },
    )
    assert manager.stop_server(str(tmp_path)) is False
    assert "Refusing to signal" in capsys.readouterr().out
    assert sleeper.poll() is None


def test_dead_pid_stale_file_removed(tmp_path, capsys):
    dead_pid = None
    for _ in range(10):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait(timeout=10)
        if not manager._is_process_alive(proc.pid):
            dead_pid = proc.pid
            break
    if dead_pid is None:
        pytest.skip("could not obtain a currently-dead PID")
    path = _write_pid_file(
        tmp_path,
        {"version": 1, "pid": dead_pid, "created": 1.0, "marker": "phoenix.server.main"},
    )
    assert manager.stop_server(str(tmp_path)) is False
    out = capsys.readouterr().out
    assert "not running" in out
    assert not path.exists()


def test_old_format_pid_file_refused_and_process_survives(tmp_path, sleeper, capsys):
    path = _write_pid_file(tmp_path, str(sleeper.pid))
    assert manager.stop_server(str(tmp_path)) is False
    assert "Refusing to signal" in capsys.readouterr().out
    assert sleeper.poll() is None
    # Unverifiable files are kept for inspection (stated behaviour).
    assert path.exists()


def test_malformed_pid_file_signals_nothing(tmp_path, capsys):
    path = _write_pid_file(tmp_path, "{not json, not an int[")
    assert manager.stop_server(str(tmp_path)) is False
    assert "nothing signalled" in capsys.readouterr().out
    assert not path.exists()


# -- Lifecycle: fingerprint/verify units --------------------------------------


def test_verify_units(monkeypatch):
    record = {"pid": 4242, "created": 1000.0, "marker": "phoenix.server.main"}
    monkeypatch.setattr(manager, "_is_process_alive", lambda pid: True)
    monkeypatch.setattr(manager, "_process_creation_time", lambda pid: 1000.0)
    monkeypatch.setattr(
        manager, "_process_command_line", lambda pid: "python -m phoenix.server.main serve"
    )
    assert manager._verify_process_identity(record) == "verified"

    monkeypatch.setattr(manager, "_process_creation_time", lambda pid: 5000.0)
    assert manager._verify_process_identity(record) == "mismatch"

    monkeypatch.setattr(manager, "_process_creation_time", lambda pid: 1000.0)
    monkeypatch.setattr(manager, "_process_command_line", lambda pid: "python sleeper.py")
    assert manager._verify_process_identity(record) == "mismatch"

    # Process vanished between read and verify: handled, no exception.
    monkeypatch.setattr(manager, "_process_creation_time", lambda pid: None)
    monkeypatch.setattr(manager, "_process_command_line", lambda pid: None)
    assert manager._verify_process_identity(record) == "unavailable"
    monkeypatch.setattr(manager, "_is_process_alive", lambda pid: False)
    assert manager._verify_process_identity(record) == "dead"


def test_proc_stat_parser_pure():
    fields = ["0"] * 18 + ["12345"]
    text = "4242 (python) S " + " ".join(fields)
    assert manager._parse_proc_stat_starttime(text) == 12345.0
    assert manager._parse_proc_stat_starttime("garbage") is None


def test_fingerprint_capture_shape(sleeper):
    fp = manager._capture_fingerprint(sleeper.pid)
    assert fp["pid"] == sleeper.pid
    assert fp["marker"] == "phoenix.server.main"
    assert fp["created"] is not None  # Windows creation time via ctypes


# -- Lifecycle: genuine stop still works (Phoenix on 6007) ---------------------


def test_manager_up_down_genuine_stop(tmp_path, monkeypatch):
    import socket

    # A second Phoenix cannot coexist with one already running (shared
    # default gRPC port); this genuine up->down path is exercised when
    # no other Phoenix is up (e.g. running this file standalone).
    try:
        if httpx.get("http://127.0.0.1:6006/healthz", timeout=2.0).status_code == 200:
            pytest.skip("another Phoenix already running on 6006")
    except Exception:
        pass

    probe = socket.socket()
    try:
        probe.bind(("127.0.0.1", 6007))
    except OSError:
        pytest.skip("port 6007 already in use")
    finally:
        probe.close()

    monkeypatch.setenv("PHOENIX_HOST", "127.0.0.1")
    monkeypatch.setenv("PHOENIX_PORT", "6007")
    assert manager.launch_server(project_root=str(tmp_path)) is True
    try:
        response = httpx.get("http://127.0.0.1:6007/healthz", timeout=5.0)
        assert response.status_code == 200
        pid_path = tmp_path / manager.PID_FILENAME
        record = json.loads(pid_path.read_text(encoding="utf-8"))
        assert record["pid"] > 0 and record["marker"] == "phoenix.server.main"
        assert manager.stop_server(str(tmp_path)) is True
        assert not pid_path.exists()
    finally:
        # Ensure the test-owned Phoenix never survives, even on failure.
        if (tmp_path / manager.PID_FILENAME).exists():
            manager.stop_server(str(tmp_path))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            httpx.get("http://127.0.0.1:6007/healthz", timeout=1.0)
            time.sleep(0.5)
        except Exception:
            break
    else:
        raise AssertionError("port 6007 still answering after stop_server")


# -- Reset tests ----------------------------------------------------------------


def _emit(tracer, provider, name):
    with tracer.start_as_current_span(name):
        pass
    provider.force_flush()


def test_init_reset_reinit_objective(clean_tracer_state):
    mem_a = InMemorySpanExporter()
    tracer_a = init_vibedebug(
        "reset-a", exporter=mem_a, endpoint="http://127.0.0.1:6007/v1/traces"
    )
    provider_a = current_provider()
    _emit(tracer_a, provider_a, "span_a")
    assert [s.name for s in mem_a.get_finished_spans()] == ["span_a"]

    reset_tracer()
    assert current_provider() is None
    assert get_export_health()["status"] == "no_export_yet"

    mem_b = InMemorySpanExporter()
    tracer_b = init_vibedebug(
        "reset-b", exporter=mem_b, endpoint="http://127.0.0.1:6008/v1/traces"
    )
    provider_b = current_provider()
    assert provider_b is not provider_a
    _emit(tracer_b, provider_b, "span_b")
    assert [s.name for s in mem_b.get_finished_spans()] == ["span_b"]
    assert [s.name for s in mem_a.get_finished_spans()] == ["span_a"]
    import vibe_debug.tracer as tracer_module

    assert tracer_module._health_exporter.delegate is mem_b


def test_health_state_reset(clean_tracer_state):
    mem = InMemorySpanExporter()
    tracer = init_vibedebug("health-reset", exporter=mem)
    _emit(tracer, current_provider(), "health_span")
    assert get_export_health()["successes"] >= 1
    reset_tracer()
    health = get_export_health()
    assert health["initialized"] is False
    assert health["status"] == "no_export_yet"
    assert health["successes"] == 0


def test_reset_noop_safety(clean_tracer_state):
    reset_tracer()
    reset_tracer()


def test_post_reset_decorator_usable(clean_tracer_state):
    mem = InMemorySpanExporter()
    init_vibedebug("post-reset", exporter=mem)
    reset_tracer()
    mem_b = InMemorySpanExporter()
    init_vibedebug("post-reset-b", exporter=mem_b)

    from vibe_debug.decorators import vibetrace

    sentinel = object()

    @vibetrace(component_type="test")
    def post_reset_work():
        return sentinel

    assert post_reset_work() is sentinel
    current_provider().force_flush()
    names = [s.name for s in mem_b.get_finished_spans()]
    assert "post_reset_work" in names
    assert mem.get_finished_spans() == ()
