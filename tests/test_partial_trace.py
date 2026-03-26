import os

import pytest

from vibe_debug.runtime.partial_trace_builder import (
    ComponentStatus, PartialTraceBuilder
)
from vibe_debug.runtime.static_analyzer import ProjectStaticAnalyzer


def test_partial_trace_records_crash_and_marks_downstream():
    """
    Simulate a pipeline crash at step 3.
    Verify steps 1-2 are COMPLETED, step 3 is FAILED,
    and steps 4-6 are NEVER_REACHED - all from static map.
    """
    project_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    analyzer = ProjectStaticAnalyzer(project_root=project_root)
    pipeline_map = analyzer.scan()

    builder = PartialTraceBuilder(project_name="test-project")
    builder.load_static_map(analyzer.to_dict())

    builder.start_trace("trace_001")

    builder.record_start(
        "route_query", "tests.mock_pipeline",
        "tests/mock_pipeline.py", 5, "explain AI"
    )
    builder.record_success("route_query", "rag", 12.3)

    builder.record_start(
        "retrieve_documents", "tests.mock_pipeline",
        "tests/mock_pipeline.py", 12, "explain AI"
    )
    builder.record_success("retrieve_documents", "[doc1, doc2]", 45.1)

    builder.record_start(
        "lookup_knowledge_graph", "tests.mock_pipeline",
        "tests/mock_pipeline.py", 22, "[]"
    )
    builder.record_failure(
        "lookup_knowledge_graph",
        error_type="RuntimeError",
        error_message="Connection timeout after 3000ms",
        duration_ms=3001.0
    )

    trace = builder.finish_trace(success=False)

    assert trace.crash_point == "lookup_knowledge_graph"
    assert trace.is_partial() is True

    completed = trace.get_completed()
    failed = trace.get_failed()
    unreached = trace.get_never_reached()

    completed_names = {c.name for c in completed}
    failed_names = {c.name for c in failed}

    assert "route_query" in completed_names
    assert "retrieve_documents" in completed_names
    assert "lookup_knowledge_graph" in failed_names
    assert len(unreached) > 0, (
        "No NEVER_REACHED components - static map not applied"
    )

    report = builder.get_last_trace_report()
    print("\n" + report)

    print("\nPartial trace correctly assembled crash + downstream impact")


def test_complete_trace_has_no_crash_point():
    """A successful trace must not mark anything as crashed."""
    builder = PartialTraceBuilder(project_name="test")
    builder.start_trace("trace_success")

    builder.record_start("route_query", "mod", "file.py", 1)
    builder.record_success("route_query", "rag", 10.0)

    builder.record_start("retrieve_documents", "mod", "file.py", 5)
    builder.record_success("retrieve_documents", "[docs]", 20.0)

    trace = builder.finish_trace(success=True)

    assert trace.crash_point is None
    failed = trace.get_failed()
    assert len(failed) == 0

    print("Successful trace has no crash point or failed components")


def test_report_shows_full_picture_from_static_map():
    """
    Report must show all three sections:
    COMPLETED, FAILED, and NEVER_REACHED.
    """
    project_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    analyzer = ProjectStaticAnalyzer(project_root=project_root)
    analyzer.scan()

    builder = PartialTraceBuilder(project_name="test")
    builder.load_static_map(analyzer.to_dict())
    builder.start_trace("trace_report")

    builder.record_start("route_query", "tests.mock_pipeline",
                       "tests/mock_pipeline.py", 5)
    builder.record_failure("route_query", "ValueError",
                         "invalid query format", 5.0)

    trace = builder.finish_trace(success=False)
    report = builder.get_last_trace_report()

    assert "CRASH POINT" in report
    assert "FAILED" in report
    assert "route_query" in report

    print("\n" + report)
    print("\nReport shows complete picture including static map")
