import os

from dotenv import load_dotenv
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

load_dotenv()

_initialized = False


class LoggingBatchSpanProcessor(BatchSpanProcessor):
    def on_start(self, span, parent_context=None):
        print(f"[vibe-debug] Span started: {span.name}")
        super().on_start(span, parent_context=parent_context)

    def on_end(self, span):
        print(f"[vibe-debug] Exporting span: {span.name}")
        try:
            super().on_end(span)
            print(f"[vibe-debug] Export succeeded: {span.name}")
        except Exception as e:
            print(f"[vibe-debug] Export failed: {span.name}: {e}")
            raise


def init_vibedebug(project_name="vibe-debug"):
    global _initialized
    if _initialized:
        return trace.get_tracer(__name__)

    otlp_endpoint = os.getenv(
        "OTLP_ENDPOINT", "http://localhost:6006/v1/traces"
    )

    print(f"[vibe-debug] Initializing tracer -> {otlp_endpoint}")

    resource = Resource(attributes={"service.name": project_name})
    provider = TracerProvider(resource=resource)

    try:
        exporter = OTLPSpanExporter(
            endpoint=otlp_endpoint,
            headers={}
        )
        processor = LoggingBatchSpanProcessor(
            exporter,
            export_timeout_millis=5000,
            schedule_delay_millis=500,
            max_export_batch_size=32,
        )
        provider.add_span_processor(processor)
        print("[vibe-debug] Exporter registered successfully")
    except Exception as e:
        print(f"[vibe-debug] ERROR setting up exporter: {e}")

    trace.set_tracer_provider(provider)
    _initialized = True
    return trace.get_tracer(__name__)
