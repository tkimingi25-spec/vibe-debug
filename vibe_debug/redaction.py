"""Redact and truncate values before they become span attributes.

Key-name-based redaction only: no content scanning. Secrets passed as
bare positional strings, or embedded inside free-text values under
non-sensitive keys (e.g. "use key sk-..." inside a ``query`` string),
are not detected. ``prompt`` and similar payload fields are not
redacted by default; add them via ``redact_keys`` / ``VibeConfig``.

Fail-closed for privacy, fail-safe for execution: this module never
raises, never invokes user ``__eq__``, and on any stringification
failure returns a safe placeholder instead of the raw value.
"""

import re

MAX_DEPTH = 10
TRUNCATION_MARKER = "[truncated]"
REDACTED = "***"

# Compact forms (lowercase, non-alphanumerics removed) matched whole-key
# only, so token_count / secret_count are NOT redacted.
_SENSITIVE_COMPACT = frozenset(
    {
        "apikey",
        "accesstoken",
        "authtoken",
        "token",
        "clientsecret",
        "secret",
        "password",
        "passwd",
        "authorization",
        "privatekey",
        "cookie",
        "setcookie",
    }
)

_SENSITIVE_PAIRS = frozenset(
    {
        ("api", "key"),
        ("access", "token"),
        ("auth", "token"),
        ("client", "secret"),
        ("private", "key"),
        ("set", "cookie"),
    }
)

_SENSITIVE_LAST_SEGMENT = frozenset(
    {"password", "passwd", "authorization", "cookie"}
)


def _compact(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_sensitive_key(key, extra_keys=()):
    try:
        if key is None:
            return False
        try:
            raw = str(key)
        except Exception:
            return False
        lower = raw.strip().lower()
        if not lower:
            return False
        compact = _compact(lower)
        for extra in extra_keys or ():
            try:
                extra_text = str(extra).strip().lower()
            except Exception:
                continue
            if not extra_text:
                continue
            if lower == extra_text or compact == _compact(extra_text):
                return True
            # Extra entries may be regex patterns matched on the raw key.
            try:
                if re.search(extra_text, raw, flags=re.IGNORECASE):
                    return True
            except re.error:
                pass
        if compact in _SENSITIVE_COMPACT:
            return True
        segments = [s for s in re.split(r"[^a-z0-9]+", lower) if s]
        if any(
            (segments[i], segments[i + 1]) in _SENSITIVE_PAIRS
            for i in range(len(segments) - 1)
        ):
            return True
        return bool(segments) and segments[-1] in _SENSITIVE_LAST_SEGMENT
    except Exception:
        return False


def _walk(value, key, extra_keys, depth, seen):
    if _is_sensitive_key(key, extra_keys):
        return REDACTED
    if depth > MAX_DEPTH:
        return "<max-depth>"
    if isinstance(value, dict):
        marker = id(value)
        if marker in seen:
            return "<cycle>"
        next_seen = seen | {marker}
        return {
            entry_key: _walk(
                entry_value, entry_key, extra_keys, depth + 1, next_seen
            )
            for entry_key, entry_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        marker = id(value)
        if marker in seen:
            return "<cycle>"
        next_seen = seen | {marker}
        items = [
            _walk(item, None, extra_keys, depth + 1, next_seen)
            for item in value
        ]
        if isinstance(value, tuple):
            return tuple(items)
        if isinstance(value, (set, frozenset)):
            try:
                return type(value)(items)
            except Exception:
                return items
        return items
    return value


def _truncate(text, max_length):
    try:
        limit = int(max_length)
    except Exception:
        limit = 1024
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(TRUNCATION_MARKER):
        return TRUNCATION_MARKER[:limit]
    return text[: limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER


def redact_value(value, key=None, *, extra_keys=(), max_length=1024):
    """Return a safe string representation of value for a span attribute."""
    try:
        if _is_sensitive_key(key, extra_keys):
            return REDACTED
        walked = _walk(value, key, extra_keys, 0, frozenset())
        if isinstance(walked, str):
            text = walked
        else:
            try:
                text = str(walked)
            except Exception:
                return _truncate(
                    f"<unrepresentable: {type(value).__name__}>",
                    max_length,
                )
        if not isinstance(text, str):
            text = str(type(value).__name__)
        return _truncate(text, max_length)
    except Exception:
        try:
            return _truncate(
                f"<unrepresentable: {type(value).__name__}>", max_length
            )
        except Exception:
            return "<unrepresentable>"
