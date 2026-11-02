"""Optional Muse Spark error explanations (English / Swahili).

A thin, optional layer over Vibe-Debug's existing deterministic
explanations. It builds a small, redacted evidence package from span
data (Improvement 1 redaction applied to every user-controlled value),
optionally sends that one package to a configured provider, validates
the structured response, and falls back to the deterministic
explanation whenever the provider is unavailable, unconfigured, slow,
or invalid.

Provider contract (verified from official Meta documentation,
https://dev.meta.ai/docs): ``POST https://api.meta.ai/v1/chat/completions``
with ``Authorization: Bearer <key>``, an OpenAI-style
``{model, messages}`` request, and a
``choices[0].message.content`` response. The flagship Muse Spark
model is ``muse-spark-1.3``. The HTTP transport sits behind one small
injectable client callable so provider details never spread into
CLI/MCP code.

The API key is read only from the ``MUSE_SPARK_API_KEY`` environment
variable (Meta's docs name the same credential ``MODEL_API_KEY``; the
auth format is what is verified). It is never stored in config files,
logged, printed, or returned.
"""

import copy
import json
import os
import time

import httpx

from vibe_debug.redaction import redact_value

API_KEY_ENV_VAR = "MUSE_SPARK_API_KEY"

# Verified against official Meta documentation (dev.meta.ai):
# flagship Muse Spark model + Chat Completions endpoint.
DEFAULT_MODEL = "muse-spark-1.3"
DEFAULT_ENDPOINT = "https://api.meta.ai/v1/chat/completions"

_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "FailureExplanation",
        "schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "likely_cause": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "string"}},
                "suggested_fix": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "missing_information": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "summary",
                "likely_cause",
                "evidence",
                "suggested_fix",
                "confidence",
                "missing_information",
            ],
            "additionalProperties": False,
        },
    },
}

_LANGUAGE_ALIASES = {
    "en": "en",
    "english": "en",
    "sw": "sw",
    "swahili": "sw",
    "kiswahili": "sw",
}

_CONFIDENCE_VALUES = ("low", "medium", "high")

SYSTEM_PROMPT = """You are explaining a software failure from supplied trace evidence only.

Rules:
- Use only the supplied evidence. Do not invent files, settings, causes, or events.
- Separate observed facts from likely causes.
- Cite relevant span names or evidence fields.
- State when the evidence is insufficient.
- Label fixes as suggested checks unless directly established by evidence.
- Preserve code identifiers, exception names, file paths, JSON keys, commands, and configuration names exactly.
- Respond in the requested language. For Swahili (sw), use clear standard Swahili while retaining technical terms where translation would reduce precision.
- Return ONLY a JSON object with exactly these fields:
  {"summary": "string", "likely_cause": "string", "evidence": ["string"], "suggested_fix": ["string"], "confidence": "low | medium | high", "missing_information": ["string"]}
"""


# ---------------------------------------------------------------------------
# Span helpers
# ---------------------------------------------------------------------------


def parse_span_attributes(span):
    """Tolerantly parse a span's attributes (dict or JSON string). Never raises."""
    try:
        if not isinstance(span, dict):
            return {}
        attrs = span.get("attributes")
        if isinstance(attrs, dict):
            return dict(attrs)
        if isinstance(attrs, str):
            parsed = json.loads(attrs) if attrs.strip() else {}
            return parsed if isinstance(parsed, dict) else {}
        return {}
    except Exception:
        return {}


def select_failure_span(spans):
    """Return the first ERROR span (fetch order is newest-first), or None."""
    try:
        for span in spans or []:
            if isinstance(span, dict) and span.get("statusCode") == "ERROR":
                return span
    except Exception:
        return None
    return None


def _is_silent_failure(span, attrs):
    try:
        if not isinstance(span, dict) or span.get("statusCode") != "ERROR":
            return False
        if attrs.get("error.type"):
            return False
        output = attrs.get("vibe.output", attrs.get("vibe.output_preview", ""))
        return str(output) in ("None", "[]", "{}", "''", '""', "")
    except Exception:
        return False


def _related_spans(span, all_spans, limit=5):
    """Directly relevant spans: same trace first, else following spans.

    Deterministic: preserves the (newest-first) fetch order, excludes
    the selected span, bounded to ``limit``.
    """
    try:
        if not isinstance(span, dict):
            return []
        selected_span_id = None
        if isinstance(span.get("context"), dict):
            selected_span_id = span["context"].get("spanId")
        candidates = []
        for s in (all_spans or []):
            if not isinstance(s, dict) or s is span:
                continue
            if selected_span_id and isinstance(s.get("context"), dict) and s["context"].get("spanId") == selected_span_id:
                continue
            candidates.append(s)
        trace_id = ((span.get("context") or {}).get("traceId")) if isinstance(span.get("context"), dict) else None
        if trace_id:
            same_trace = [
                s for s in candidates
                if isinstance(s.get("context"), dict)
                and s["context"].get("traceId") == trace_id
            ]
            if same_trace:
                return same_trace[:limit]
        return candidates[:limit]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Stage 1: deterministic evidence package
# ---------------------------------------------------------------------------


def build_evidence_package(
    span,
    related_spans=(),
    *,
    max_evidence_size=8000,
    max_related=5,
    redact_keys=(),
    max_attr_length=1024,
):
    """Build a small, redacted, JSON-serializable evidence package.

    Every user-controlled value passes through Improvement 1 redaction
    before inclusion. Never raises; incomplete evidence yields a
    smaller valid package. The input span is not mutated.
    """
    try:
        source = copy.deepcopy(span) if isinstance(span, dict) else {}
        attrs = parse_span_attributes(source)
        safe_attrs = {}
        for key, value in attrs.items():
            if isinstance(value, (str, dict, list, tuple, set)):
                safe_attrs[key] = redact_value(
                    value, key=key, extra_keys=redact_keys, max_length=max_attr_length
                )
            else:
                safe_attrs[key] = value

        def preview(*keys):
            for key in keys:
                value = safe_attrs.get(key)
                if value in (None, ""):
                    continue
                if isinstance(value, str):
                    try:
                        parsed = json.loads(value)
                    except Exception:
                        parsed = None
                    if isinstance(parsed, (dict, list)):
                        # Structured preview: redact nested values by key.
                        return redact_value(
                            parsed, extra_keys=redact_keys, max_length=max_attr_length
                        )
                return redact_value(value, max_length=max_attr_length)
            return None

        duration = source.get("latencyMs")
        if duration is None:
            duration = safe_attrs.get("execution_time_ms")
        try:
            duration_ms = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_ms = None

        error_message = None
        for key in ("error.message", "exception.message"):
            if safe_attrs.get(key):
                error_message = redact_value(safe_attrs[key], max_length=max_attr_length)
                break

        related = []
        for rel in _related_spans(source, list(related_spans or []), limit=max_related):
            rel_attrs = parse_span_attributes(rel)
            related.append(
                {
                    "name": str(rel.get("name", "unknown")),
                    "status": str(rel.get("statusCode", "unknown")),
                    "duration_ms": rel.get("latencyMs"),
                }
            )
            _ = rel_attrs

        package = {
            "function": str(source.get("name", "unknown")),
            "status": str(source.get("statusCode", "unknown")),
            "error_type": safe_attrs.get("error.type") or None,
            "error_message": error_message,
            "duration_ms": duration_ms,
            "file": safe_attrs.get("vibe.file"),
            "line": safe_attrs.get("vibe.line"),
            "module": safe_attrs.get("vibe.module"),
            "input_preview": preview("vibe.input", "vibe.input_preview"),
            "output_preview": preview("vibe.output", "vibe.output_preview"),
            "silent_failure": _is_silent_failure(source, attrs),
            "related_spans": related,
        }
        return _bound_package(package, max_evidence_size)
    except Exception:
        return {
            "function": "unknown",
            "status": "unknown",
            "error_type": None,
            "error_message": None,
            "duration_ms": None,
            "file": None,
            "line": None,
            "module": None,
            "input_preview": None,
            "output_preview": None,
            "silent_failure": False,
            "related_spans": [],
        }


def _bound_package(package, max_evidence_size):
    """Shrink a package until its JSON serialization fits the bound."""
    try:
        limit = int(max_evidence_size) if max_evidence_size else 8000
    except (TypeError, ValueError):
        limit = 8000

    def size(pkg):
        return len(json.dumps(pkg, ensure_ascii=False, default=str))

    pkg = dict(package)
    while size(pkg) > limit and pkg.get("related_spans"):
        pkg["related_spans"] = pkg["related_spans"][:-1]
    for key in ("input_preview", "output_preview", "error_message"):
        if size(pkg) <= limit:
            break
        if isinstance(pkg.get(key), str) and len(pkg[key]) > 120:
            pkg[key] = redact_value(pkg[key], max_length=120)
    if size(pkg) > limit:
        for key in ("input_preview", "output_preview", "error_message"):
            if isinstance(pkg.get(key), str):
                pkg[key] = redact_value(pkg[key], max_length=40)
    return pkg


# ---------------------------------------------------------------------------
# Stage 2: language, validation, provider, fallback
# ---------------------------------------------------------------------------


def normalize_language(value):
    """Map a language name/code to canonical 'en'/'sw', or None."""
    if value is None:
        return None
    return _LANGUAGE_ALIASES.get(str(value).strip().lower())


def _config_value(config, key, default=None):
    if config is None:
        return default
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _deterministic_text(evidence, language="en"):
    ev = evidence or {}
    lines = [
        f"Component: {ev.get('function', 'unknown')}",
        f"Status: {ev.get('status', 'unknown')}",
        f"Duration: {ev.get('duration_ms', 'unknown')}ms",
        f"Silent failure: {bool(ev.get('silent_failure'))}",
    ]
    if ev.get("error_type"):
        lines.append(f"Error type: {ev['error_type']}")
    if ev.get("error_message"):
        lines.append(f"Error: {ev['error_message']}")
    return "\n".join(lines)


def validate_explanation(payload):
    """Return a validated structured explanation dict, or None."""
    try:
        if not isinstance(payload, dict):
            return None
        summary = payload.get("summary")
        likely_cause = payload.get("likely_cause")
        evidence_list = payload.get("evidence")
        fixes = payload.get("suggested_fix")
        confidence = payload.get("confidence")
        missing = payload.get("missing_information", [])
        if not isinstance(summary, str) or not summary.strip():
            return None
        if not isinstance(likely_cause, str) or not likely_cause.strip():
            return None
        if not isinstance(evidence_list, list) or not all(
            isinstance(item, str) for item in evidence_list
        ):
            return None
        if not isinstance(fixes, list) or not all(
            isinstance(item, str) for item in fixes
        ):
            return None
        if confidence not in _CONFIDENCE_VALUES:
            return None
        if not isinstance(missing, list) or not all(
            isinstance(item, str) for item in missing
        ):
            return None
        return {
            "summary": summary,
            "likely_cause": likely_cause,
            "evidence": list(evidence_list),
            "suggested_fix": list(fixes),
            "confidence": confidence,
            "missing_information": list(missing),
        }
    except Exception:
        return None


def render_explanation(structured, language="en"):
    """Render a validated structured explanation. Values pass through verbatim."""
    s = structured or {}
    labels = (
        {
            "summary": "Summary",
            "cause": "Likely cause",
            "evidence": "Evidence",
            "fixes": "Suggested checks",
            "confidence": "Confidence",
            "missing": "Missing information",
        }
        if language != "sw"
        else {
            "summary": "Muhtasari",
            "cause": "Sababu inayowezekana",
            "evidence": "Ushahidi",
            "fixes": "Hatua zinazopendekezwa",
            "confidence": "Kiwango cha uhakika",
            "missing": "Taarifa zinazokosekana",
        }
    )
    lines = [f"{labels['summary']}: {s.get('summary', '')}"]
    lines.append(f"{labels['cause']}: {s.get('likely_cause', '')}")
    lines.append(f"{labels['evidence']}:")
    lines.extend(f"- {item}" for item in s.get("evidence", []))
    lines.append(f"{labels['fixes']}:")
    lines.extend(f"- {item}" for item in s.get("suggested_fix", []))
    lines.append(f"{labels['confidence']}: {s.get('confidence', '')}")
    lines.append(f"{labels['missing']}:")
    lines.extend(f"- {item}" for item in s.get("missing_information", []))
    return "\n".join(lines)


def _extract_structured(payload):
    """Extract + validate the structured object from a provider payload."""
    if isinstance(payload, dict) and "summary" in payload:
        return validate_explanation(payload)
    text = None
    if isinstance(payload, dict):
        try:
            choices = payload.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                text = message.get("content")
            text = text or payload.get("content") or payload.get("text")
        except Exception:
            text = None
    if isinstance(text, list):
        text = "".join(
            part.get("text", "") for part in text if isinstance(part, dict)
        )
    if isinstance(text, str):
        try:
            return validate_explanation(json.loads(text))
        except Exception:
            return None
    return None


def _default_http_client(config):
    """Build the live client callable, or None if not fully configured.

    Verified Meta Model API contract (dev.meta.ai): POST to the Chat
    Completions endpoint with a Bearer key, ``{model, messages}``
    payload, and ``response_format`` JSON-schema structured output.
    Endpoint/model default to the verified flagship values; the
    credential must come from the environment.
    """
    endpoint = _config_value(config, "explainer_endpoint") or DEFAULT_ENDPOINT
    model = _config_value(config, "explainer_model") or DEFAULT_MODEL
    api_key = os.getenv(API_KEY_ENV_VAR)
    if not api_key:
        return None
    if endpoint.rstrip("/").endswith("/v1"):
        endpoint = endpoint.rstrip("/") + "/chat/completions"
    try:
        timeout = float(_config_value(config, "explainer_timeout_seconds", 10.0))
    except (TypeError, ValueError):
        timeout = 10.0

    def call(request):
        payload = {
            "model": model,
            "messages": [
                {"role": "developer", "content": request["system"]},
                {"role": "user", "content": request["user"]},
            ],
            "temperature": 0.1,
            "response_format": _RESPONSE_FORMAT,
        }
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                endpoint,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        response.raise_for_status()
        return response.json()

    return call


def explain_failure(
    evidence,
    *,
    language=None,
    question_language=None,
    client=None,
    config=None,
    fallback_text=None,
):
    """Explain a failure with the optional provider, else fall back.

    Returns a dict: ``{language, source, structured, text, error}``
    where ``source`` is ``"llm"`` or ``"fallback"``. Never raises and
    never includes the API key anywhere. The provider client is called
    at most once.
    """
    fallback = fallback_text if fallback_text is not None else _deterministic_text(evidence)
    try:
        explicit = language
        canonical = (
            normalize_language(explicit)
            or normalize_language(question_language)
            or normalize_language(_config_value(config, "explainer_language"))
            or "en"
        )
        if explicit is not None and normalize_language(explicit) is None:
            return {
                "language": canonical,
                "source": "fallback",
                "structured": None,
                "text": fallback,
                "error": f"unsupported language: {explicit}",
            }

        enabled = bool(_config_value(config, "explainer_enabled", False))
        call = client
        if call is not None and config is not None and not enabled and explicit is None:
            # Disabled configuration means no API call is attempted,
            # unless the caller explicitly requested a language.
            call = None
        if call is None and enabled:
            call = _default_http_client(config)
        if call is None:
            return {
                "language": canonical,
                "source": "fallback",
                "structured": None,
                "text": fallback,
                "error": None,
            }

        user = json.dumps(
            {"language": canonical, "evidence": evidence},
            ensure_ascii=False,
            default=str,
        )
        request = {"system": SYSTEM_PROMPT, "user": user, "language": canonical, "evidence": evidence}
        started = time.monotonic()
        payload = call(request)
        _ = started
        structured = _extract_structured(payload)
        if structured is None:
            return {
                "language": canonical,
                "source": "fallback",
                "structured": None,
                "text": fallback,
                "error": "invalid provider response",
            }
        return {
            "language": canonical,
            "source": "llm",
            "structured": structured,
            "text": render_explanation(structured, canonical),
            "error": None,
        }
    except Exception:
        return {
            "language": normalize_language(language) or "en",
            "source": "fallback",
            "structured": None,
            "text": fallback,
            "error": None,
        }
