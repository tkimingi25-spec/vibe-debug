import json
import os

from vibe_debug.runtime.static_analyzer import ProjectStaticAnalyzer


def test_static_analyzer_scans_project():
    """
    Run static analyzer on the vibe-debug project itself.
    Verify it finds known functions without running any code.
    """
    project_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    analyzer = ProjectStaticAnalyzer(project_root=project_root)
    pipeline_map = analyzer.scan()

    assert len(pipeline_map.functions) > 0, "Analyzer found no functions"

    known_functions = [
        "vibetrace", "init_vibedebug", "route_query",
        "retrieve_documents", "lookup_knowledge_graph"
    ]
    found_names = {
        info.name
        for info in pipeline_map.functions.values()
    }
    for func in known_functions:
        assert func in found_names, f"Known function '{func}' not found by static analyzer"

    for key, info in pipeline_map.functions.items():
        assert info.file, f"Missing file for {key}"
        assert info.line > 0, f"Missing line for {key}"
        assert os.path.exists(info.file), f"File does not exist: {info.file}"

    assert len(pipeline_map.modules) > 0

    print("\n--- Static Analysis Results ---")
    print(f"Functions found: {len(pipeline_map.functions)}")
    print(f"Modules found:   {len(pipeline_map.modules)}")
    print(f"Entry points:    {pipeline_map.entry_points}")
    print("\nSample functions:")
    for key, info in list(pipeline_map.functions.items())[:8]:
        print(f"  {info.name:30} {info.file}:{info.line}")

    print("\nStatic analyzer works - pipeline map built without running code")


def test_static_analyzer_survives_broken_files():
    """
    Analyzer must never crash on syntax errors or unreadable files.
    It should skip bad files and continue scanning the rest.
    """
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "good.py"), "w") as f:
            f.write("def good_function():\n    return 'hello'\n")

        with open(os.path.join(tmp, "broken.py"), "w") as f:
            f.write("def broken_function(\n    # missing closing paren\n")

        analyzer = ProjectStaticAnalyzer(project_root=tmp)
        pipeline_map = analyzer.scan()

        found_names = {
            info.name
            for info in pipeline_map.functions.values()
        }
        assert "good_function" in found_names, (
            "Good function not found after skipping broken file"
        )

        print("Analyzer survived broken file - continued scanning")
    finally:
        shutil.rmtree(tmp)


def test_config_generation():
    """
    Verify auto-generated vibedebug.toml contains expected sections.
    """
    import shutil
    import tempfile

    from vibe_debug.config.config_generator import generate_config
    from vibe_debug.runtime.static_analyzer import (
        ProjectStaticAnalyzer, StaticPipelineMap
    )

    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "app.py"), "w") as f:
            f.write("""
def handle_request(query):
    '''Main entry point for all requests.'''
    return route(query)

def route(query):
    '''Routes query to correct handler.'''
    return query
""")
        analyzer = ProjectStaticAnalyzer(project_root=tmp)
        pipeline_map = analyzer.scan()

        config_path = generate_config(
            pipeline_map=pipeline_map,
            project_root=tmp,
            project_name="test-project"
        )

        assert os.path.exists(config_path), "Config file not created"

        with open(config_path) as f:
            content = f.read()

        assert "[project]" in content
        assert "[watch]" in content
        assert "[thresholds]" in content
        assert "test-project" in content
        assert "handle_request" in content or "app" in content

        print("Config auto-generated with correct structure")
        print("\nGenerated config preview:")
        print(content[:500])
    finally:
        shutil.rmtree(tmp)
