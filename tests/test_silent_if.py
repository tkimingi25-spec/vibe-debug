"""Opt-in silent_if predicate (Stage 7, limitations task).

Defaults unchanged: without silent_if only structural emptiness is
flagged (proven by the existing silent-failure suite passing
unmodified alongside this file).
"""

import asyncio

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from vibe_debug.decorators import vibetrace


@pytest.fixture
def exporter():
    exp = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exp)
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.add_span_processor(processor)
    else:
        new_provider = TracerProvider()
        new_provider.add_span_processor(processor)
        trace.set_tracer_provider(new_provider)
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider) and current is not new_provider:
            current.add_span_processor(processor)
    exp.clear()
    yield exp
    exp.clear()
    processor.shutdown()


def _only(exporter, name):
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(spans) == 1
    return spans[0]


def test_predicate_flags_nonempty_invalid_result(exporter):
    @vibetrace("test", silent_if=lambda r: isinstance(r, dict) and not r.get("valid"))
    def work():
        return {"valid": False, "rows": 3}

    assert work() == {"valid": False, "rows": 3}
    span = _only(exporter, "work")
    assert span.status.status_code == StatusCode.ERROR
    assert span.attributes["vibe.status"] == "ERROR"


def test_predicate_not_applied_by_default(exporter):
    @vibetrace("test")
    def work_default():
        return {"valid": False, "rows": 3}

    work_default()
    span = _only(exporter, "work_default")
    assert span.status.status_code == StatusCode.OK


def test_raising_predicate_never_breaks_call(exporter):
    sentinel = object()

    def boom(result):
        raise RuntimeError("predicate exploded")

    @vibetrace("test", silent_if=boom)
    def work_raising():
        return sentinel

    assert work_raising() is sentinel
    span = _only(exporter, "work_raising")
    assert span.status.status_code == StatusCode.OK


def test_async_predicate_variant(exporter):
    @vibetrace("test", silent_if=lambda r: r == "empty-ish")
    async def work_async():
        return "empty-ish"

    # Note: the predicate's == is on plain str here; the decorator's
    # own guard never invokes __eq__ on the result itself.
    assert asyncio.run(work_async()) == "empty-ish"
    span = _only(exporter, "work_async")
    assert span.status.status_code == StatusCode.ERROR


class _Sized:
    def __init__(self, n):
        self._n = n

    def __len__(self):
        return self._n


def test_len_optin_flags_custom_sized(exporter):
    @vibetrace("test", silent_if=len)
    def work_sized():
        return _Sized(0)

    work_sized()
    span = _only(exporter, "work_sized")
    assert span.status.status_code == StatusCode.ERROR


def test_custom_sized_not_flagged_by_default(exporter):
    @vibetrace("test")
    def work_sized_default():
        return _Sized(0)

    work_sized_default()
    span = _only(exporter, "work_sized_default")
    assert span.status.status_code == StatusCode.OK
