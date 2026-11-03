"""Redact and truncate values before they become span attributes.

Default profile ``permissive`` is key-name-based only: no content
scanning. Positional arguments matched to declared parameter names
via ``inspect.signature`` ARE redacted by that parameter name;
``*args`` and unknown-signature positionals are NOT under the default
profile. Secrets embedded inside free-text values under
non-sensitive keys (e.g. "use key sk-..." inside a ``query`` string)
are not detected under ``permissive``. ``prompt`` and similar payload
fields are not redacted by default; add them via ``redact_keys`` /
``VibeConfig``, or select a privacy profile:

- ``permissive``: current behaviour (key-name matching only);
- ``balanced``: permissive plus deterministic pattern scanning for
  Bearer tokens, private-key blocks, JWTs, and common API-key
  prefixes in the rendered text;
- ``strict``: balanced plus payload keys (``prompt``, ``messages``,
  ``content``, ``input``, ``output``) redacted by key name.

Patterns can false-positive on lookalike non-secret strings (e.g. a
JWT-shaped example or an ``sk-``-prefixed identifier); that is the
documented trade-off of ``balanced``/``strict``.

Fail-closed for privacy, fail-safe for execution: this module never
raises, never invokes user ``__eq__``, and on any stringification
failure returns a safe placeholder instead of the raw value.
"""

import re
from urllib.parse import unquote_plus, urlparse, urlunparse

MAX_DEPTH = 10
TRUNCATION_MARKER = "…[truncated]"
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

PRIVACY_PROFILES = ("permissive", "balanced", "strict")
STRICT_PAYLOAD_KEYS = ("prompt", "messages", "content", "input", "output")

_PATTERN_SUBS = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        REDACTED,
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}"), "Bearer ***"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        REDACTED,
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{8,}|gho_[A-Za-z0-9]{8,}"
            r"|github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{16}"
            r"|xox[baprs]-[A-Za-z0-9-]{8,})\b"
        ),
        REDACTED,
    ),
)


def normalize_profile(profile):
    """Return a known privacy profile; unknown values fall back safely."""
    try:
        value = str(profile or "permissive").strip().lower()
    except Exception:
        return "permissive"
    return value if value in PRIVACY_PROFILES else "permissive"


def _effective_extra_keys(extra_keys, profile):
    keys = list(extra_keys or ())
    if normalize_profile(profile) == "strict":
        keys.extend(STRICT_PAYLOAD_KEYS)
    return tuple(keys)


def redact_patterns(text):
    """Redact recognisable secret patterns in text; never raises."""
    try:
        if not isinstance(text, str):
            text = str(text)
        for pattern, replacement in _PATTERN_SUBS:
            text = pattern.sub(replacement, text)
        return text
    except Exception:
        return "<unrepresentable>"


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


def _redact_query_like(text, extra_keys=()):
    """Redact sensitive values in a ``k=v&k2=v2`` string, preserving the rest."""
    if not text:
        return text
    parts = []
    for pair in text.split("&"):
        if "=" in pair:
            raw_key, raw_value = pair.split("=", 1)
            if _is_sensitive_key(unquote_plus(raw_key), extra_keys):
                parts.append(f"{raw_key}={REDACTED}")
            else:
                parts.append(pair)
        else:
            parts.append(pair)
    return "&".join(parts)


def redact_url(url, *, extra_keys=(), max_length=500, profile="permissive"):
    """Return a redacted, truncated URL string; never raises, never leaks.

    Query-string values whose parameter name is sensitive (per the same
    key logic as :func:`redact_value`) are replaced with ``***``;
    non-sensitive parameters are preserved verbatim. A password in the
    URL authority (``https://user:pass@host/``) is replaced with ``***``.
    The fragment is treated as a query-like ``k=v`` string and redacted
    the same way, because fragments can carry tokens (e.g. OAuth
    implicit flow); bare non-``k=v`` fragment text is left as-is.
    """
    try:
        try:
            text = url if isinstance(url, str) else str(url)
        except Exception:
            return _truncate("<unrepresentable>", max_length)
        parsed = urlparse(text)
        netloc = parsed.netloc
        if "@" in netloc:
            userinfo, hostport = netloc.rsplit("@", 1)
            if ":" in userinfo:
                username = userinfo.split(":", 1)[0]
                netloc = f"{username}:{REDACTED}@{hostport}"
        effective = _effective_extra_keys(extra_keys, profile)
        rebuilt = urlunparse(
            parsed._replace(
                netloc=netloc,
                query=_redact_query_like(parsed.query, effective),
                fragment=_redact_query_like(parsed.fragment, effective),
            )
        )
        if normalize_profile(profile) != "permissive":
            rebuilt = redact_patterns(rebuilt)
        return _truncate(rebuilt, max_length)
    except Exception:
        return _truncate("<unrepresentable>", max_length)


def redact_value(value, key=None, *, extra_keys=(), max_length=1024, profile="permissive"):
    """Return a safe string representation of value for a span attribute."""
    try:
        effective = _effective_extra_keys(extra_keys, profile)
        if _is_sensitive_key(key, effective):
            return REDACTED
        walked = _walk(value, key, effective, 0, frozenset())
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
        if normalize_profile(profile) != "permissive":
            text = redact_patterns(text)
        return _truncate(text, max_length)
    except Exception:
        try:
            return _truncate(
                f"<unrepresentable: {type(value).__name__}>", max_length
            )
        except Exception:
            return "<unrepresentable>"
