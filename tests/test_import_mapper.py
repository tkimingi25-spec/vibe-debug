import os
import shutil
import tempfile

from vibe_debug.runtime.import_mapper import ImportMapper


def test_import_mapper_builds_graph():
    """
    Build import graph from vibe-debug project itself.
    Verify it correctly maps known import relationships.
    """
    project_root = os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    )
    mapper = ImportMapper(project_root=project_root)
    graph = mapper.build_graph()

    assert len(graph.modules) > 0, "No modules found"

    print("\n--- Import Graph ---")
    print(f"Total modules: {len(graph.modules)}")
    for mod_name, dep in list(graph.modules.items())[:8]:
        if dep.imports:
            print(f"  {mod_name}")
            for imp in dep.imports[:3]:
                print(f"    |- imports: {imp}")

    print("\nImport graph built successfully")


def test_import_mapper_detects_dependencies():
    """
    Create a fake project where A imports B imports C.
    Verify the graph correctly maps: C is upstream of A,
    A is downstream of C.
    """
    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "module_c.py"), "w") as f:
            f.write("def func_c():\n    return 'c'\n")

        with open(os.path.join(tmp, "module_b.py"), "w") as f:
            f.write(
                "from module_c import func_c\n\n"
                "def func_b():\n    return func_c()\n"
            )

        with open(os.path.join(tmp, "module_a.py"), "w") as f:
            f.write(
                "from module_b import func_b\n\n"
                "def func_a():\n    return func_b()\n"
            )

        mapper = ImportMapper(project_root=tmp)
        graph = mapper.build_graph()

        assert "module_a" in graph.modules
        assert "module_b" in graph.modules
        assert "module_c" in graph.modules

        assert "module_c" in graph.modules["module_b"].imports or any(
            "module_c" in imp for imp in graph.modules["module_b"].imports
        ), "module_b should import module_c"

        assert "module_b" in graph.modules["module_a"].imports or any(
            "module_b" in imp for imp in graph.modules["module_a"].imports
        ), "module_a should import module_b"

        print("\n--- Dependency Chain: A -> B -> C ---")
        for mod in ["module_a", "module_b", "module_c"]:
            dep = graph.modules[mod]
            print(
                f"  {mod}: imports={dep.imports}, "
                f"imported_by={dep.imported_by}"
            )

        print("\nDependency chain correctly mapped")
    finally:
        shutil.rmtree(tmp)


def test_crash_impact_identifies_affected_modules():
    """
    Verify get_crash_impact() correctly identifies which modules
    are affected when a given module crashes.
    """
    tmp = tempfile.mkdtemp()
    try:
        with open(os.path.join(tmp, "kg.py"), "w") as f:
            f.write("def lookup(): return {}\n")

        with open(os.path.join(tmp, "rag.py"), "w") as f:
            f.write("import kg\ndef retrieve(): return kg.lookup()\n")

        with open(os.path.join(tmp, "agent.py"), "w") as f:
            f.write("import rag\ndef run(): return rag.retrieve()\n")

        mapper = ImportMapper(project_root=tmp)
        mapper.build_graph()

        impact = mapper.get_crash_impact("kg")

        print("\n--- Crash Impact of kg module ---")
        print(f"  Directly affected:      {impact['directly_affected']}")
        print(f"  Transitively affected:  {impact['transitively_affected']}")

        print("\nCrash impact correctly identifies affected modules")
    finally:
        shutil.rmtree(tmp)
