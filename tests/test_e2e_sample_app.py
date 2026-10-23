"""Live end-to-end validation for the documented four-module sample app."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


pytestmark = pytest.mark.requires_phoenix

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_ROOT = ROOT / "sample_app"


def _run_sample(*args):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "main.py", *args],
        cwd=SAMPLE_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _recent_spans():
    query = """
    { projects { edges { node {
        spans(first: 100, sort: {col: startTime, dir: desc}) {
            edges { node { name statusCode attributes } }
        }
    } } } }
    """
    response = httpx.post("http://localhost:6006/graphql", json={"query": query}, timeout=10)
    response.raise_for_status()
    projects = response.json()["data"]["projects"]["edges"]
    spans = []
    for project in projects:
        spans.extend(edge["node"] for edge in project["node"]["spans"]["edges"])
    return spans


def test_sample_app_workflow_through_cli_and_mcp():
    successful = _run_sample()
    assert successful.returncode == 0, successful.stderr
    assert json.loads(successful.stdout.splitlines()[-1])["status"] == "ok"

    failed = _run_sample("--fail")
    assert failed.returncode == 1, failed.stderr
    assert "sample processor deliberately failed" in failed.stdout

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        spans = _recent_spans()
        processor_spans = [span for span in spans if span["name"] == "process_documents"]
        if any(span["statusCode"] == "ERROR" for span in processor_spans):
            break
        time.sleep(0.25)
    else:
        pytest.fail("The sample processor failure did not reach Phoenix")

    cache = json.loads((SAMPLE_ROOT / ".vibe_pipeline_cache.json").read_text(encoding="utf-8"))
    assert {"run_pipeline", "retrieve_documents", "process_documents", "generate_response"} <= set(cache["components"])

    cli_base = [sys.executable, "-m", "vibe_debug.cli", "--project-root", str(SAMPLE_ROOT)]
    for command, expected in (("status", "Project: vibe-debug-sample"), ("map", "process_documents"), ("failures", "process_documents"), ("explain", "process_documents")):
        result = subprocess.run([*cli_base, command], cwd=ROOT, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout

    code = "import asyncio; from server.mcp_bridge import why_did_this_fail; print(asyncio.run(why_did_this_fail()))"
    mcp = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT,
        env={
            **os.environ,
            "VIBE_PROJECT_ROOT": str(SAMPLE_ROOT),
            "PYTHONIOENCODING": "utf-8",
        },
        text=True, capture_output=True, timeout=20,
    )
    assert mcp.returncode == 0, mcp.stderr
    assert "process_documents" in mcp.stdout
