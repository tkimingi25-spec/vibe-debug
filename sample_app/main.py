"""Runnable four-module sample pipeline for vibe-debug."""

import argparse
import json
from pathlib import Path

from opentelemetry import trace

import vibe_debug


def run_pipeline(query: str, *, fail: bool = False) -> str:
    """Run retrieval, processing, and response generation in sequence."""
    from generator import generate_response
    from processor import process_documents
    from retriever import retrieve_documents

    documents = retrieve_documents(query)
    processed = process_documents(documents, fail=fail)
    return generate_response(processed, query)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="How does tracing work?")
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    vibe_debug.start("vibe-debug-sample", project_root=str(root))
    try:
        result = run_pipeline(args.query, fail=args.fail)
    except ValueError as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1
    finally:
        trace.get_tracer_provider().force_flush(timeout_millis=5000)

    print(json.dumps({"status": "ok", "response": result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
