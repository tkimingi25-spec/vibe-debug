import json
import os
import shutil
import tempfile

from vibe_debug.runtime.import_mapper import ImportMapper
from vibe_debug.runtime.partial_trace_builder import (
    ComponentStatus, PartialTraceBuilder
)
from vibe_debug.runtime.pipeline_discovery import PipelineDiscovery
from vibe_debug.runtime.static_analyzer import ProjectStaticAnalyzer

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_pipeline_discovery_initializes_from_static():
    """
    Pipeline map must be fully populated from static analysis
    before any runtime observations - works on broken projects.
    """
    tmp = tempfile.mkdtemp()
    try:
        analyzer = ProjectStaticAnalyzer(project_root=PROJECT_ROOT)
        static_map = analyzer.scan()

        mapper = ImportMapper(project_root=PROJECT_ROOT)
        import_graph = mapper.build_graph()

        discovery = PipelineDiscovery(
            project_root=tmp,
            project_name="test-discovery"
        )
        pipeline_map = discovery.initialize(static_map, import_graph)

        assert len(pipeline_map.components) > 0
        assert "route_query" in pipeline_map.components
        assert "retrieve_documents" in pipeline_map.components

        comp = pipeline_map.components["route_query"]
        assert comp.file.endswith("mock_pipeline.py")
        assert comp.line == 5
        assert comp.times_called == 0
        assert comp.times_failed == 0

        report = discovery.get_pipeline_report()
        print("\n" + report)
        print("\n✅ Pipeline map initialized from static analysis")
    finally:
        shutil.rmtree(tmp)


def test_pipeline_discovery_updates_from_traces():
    """
    After recording traces, pipeline map must reflect
    runtime observations - call counts, failures, durations.
    """
    tmp = tempfile.mkdtemp()
    try:
        analyzer = ProjectStaticAnalyzer(project_root=PROJECT_ROOT)
        static_map = analyzer.scan()
        mapper = ImportMapper(project_root=PROJECT_ROOT)
        import_graph = mapper.build_graph()

        discovery = PipelineDiscovery(
            project_root=tmp,
            project_name="test-trace-update"
        )
        discovery.initialize(static_map, import_graph)

        builder = PartialTraceBuilder(project_name="test")
        builder.load_static_map(analyzer.to_dict())
        builder.start_trace("t1")
        builder.record_start(
            "route_query", "tests.mock_pipeline",
            "tests/mock_pipeline.py", 5, "hello"
        )
        builder.record_success("route_query", "rag", 15.0)
        builder.record_start(
            "lookup_knowledge_graph", "tests.mock_pipeline",
            "tests/mock_pipeline.py", 22, "[]"
        )
        builder.record_failure(
            "lookup_knowledge_graph", "TimeoutError",
            "Neo4j timeout", 3001.0
        )
        partial_trace = builder.finish_trace(success=False)
        discovery.record_trace(partial_trace)

        route = discovery.pipeline_map.components["route_query"]
        kg = discovery.pipeline_map.components["lookup_knowledge_graph"]

        assert route.times_called == 1
        assert route.times_succeeded == 1
        assert route.last_status == "OK"
        assert kg.times_called == 1
        assert kg.times_failed == 1
        assert kg.last_status == "ERROR"
        assert "TimeoutError" in kg.last_error

        crash_prone = discovery.pipeline_map.get_crash_prone()
        assert any(
            c.name == "lookup_knowledge_graph" for c in crash_prone
        )

        report = discovery.get_pipeline_report()
        assert "CRASH-PRONE" in report
        assert "lookup_knowledge_graph" in report
        print("\n" + report)
        print("\n✅ Pipeline map updated correctly from trace observations")
    finally:
        shutil.rmtree(tmp)


def test_pipeline_map_persists_to_disk():
    """
    Pipeline map must survive restarts via disk cache.
    """
    tmp = tempfile.mkdtemp()
    try:
        cache_path = os.path.join(tmp, ".vibe_pipeline_cache.json")

        analyzer = ProjectStaticAnalyzer(project_root=PROJECT_ROOT)
        static_map = analyzer.scan()
        mapper = ImportMapper(project_root=PROJECT_ROOT)
        import_graph = mapper.build_graph()

        d1 = PipelineDiscovery(
            project_root=tmp,
            project_name="persist-test"
        )
        d1.initialize(static_map, import_graph)
        d1.pipeline_map.total_traces = 5
        d1.pipeline_map.total_crashes = 2
        d1._save_cache()

        assert os.path.exists(cache_path)

        d2 = PipelineDiscovery(
            project_root=tmp,
            project_name="persist-test"
        )
        d2._load_cache()

        assert d2.pipeline_map.total_traces == 5
        assert d2.pipeline_map.total_crashes == 2

        print("Pipeline map persisted and reloaded correctly")
    finally:
        shutil.rmtree(tmp)
