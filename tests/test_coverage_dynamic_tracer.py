"""Coverage diagnostics + dynamic runtime tracer (Stage 6)."""

import argparse
from types import SimpleNamespace

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from vibe_debug import cli
from vibe_debug.runtime.sys_tracer import UniversalRuntimeTracer
from vibe_debug.tracer import current_provider, init_vibedebug, reset_tracer


@pytest.fixture
def clean_tracer_state():
    reset_tracer()
    yield
    reset_tracer()


def test_coverage_lists_configured_but_missing_module(tmp_path):
    (tmp_path / "vibedebug.toml").write_text(
        '[watch]\nmodules = ["definitely_missing_module_xyz", "json"]\n',
        encoding="utf-8",
    )
    text = "\n".join(cli._coverage_lines(tmp_path))
    assert "definitely_missing_module_xyz" in text
    assert "Configured modules not found: definitely_missing_module_xyz" in text
    assert "Watched/configured modules:" in text


def test_coverage_doctor_works_with_phoenix_down(tmp_path, capsys):
    (tmp_path / "vibedebug.toml").write_text(
        '[watch]\nmodules = ["definitely_missing_module_xyz"]\n', encoding="utf-8"
    )
    args = argparse.Namespace(project_root=tmp_path)
    assert cli.command_doctor(args) == 0
    out = capsys.readouterr().out
    assert "Tracing coverage:" in out
    assert "definitely_missing_module_xyz" in out
    assert "Coverage source: local config/cache only" in out


def test_runtime_tracer_constructed_before_reset_lands_in_b(clean_tracer_state):
    # Constructed BEFORE any init: a captured global tracer would keep
    # the first provider; dynamic acquisition must re-point after reset.
    runtime = UniversalRuntimeTracer(project_root=".", project_name="test")

    def emit(name):
        frame = SimpleNamespace(
            f_locals={},
            f_globals={"__name__": "coverage_test"},
            f_code=SimpleNamespace(co_firstlineno=1),
        )
        runtime._on_call(frame, name, __file__)
        span = frame.f_locals["__vibe_span__"]
        runtime._on_return(frame, "ok", span)
        current_provider().force_flush()

    mem_a = InMemorySpanExporter()
    init_vibedebug("coverage-a", exporter=mem_a)
    emit("func_a")
    assert [s.name for s in mem_a.get_finished_spans()] == ["func_a"]

    reset_tracer()
    mem_b = InMemorySpanExporter()
    init_vibedebug("coverage-b", exporter=mem_b)
    emit("func_b")
    assert [s.name for s in mem_b.get_finished_spans()] == ["func_b"]
    assert [s.name for s in mem_a.get_finished_spans()] == ["func_a"]
