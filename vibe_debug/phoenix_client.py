"""Shared Phoenix GraphQL span-fetch (Improvement 6).

One implementation of the Phoenix span fetch-and-sort, consumed by
both the CLI and the MCP bridge. Dependency-light: stdlib + ``httpx``
+ ``vibe_debug.phoenix_endpoint`` only. It must NOT import from
``vibe_debug.cli`` or ``server/`` (no import cycles). Endpoint
resolution (Improvement 4) and loopback-safe transport
(Improvement 3) come from ``vibe_debug.phoenix_endpoint``.
"""

import httpx

from vibe_debug.phoenix_endpoint import make_client, resolve_base_url

_CONTEXT_FIELDS = "context { spanId traceId }"


def _span_query(limit, include_context):
    extra = f"\n                  {_CONTEXT_FIELDS}" if include_context else ""
    return f"""
    {{
      projects {{
        edges {{
          node {{
            spans(first: {limit}, sort: {{col: startTime, dir: desc}}) {{
              edges {{
                node {{
                  name statusCode statusMessage latencyMs
                  startTime attributes
                  events {{ name timestamp attributes }}{extra}
                }}
              }}
            }}
          }}
        }}
      }}
    }}
    """


def _post_graphql(query, *, base_url, config, timeout, client_factory=None):
    resolved = resolve_base_url(base_url, config=config)
    factory = client_factory or make_client
    with factory(resolved, timeout=timeout) as client:
        response = client.post("/graphql", json={"query": query})
    response.raise_for_status()
    return response.json()


def query_graphql(
    query, *, base_url=None, config=None, timeout=10.0, client_factory=None
):
    """Run an arbitrary Phoenix GraphQL query.

    Returns the raw JSON payload, or ``{"error": ...}`` on any failure
    (this is the MCP bridge's historical ``query_phoenix`` behaviour).
    ``client_factory`` defaults to ``make_client``; callers pass their
    module-level ``make_client`` so existing client-construction seams
    (and tests patching them) keep working.
    """
    try:
        return _post_graphql(
            query,
            base_url=base_url,
            config=config,
            timeout=timeout,
            client_factory=client_factory,
        )
    except Exception as e:
        return {"error": f"Could not connect to Phoenix GraphQL: {str(e)}"}


def fetch_spans(
    limit=20,
    *,
    base_url=None,
    config=None,
    timeout=10.0,
    include_context=False,
    client_factory=None,
    wait_seconds=0,
    poll_interval_seconds=0.5,
):
    """Fetch spans from Phoenix via GraphQL and return them sorted.

    Resolves the base URL via ``resolve_base_url`` (Improvement 4) and
    uses ``make_client`` (Improvement 3) for transport. Returns a list
    of span node dicts sorted by ``startTime`` descending (stable),
    truncated to ``limit``. Returns ``[]`` on HTTP errors, GraphQL
    error payloads, and malformed response structures
    (``KeyError``/``TypeError``/``ValueError``); callers that
    historically swallowed broader exceptions wrap this accordingly.
    """
    # Bounded polling for Phoenix ingestion lag: only re-runs the
    # existing single fetch; semantics, sorting, and shapes unchanged.
    import time as _time

    deadline = _time.monotonic() + max(0.0, float(wait_seconds or 0))
    while True:
        result = _fetch_spans_once(
            limit,
            base_url=base_url,
            config=config,
            timeout=timeout,
            include_context=include_context,
            client_factory=client_factory,
        )
        if result:
            return result
        remaining = deadline - _time.monotonic()
        if remaining <= 0:
            return result
        _time.sleep(min(max(0.05, float(poll_interval_seconds or 0.5)), remaining))


def _fetch_spans_once(
    limit,
    *,
    base_url=None,
    config=None,
    timeout=10.0,
    include_context=False,
    client_factory=None,
):
    query = _span_query(limit, include_context)
    try:
        data = _post_graphql(
            query,
            base_url=base_url,
            config=config,
            timeout=timeout,
            client_factory=client_factory,
        )
        if "error" in data:
            return []
        spans = []
        for project in data["data"]["projects"]["edges"]:
            spans.extend(
                edge["node"] for edge in project["node"]["spans"]["edges"]
            )
        return sorted(
            spans, key=lambda span: span.get("startTime", ""), reverse=True
        )[:limit]
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return []
