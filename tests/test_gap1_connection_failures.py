import httpx
import json
import socket
import pytest
from vibe_debug.tracer import init_vibedebug
from vibe_debug.runtime.io_interceptor import IOInterceptor

PROJECT_ROOT = "C:\\Users\\hp\\Desktop\\vibe-debug"

QUERY = """
{ projects { edges { node {
    spans(first: 30, sort: {col: startTime, dir: desc}) {
        edges { node { name statusCode attributes } }
    }
} } } }
"""


def flatten_attrs(attrs: dict, prefix="") -> dict:
    flat = {}
    for key, value in attrs.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(flatten_attrs(value, full_key))
        else:
            flat[full_key] = value
    return flat


def test_tcp_connection_failure_captured_as_error_span():
    """
    When any TCP connection is refused - to any service,
    any port, any protocol - the failure must appear as
    an ERROR span in Phoenix with the host, port, and
    error type captured.
    This is universal - works for Neo4j, MongoDB, Redis,
    PostgreSQL, any TCP service, without any adapter.
    """
    init_vibedebug("gap1-test")
    interceptor = IOInterceptor(project_name="gap1-test")
    interceptor.start()

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("localhost", 19999))
    except (ConnectionRefusedError, OSError):
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    interceptor.stop()

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    r.raise_for_status()
    spans = (
        r.json()["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    tcp_spans = [
        e["node"] for e in spans
        if "tcp.connect" in e["node"]["name"]
    ]

    assert len(tcp_spans) > 0, \
        "No tcp.connect span found - connection failure not captured"

    failed_tcp = [
        s for s in tcp_spans
        if s["statusCode"] == "ERROR"
    ]

    assert len(failed_tcp) > 0, (
        "GAP 1: tcp.connect span exists but connection failure "
        "is not marked as ERROR - need to capture socket errors"
    )

    attrs = flatten_attrs(
        json.loads(failed_tcp[0].get("attributes", "{}"))
    )
    assert attrs.get("error.type"), \
        "GAP 1: error.type not captured on failed connection"
    assert attrs.get("tcp.port"), \
        "GAP 1: port not captured on tcp.connect span"

    print("Connection failure captured as ERROR span")
    print(f"   host: {attrs.get('tcp.host')}")
    print(f"   port: {attrs.get('tcp.port')}")
    print(f"   error: {attrs.get('error.type')}")


def test_connection_failure_shows_which_service_was_unreachable():
    """
    The span must tell us WHICH service was unreachable
    so the IDE can report "Neo4j at localhost:7687 is down"
    instead of just "a connection failed".
    """
    init_vibedebug("gap1-service-test")
    interceptor = IOInterceptor(project_name="gap1-service-test")
    interceptor.start()

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("localhost", 7687))
    except (ConnectionRefusedError, OSError):
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("localhost", 27017))
    except (ConnectionRefusedError, OSError):
        pass
    finally:
        try:
            s.close()
        except Exception:
            pass

    interceptor.stop()

    r = httpx.post(
        "http://localhost:6006/graphql",
        json={"query": QUERY},
        timeout=10.0,
    )
    r.raise_for_status()
    spans = (
        r.json()["data"]["projects"]["edges"][0]
        ["node"]["spans"]["edges"]
    )

    tcp_error_spans = [
        e["node"] for e in spans
        if "tcp.connect" in e["node"]["name"]
        and e["node"]["statusCode"] == "ERROR"
    ]

    assert len(tcp_error_spans) >= 2, (
        "GAP 1: Expected 2 failed connection spans "
        f"(ports 7687 and 27017), got {len(tcp_error_spans)}"
    )

    ports_found = set()
    for span in tcp_error_spans:
        attrs = flatten_attrs(
            json.loads(span.get("attributes", "{}"))
        )
        port = attrs.get("tcp.port")
        if port:
            ports_found.add(int(port))

    assert 7687 in ports_found, \
        "GAP 1: Port 7687 (Neo4j) not identified in failed span"
    assert 27017 in ports_found, \
        "GAP 1: Port 27017 (MongoDB) not identified in failed span"

    print("Service identity captured in connection failure spans")
