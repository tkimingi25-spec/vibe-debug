"""Manual Phoenix smoke check; run with ``python tests/test_span_send.py``."""

from vibe_debug.tracer import init_vibedebug


def main():
    tracer = init_vibedebug("test-project")
    with tracer.start_as_current_span("manual.test.span") as span:
        span.set_attribute("vibe.type", "test")
        span.set_attribute("vibe.input", "hello phoenix")
        span.set_attribute("vibe.status", "OK")
        print("Span created and queued for export")
    print("Done — check Phoenix UI at http://localhost:6006")


if __name__ == "__main__":
    main()
