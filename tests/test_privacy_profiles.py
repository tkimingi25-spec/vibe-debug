"""Privacy profiles and pattern redaction (Stage 4, limitations task).

Default ``permissive`` must keep today's key-name-only behaviour.
``balanced`` adds pattern scanning; ``strict`` also redacts payload
keys. Sentinels are built by concatenation so they stay test-local.
"""

import json

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from vibe_debug.config.config import VibeConfig, load_config
from vibe_debug.decorators import vibetrace
from vibe_debug.explainer import build_evidence_package
from vibe_debug.redaction import redact_patterns, redact_url, redact_value
from vibe_debug.runtime.io_interceptor import _redact_body

BEARER = "Bearer " + "test-token-456"
APIKEY = "sk-" + "testsecret123456"
JWT = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxIn0." + "signaturepart123"
PRIVATE_KEY = (
    "-----BEGIN PRIVATE KEY-----\n" + "FAKEKEYDATA123" + "\n-----END PRIVATE KEY-----"
)
PATTERNS = [BEARER, APIKEY, JWT, PRIVATE_KEY]


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


def _span_attrs(exporter, name):
    spans = [s for s in exporter.get_finished_spans() if s.name == name]
    assert len(spans) == 1
    return dict(spans[0].attributes or {})


@pytest.mark.parametrize("secret", PATTERNS)
def test_patterns_redacted_in_free_text_balanced(secret):
    out = redact_value(f"prefix {secret} suffix", profile="balanced")
    assert secret not in out
    assert "prefix" in out and "suffix" in out


@pytest.mark.parametrize("secret", PATTERNS)
def test_patterns_not_scanned_under_permissive(secret):
    out = redact_value(f"prefix {secret} suffix")
    assert secret in out


def test_patterns_preserve_non_secret_lookalikes():
    # Preserved: incomplete JWT (two parts), short sk- prefix, bare
    # word Bearer with no token, ordinary prose.
    text = "eyJabc.def and sk-short and Bearer and hello world"
    assert redact_patterns(text) == text
    assert redact_value(text, profile="balanced") == text


def test_documented_false_positive_jwt_shaped_example():
    # A JWT-shaped documentation example is indistinguishable from a
    # real JWT; balanced redacts it. Stated false-positive trade-off.
    assert JWT not in redact_value(f"example {JWT}", profile="balanced")


def test_http_body_and_url_patterns_balanced():
    body = _redact_body(f'{{"note": "auth {BEARER}"}}', profile="balanced")
    assert "test-token-456" not in body
    url = redact_url(f"https://h/p?q={APIKEY}&x=ok", profile="balanced")
    assert APIKEY not in url and "x=ok" in url
    # Default profile leaves free-text patterns alone (key-name only).
    assert "test-token-456" in _redact_body(f'{{"note": "auth {BEARER}"}}')


def test_strict_redacts_payload_keys_balanced_does_not():
    data = {"prompt": "hello", "messages": [1], "content": "c", "input": "i", "output": "o", "q": "keep"}
    strict = redact_value(data, profile="strict")
    for gone in ("hello", "'c'"):
        assert gone not in strict or "***" in strict
    assert "keep" in strict
    balanced = redact_value(data, profile="balanced")
    assert "hello" in balanced


def test_invalid_profile_falls_back_to_permissive():
    assert redact_value(f"x {BEARER}", profile="bogus") == f"x {BEARER}"
    assert redact_value("v", key="prompt", profile="bogus") == "v"


def test_config_loader_reads_privacy_profile(tmp_path):
    path = tmp_path / "vibedebug.toml"
    path.write_text('[redaction]\nprivacy_profile = "strict"\n', encoding="utf-8")
    assert load_config(str(path)).privacy_profile == "strict"
    assert VibeConfig().privacy_profile == "permissive"


def test_args_patterns_redacted_through_decorator(exporter):
    cfg = VibeConfig(privacy_profile="balanced")

    @vibetrace("test", config=cfg)
    def variadic(*args):
        return "done"

    assert variadic(f"auth {BEARER}", APIKEY) == "done"
    attrs = _span_attrs(exporter, "variadic")
    blob = json.dumps(attrs, default=str)
    assert "test-token-456" not in blob and APIKEY not in blob
    assert "***" in blob


def test_args_permissive_default_through_decorator(exporter):
    @vibetrace("test")
    def variadic2(*args):
        return "done"

    variadic2(f"auth {BEARER}")
    attrs = _span_attrs(exporter, "variadic2")
    assert "test-token-456" in json.dumps(attrs, default=str)


def test_evidence_package_patterns_balanced():
    span = {
        "name": "route_query",
        "statusCode": "ERROR",
        "latencyMs": 10,
        "attributes": json.dumps(
            {"vibe.input": f"q=auth {BEARER} key {APIKEY}", "error.type": "TimeoutError"}
        ),
        "context": {"spanId": "s1", "traceId": "t1"},
    }
    pkg = build_evidence_package(span, [], privacy_profile="balanced")
    blob = json.dumps(pkg)
    assert "test-token-456" not in blob and APIKEY not in blob
    pkg_default = build_evidence_package(span, [])
    assert "test-token-456" in json.dumps(pkg_default)
