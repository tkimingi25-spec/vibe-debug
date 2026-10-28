"""Non-throwing silent-failure detection (Improvement 2).

New file rather than extending tests/test_instrument.py: that file mocks
the tracer and cannot assert span status. These tests use an in-memory
exporter and do not require Phoenix.
"""

import importlib.util

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from vibe_debug.decorators import _is_silent_failure, vibetrace


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
        # If the global Once already consumed, fall back to that provider.
        current = trace.get_tracer_provider()
        if isinstance(current, TracerProvider) and current is not new_provider:
            current.add_span_processor(processor)
    exp.clear()
    yield exp
    exp.clear()
    processor.shutdown()


def _only_span(exporter, name):
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(spans) == 1, f"expected one span {name!r}, got {len(spans)}"
    return spans[0]


def _assert_flagged(span):
    assert span.attributes["vibe.status"] == "ERROR"
    assert span.status.status_code == StatusCode.ERROR
    assert span.status.description == "Silent Failure detected"


def _assert_not_flagged(span):
    assert span.attributes["vibe.status"] == "OK"
    assert span.status.status_code == StatusCode.OK


class RaisingEq:
    def __eq__(self, other):
        raise RuntimeError("eq exploded")

    __hash__ = object.__hash__


class _AmbiguousBool:
    def __bool__(self):
        raise ValueError("The truth value of an array is ambiguous")

    __nonzero__ = __bool__


class NonBoolEq:
    """Simulates NumPy: __eq__ returns a non-bool with ambiguous truth."""

    def __eq__(self, other):
        return _AmbiguousBool()

    __hash__ = object.__hash__


class RaisingBool:
    def __bool__(self):
        raise ValueError("bool exploded")


class BadLenList(list):
    def __len__(self):
        raise RuntimeError("len exploded")


# 1. Raising __eq__
def test_raising_eq_returns_identity_and_raises_nothing(exporter):
    sentinel = RaisingEq()

    @vibetrace(component_type="test")
    def returns_raising_eq():
        return sentinel

    assert returns_raising_eq() is sentinel
    _assert_not_flagged(_only_span(exporter, "returns_raising_eq"))


# 2. Non-bool __eq__ (NumPy-style ambiguous truth value)
def test_non_bool_eq_returns_identity_and_raises_nothing(exporter):
    sentinel = NonBoolEq()

    @vibetrace(component_type="test")
    def returns_non_bool_eq():
        return sentinel

    assert returns_non_bool_eq() is sentinel
    _assert_not_flagged(_only_span(exporter, "returns_non_bool_eq"))


def test_raising_bool_is_not_invoked(exporter):
    sentinel = RaisingBool()

    @vibetrace(component_type="test")
    def returns_raising_bool():
        return sentinel

    assert returns_raising_bool() is sentinel
    _assert_not_flagged(_only_span(exporter, "returns_raising_bool"))


def test_len_raising_builtin_subclass_returns_false_and_untouched(exporter):
    sentinel = BadLenList()

    @vibetrace(component_type="test")
    def returns_bad_len():
        return sentinel

    assert returns_bad_len() is sentinel
    assert _is_silent_failure(sentinel) is False
    _assert_not_flagged(_only_span(exporter, "returns_bad_len"))


# 3. Regression: still flagged
@pytest.mark.parametrize("value", [[], "", {}, (), set(), frozenset()])
def test_empty_builtins_still_flagged(exporter, value):
    @vibetrace(component_type="test")
    def returns_empty():
        return value

    assert returns_empty() is value
    _assert_flagged(_only_span(exporter, "returns_empty"))


# 4. Regression: not flagged
@pytest.mark.parametrize("value", [[1], "x", {"a": 1}, (1,), {1}])
def test_non_empty_values_not_flagged(exporter, value):
    @vibetrace(component_type="test")
    def returns_non_empty():
        return value

    assert returns_non_empty() is value
    _assert_not_flagged(_only_span(exporter, "returns_non_empty"))


def test_normal_arbitrary_object_not_flagged(exporter):
    sentinel = object()

    @vibetrace(component_type="test")
    def returns_object():
        return sentinel

    assert returns_object() is sentinel
    _assert_not_flagged(_only_span(exporter, "returns_object"))


# 5. None: existing code flags None (`result is None or ...`); preserved.
def test_none_is_flagged_preserving_existing_semantics(exporter):
    @vibetrace(component_type="test")
    def returns_none():
        return None

    assert returns_none() is None
    _assert_flagged(_only_span(exporter, "returns_none"))


# Direct helper contract
@pytest.mark.parametrize(
    "value,expected",
    [(None, True), ([], True), ("", True), ({}, True), ([1], False), ("x", False)],
)
def test_helper_direct(value, expected):
    assert _is_silent_failure(value) is expected


def test_helper_never_raises_on_exotic_objects():
    assert _is_silent_failure(RaisingEq()) is False
    assert _is_silent_failure(NonBoolEq()) is False
    assert _is_silent_failure(RaisingBool()) is False
    assert _is_silent_failure(BadLenList()) is False


# 6. Async path
@pytest.mark.asyncio
async def test_async_raising_eq_returns_identity(exporter):
    sentinel = RaisingEq()

    @vibetrace(component_type="test")
    async def async_returns_raising_eq():
        return sentinel

    assert await async_returns_raising_eq() is sentinel
    _assert_not_flagged(_only_span(exporter, "async_returns_raising_eq"))


@pytest.mark.asyncio
async def test_async_non_bool_eq_returns_identity(exporter):
    sentinel = NonBoolEq()

    @vibetrace(component_type="test")
    async def async_returns_non_bool_eq():
        return sentinel

    assert await async_returns_non_bool_eq() is sentinel
    _assert_not_flagged(_only_span(exporter, "async_returns_non_bool_eq"))


# 7. Optional real NumPy/pandas, only if already installed
@pytest.mark.skipif(
    importlib.util.find_spec("numpy") is None, reason="numpy not installed"
)
def test_real_numpy_array_not_flagged_and_untouched(exporter):
    import numpy as np

    arr = np.array([1, 2, 3])

    @vibetrace(component_type="test")
    def returns_array():
        return arr

    assert returns_array() is arr
    _assert_not_flagged(_only_span(exporter, "returns_array"))


@pytest.mark.skipif(
    importlib.util.find_spec("pandas") is None, reason="pandas not installed"
)
def test_real_pandas_dataframe_not_flagged_and_untouched(exporter):
    import pandas as pd

    frame = pd.DataFrame({"a": [1]})

    @vibetrace(component_type="test")
    def returns_frame():
        return frame

    assert returns_frame() is frame
    _assert_not_flagged(_only_span(exporter, "returns_frame"))
