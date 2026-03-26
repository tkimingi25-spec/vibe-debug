import modulefinder
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Set


@dataclass
class ModuleDependency:
    module_name: str
    file_path: str
    imports: List[str] = field(default_factory=list)
    imported_by: List[str] = field(default_factory=list)
    is_project_module: bool = True


@dataclass
class ImportGraph:
    modules: Dict[str, ModuleDependency] = field(default_factory=dict)
    project_root: str = ""

    def get_dependents(self, module_name: str) -> List[str]:
        """
        Return all modules that import the given module.
        These are the modules that break if module_name breaks.
        """
        if module_name not in self.modules:
            return []
        return self.modules[module_name].imported_by

    def get_downstream(self, module_name: str,
                      visited: Set[str] = None) -> Set[str]:
        """
        Recursively find all modules downstream of module_name.
        These are all modules that directly or indirectly depend on it.
        """
        if visited is None:
            visited = set()
        if module_name in visited:
            return visited
        visited.add(module_name)
        for dependent in self.get_dependents(module_name):
            self.get_downstream(dependent, visited)
        return visited

    def get_upstream(self, module_name: str,
                    visited: Set[str] = None) -> Set[str]:
        """
        Recursively find all modules upstream of module_name.
        These are modules module_name depends on.
        """
        if visited is None:
            visited = set()
        if module_name not in self.modules:
            return visited
        for imp in self.modules[module_name].imports:
            if imp not in visited:
                visited.add(imp)
                self.get_upstream(imp, visited)
        return visited


class ImportMapper:
    """
    Builds a complete import dependency graph for a Python project.
    Uses modulefinder for deep analysis and ast for fast fallback.
    Does not execute any project code.
    """

    SKIP_PREFIXES = (
        "site-packages", "_", "encodings", "importlib",
        "collections", "typing", "abc", "os", "sys",
        "pathlib", "json", "re", "ast", "inspect",
        "functools", "dataclasses", "enum", "time",
        "threading", "logging", "warnings",
    )

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.import_graph = ImportGraph(project_root=self.project_root)
        self.project_modules: Set[str] = set()

    def build_graph(self) -> ImportGraph:
        """
        Build the full import dependency graph using ast-based
        import extraction (fast, works on broken code too).
        Falls back gracefully on any error.
        """
        print(f"[vibe-debug] Building import graph: {self.project_root}")

        py_files = self._collect_project_files()
        self.project_modules = {
            self._file_to_module(filepath)
            for filepath in py_files
        }

        for filepath in py_files:
            module_name = self._file_to_module(filepath)
            imports = self._extract_imports_ast(filepath)

            project_imports = [
                imp for imp in imports
                if self._is_project_module(imp)
            ]

            self.import_graph.modules[module_name] = ModuleDependency(
                module_name=module_name,
                file_path=filepath,
                imports=project_imports,
                is_project_module=True,
            )

        for module_name, dep in self.import_graph.modules.items():
            for imported in dep.imports:
                if imported in self.import_graph.modules:
                    self.import_graph.modules[imported].imported_by.append(
                        module_name
                    )

        total = len(self.import_graph.modules)
        edges = sum(
            len(d.imports)
            for d in self.import_graph.modules.values()
        )
        print(
            f"[vibe-debug] Import graph complete: "
            f"{total} modules, {edges} dependency edges"
        )
        return self.import_graph

    def _collect_project_files(self) -> List[str]:
        skip_dirs = {
            ".venv", "venv", "env", "__pycache__",
            ".git", "node_modules", ".pytest_cache",
            "dist", "build", "site-packages"
        }
        files = []
        for root, dirs, filenames in os.walk(self.project_root):
            dirs[:] = [
                d for d in dirs
                if d not in skip_dirs and not d.startswith(".")
            ]
            for f in filenames:
                if f.endswith(".py"):
                    files.append(os.path.join(root, f))
        return files

    def _file_to_module(self, filepath: str) -> str:
        """Convert absolute file path to dotted module name."""
        rel = os.path.relpath(filepath, self.project_root)
        return rel.replace(os.sep, ".").replace(".py", "")

    def _is_project_module(self, module_name: str) -> bool:
        """Return True if this is a project module not a stdlib/library."""
        for prefix in self.SKIP_PREFIXES:
            if module_name.startswith(prefix):
                return False
        for mod in self.project_modules:
            if mod == module_name or mod.startswith(module_name):
                return True
        parts = module_name.split(".")
        if parts[0] in {"vibe_debug", "tests", "server"}:
            return True
        return False

    def _extract_imports_ast(self, filepath: str) -> List[str]:
        """Extract all import names from a file using ast."""
        import ast
        try:
            with open(
                filepath, "r", encoding="utf-8", errors="ignore"
            ) as f:
                source = f.read()
            tree = ast.parse(source)
            imports = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.append(node.module)
            return imports
        except Exception:
            return []

    def get_crash_impact(
        self, crashed_module: str
    ) -> Dict[str, List[str]]:
        """
        Given a module that crashed, return:
        - directly_affected: modules that import it directly
        - transitively_affected: all downstream modules
        """
        directly = self.import_graph.get_dependents(crashed_module)
        transitively = self.import_graph.get_downstream(crashed_module)
        transitively.discard(crashed_module)

        return {
            "crashed_module": crashed_module,
            "directly_affected": directly,
            "transitively_affected": list(transitively),
        }

    def to_dict(self) -> dict:
        """Serialize import graph for storage and MCP use."""
        return {
            module: {
                "file": dep.file_path,
                "imports": dep.imports,
                "imported_by": dep.imported_by,
            }
            for module, dep in self.import_graph.modules.items()
        }
