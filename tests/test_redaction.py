"""Redaction and truncation of span attributes (Improvement 1).

Offline tests: in-memory exporter, no Phoenix required.
"""

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from vibe_debug.config.config import VibeConfig, load_config
from vibe_debug.decorators import vibetrace
from vibe_debug.redaction import redact_value


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


def _only_span(exporter, name):
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(spans) == 1, f"expected one span {name!r}, got {len(spans)}"
    return spans[0]


# -- redact_value: key matching -------------------------------------------

@pytest.mark.parametrize(
    "key",
    [
        "password", "passwd", "api_key", "apiKey", "API_KEY", "apikey",
        "access_token", "auth_token", "token", "client_secret", "secret",
        "private_key", "authorization", "cookie", "set_cookie",
        "set-cookie", "my_password", "db_password",
    ],
)
def test_sensitive_keys_redacted(key):
    assert redact_value("s3cr3t-value", key=key) == "***"


@pytest.mark.parametrize(
    "key", ["query", "token_count", "secret_count", "name", "count", None]
)
def test_non_sensitive_keys_not_redacted(key):
    assert redact_value("visible", key=key) == "visible"


def test_nested_structures_redacted_recursively():
    out = redact_value(
        {"api_key": "abc", "q": "hi", "nested": [{"password": "p", "n": 1}]}
    )
    assert "abc" not in out and "'p'" not in out
    assert "***" in out and "hi" in out


def test_extra_keys_extend_defaults():
    assert redact_value("v", key="custom", extra_keys=["custom"]) == "***"
    # Defaults still apply alongside extras.
    assert redact_value("v", key="password", extra_keys=["custom"]) == "***"


def test_extra_keys_regex():
    assert redact_value("v", key="session_id", extra_keys=[r"session.*"]) == "***"
    assert redact_value("v", key="user_id", extra_keys=[r"session.*"]) == "v"


def test_bare_and_free_text_secrets_not_detected_by_design():
    # Documented non-goal: no content scanning.
    assert redact_value("use key sk-live-123", key="query") == "use key sk-live-123"
    assert redact_value("sk-live-123") == "sk-live-123"


# -- redact_value: truncation and safety -----------------------------------

def test_truncation_default_and_explicit():
    out = redact_value("a" * 5000)
    assert len(out) == 1024 and out.endswith("[truncated]")
    out = redact_value("x" * 100, max_length=20)
    assert len(out) == 20 and out.endswith("[truncated]")
    assert redact_value("short", max_length=20) == "short"


def test_cycle_and_max_depth_do_not_raise():
    cyc = {}
    cyc["self"] = cyc
    assert "<cycle>" in redact_value(cyc)
    deep = cur = {}
    for _ in range(20):
        cur["a"] = {}
        cur = cur["a"]
    assert "<max-depth>" in redact_value(deep)


class _BadStr:
    def __str__(self):
        raise RuntimeError("str exploded")

    def __repr__(self):
        raise RuntimeError("repr exploded")


class _RaisingEq:
    def __eq__(self, other):
        raise RuntimeError("eq exploded")

    __hash__ = object.__hash__


def test_redact_value_never_raises():
    assert redact_value(_BadStr()) == "<unrepresentable: _BadStr>"
    assert redact_value(_BadStr(), key="password") == "***"
    assert isinstance(redact_value(_RaisingEq()), str)
    assert isinstance(redact_value({"k": _BadStr()}), str)


# -- decorator wiring -------------------------------------------------------

def test_decorator_redacts_kwargs_and_positional_password(exporter):
    @vibetrace(component_type="test")
    def login(username, password):
        return "ok"

    assert login("alice", password="hunter2") == "ok"
    span = _only_span(exporter, "login")
    assert "hunter2" not in span.attributes["vibe.input"]
    assert "password=***" in span.attributes["vibe.input"]
    assert "alice" in span.attributes["vibe.input"]

    exporter.clear()

    @vibetrace(component_type="test")
    def login2(username, password):
        return "ok"

    assert login2("bob", "hunter3") == "ok"
    span = _only_span(exporter, "login2")
    assert "hunter3" not in span.attributes["vibe.input"]
    assert "password=***" in span.attributes["vibe.input"]


def test_decorator_redacts_output_dict_and_truncates(exporter):
    @vibetrace(component_type="test", max_attr_length=50)
    def fetch(api_key):
        return {"api_key": api_key, "data": "x" * 500}

    fetch("topsecret")
    span = _only_span(exporter, "fetch")
    assert "topsecret" not in span.attributes["vibe.input"]
    assert "topsecret" not in span.attributes["vibe.output"]
    assert "***" in span.attributes["vibe.output"]
    assert len(span.attributes["vibe.output"]) <= 50


def test_decorator_config_extends_redact_keys(exporter):
    config = VibeConfig(redact_keys=["prompt"], max_attr_length=1024)

    @vibetrace(component_type="test", config=config)
    def chat(prompt, query):
        return query

    chat("secret prompt text", "hello")
    span = _only_span(exporter, "chat")
    assert "secret prompt text" not in span.attributes["vibe.input"]
    assert "prompt=***" in span.attributes["vibe.input"]
    assert "hello" in span.attributes["vibe.input"]


@pytest.mark.asyncio
async def test_async_decorator_redacts(exporter):
    @vibetrace(component_type="test")
    async def async_login(password):
        return "ok"

    assert await async_login(password="hunter2") == "ok"
    span = _only_span(exporter, "async_login")
    assert "hunter2" not in span.attributes["vibe.input"]
    assert "password=***" in span.attributes["vibe.input"]


# -- config loading ----------------------------------------------------------

def test_load_config_redaction_section(tmp_path):
    cfg_file = tmp_path / "vibedebug.toml"
    cfg_file.write_text(
        '[redaction]\nmax_attr_length = 256\nredact_keys = ["prompt", "session.*"]\n',
        encoding="utf-8",
    )
    config = load_config(str(cfg_file))
    assert config.max_attr_length == 256
    assert config.redact_keys == ["prompt", "session.*"]


def test_load_config_redaction_defaults(tmp_path):
    config = load_config(str(tmp_path / "missing.toml"))
    assert config.max_attr_length == 1024
    assert config.redact_keys == []


# -- sys_tracer previews ------------------------------------------------------

def test_sys_tracer_previews_redacted():
    from types import SimpleNamespace

    from vibe_debug.runtime.sys_tracer import UniversalRuntimeTracer

    tracer = UniversalRuntimeTracer(project_root=".", project_name="test")
    attrs = {}

    class FakeSpan:
        def set_attribute(self, k, v):
            attrs[k] = v

        def set_status(self, status):
            pass

        def end(self):
            pass

    frame = SimpleNamespace(f_locals={"__vibe_start__": 0})
    tracer._on_return(frame, {"api_key": "topsecret", "n": 1}, FakeSpan())
    assert "topsecret" not in attrs["vibe.output_preview"]
    assert "***" in attrs["vibe.output_preview"]
