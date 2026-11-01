"""Single Phoenix endpoint resolver and loopback-safe HTTP transport.

Improvement 4 (resolver) + Improvement 3 (transport), kept in one
lightweight module (stdlib + ``httpx`` only, no ``vibe_debug`` imports)
so the CLI, the MCP bridge, and the server manager can all import it
without circular-import risk. Improvement 6 (shared span-fetch) can
reuse both helpers.

Resolver precedence, highest first:

1. Explicit argument.
2. Environment: ``PHOENIX_HOST`` / ``PHOENIX_PORT``, interpreted as
   ``server/manager.py`` has always interpreted them (bare host,
   default ``127.0.0.1``; bare port, default ``6006``, passed to the
   Phoenix child process). For robustness a full URL or ``host:port``
   value in ``PHOENIX_HOST`` is also accepted; ``PHOENIX_PORT``, when
   set, overrides any port embedded in ``PHOENIX_HOST``.
3. Config-derived: the configured ``otlp_endpoint`` parsed down to
   scheme + host + port (path stripped, never appended to).
4. Default: ``http://localhost:6006``.

Malformed values never raise: the resolver falls back to the next
precedence level and logs the bad value at debug level. A missing port
keeps the scheme-default port implicit (no port is invented). The
return value is a clean base URL: no trailing slash, no path, no
userinfo.
"""

import ipaddress
import logging
import os
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PHOENIX_BASE_URL = "http://localhost:6006"
_DEFAULT_ENV_HOST = "127.0.0.1"
_DEFAULT_ENV_PORT = 6006


def _clean_base_url(value):
    """Return a clean ``scheme://host[:port]`` base URL, or None if malformed."""
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        if "://" not in text:
            text = f"http://{text}"
        parsed = urlparse(text)
        scheme = (parsed.scheme or "http").lower()
        if scheme not in ("http", "https"):
            return None
        host = parsed.hostname
        if not host:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        host_display = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"{scheme}://{host_display}" + (f":{port}" if port else "")
    except Exception:
        return None


def _with_port(base_url, port):
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    host_display = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{host_display}:{port}"


def _env_base_url():
    """Build a base URL from PHOENIX_HOST/PHOENIX_PORT, manager-style."""
    host_env = os.getenv("PHOENIX_HOST")
    port_env = os.getenv("PHOENIX_PORT")
    if host_env is None and port_env is None:
        return None
    cleaned = _clean_base_url(host_env.strip() if host_env else _DEFAULT_ENV_HOST)
    if cleaned is None:
        logger.debug("Ignoring malformed PHOENIX_HOST value: %r", host_env)
        return None
    if port_env is not None and str(port_env).strip() != "":
        try:
            port = int(str(port_env).strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except (TypeError, ValueError):
            logger.debug("Ignoring malformed PHOENIX_PORT value: %r", port_env)
            return None
        return _with_port(cleaned, port)
    if urlparse(cleaned).port is None:
        return _with_port(cleaned, _DEFAULT_ENV_PORT)
    return cleaned


def _config_otlp_endpoint(config):
    if config is None:
        return None
    if isinstance(config, str):
        return config
    if isinstance(config, dict):
        return config.get("otlp_endpoint")
    return getattr(config, "otlp_endpoint", None)


def resolve_base_url(explicit=None, *, config=None):
    """Return the Phoenix query base URL, e.g. 'http://localhost:6006'.

    Precedence: explicit > PHOENIX_HOST/PHOENIX_PORT env > config
    ``otlp_endpoint`` (path stripped) > default. Malformed values fall
    back to the next level; this function never raises.
    """
    candidates = (
        ("explicit", explicit),
        ("environment", _env_base_url()),
        ("config", _config_otlp_endpoint(config)),
    )
    for source, candidate in candidates:
        if candidate is None:
            continue
        cleaned = _clean_base_url(candidate)
        if cleaned is not None:
            logger.debug(
                "Resolved Phoenix base URL %s (source: %s)", cleaned, source
            )
            return cleaned
        logger.debug(
            "Ignoring malformed Phoenix endpoint from %s: %r", source, candidate
        )
    logger.debug(
        "Resolved Phoenix base URL %s (source: default)", DEFAULT_PHOENIX_BASE_URL
    )
    return DEFAULT_PHOENIX_BASE_URL


def _is_loopback_host(host) -> bool:
    """True for localhost / 127.0.0.0/8 / ::1 (case-insensitive); never raises."""
    try:
        if host is None:
            return False
        text = str(host).strip().lower()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1]
        if text == "localhost":
            return True
        try:
            return ipaddress.ip_address(text).is_loopback
        except ValueError:
            return False
    except Exception:
        return False


def _trust_env_for(base_url) -> bool:
    """Proxy env vars are honoured only for non-loopback targets."""
    try:
        host = urlparse(str(base_url)).hostname
    except Exception:
        host = None
    return not _is_loopback_host(host)


def trust_env_for(base_url) -> bool:
    """Public wrapper: should an httpx call to ``base_url`` honour proxy env?"""
    return _trust_env_for(base_url)


def make_client(base_url, **kwargs):
    """Return an ``httpx.Client`` for Phoenix at ``base_url``.

    Loopback targets get ``trust_env=False`` so ``HTTP_PROXY`` /
    ``HTTPS_PROXY`` are ignored for localhost calls; remote targets
    keep the default ``trust_env=True``. A caller-supplied
    ``trust_env`` is respected. Proxy env vars are never mutated.
    """
    kwargs.setdefault("trust_env", _trust_env_for(base_url))
    kwargs.setdefault("base_url", str(base_url))
    return httpx.Client(**kwargs)


def make_async_client(base_url, **kwargs):
    """Async variant of :func:`make_client` (same loopback rule)."""
    kwargs.setdefault("trust_env", _trust_env_for(base_url))
    kwargs.setdefault("base_url", str(base_url))
    return httpx.AsyncClient(**kwargs)
