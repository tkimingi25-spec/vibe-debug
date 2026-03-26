import ast
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set


@dataclass
class FunctionInfo:
    name: str
    file: str
    line: int
    calls: List[str] = field(default_factory=list)
    docstring: str = ""
    module: str = ""


@dataclass
class StaticPipelineMap:
    functions: Dict[str, FunctionInfo] = field(default_factory=dict)
    imports: Dict[str, List[str]] = field(default_factory=dict)
    entry_points: List[str] = field(default_factory=list)
    external_calls: Set[str] = field(default_factory=set)
    modules: List[str] = field(default_factory=list)


class ProjectStaticAnalyzer:
    """
    Scans all .py files in a project directory using ast.
    Extracts functions, calls, imports, and entry points
    without executing any code.
    """

    SKIP_DIRS = {
        "site-packages", ".venv", "venv", "env",
        "__pycache__", ".git", "node_modules",
        ".pytest_cache", "dist", "build", "eggs"
    }

    ENTRY_POINT_NAMES = {
        "main.py", "app.py", "run.py", "server.py",
        "api.py", "cli.py", "start.py", "index.py"
    }

    def __init__(self, project_root: str):
        self.project_root = os.path.abspath(project_root)
        self.pipeline_map = StaticPipelineMap()

    def scan(self) -> StaticPipelineMap:
        """
        Full project scan. Returns complete StaticPipelineMap.
        Skips files that fail to parse - never crashes on bad code.
        """
        print(f"[vibe-debug] Scanning project: {self.project_root}")
        py_files = self._collect_python_files()
        print(f"[vibe-debug] Found {len(py_files)} Python files")

        for filepath in py_files:
            self._analyze_file(filepath)

        self._detect_entry_points()
        self._detect_modules()

        print(
            f"[vibe-debug] Static analysis complete: "
            f"{len(self.pipeline_map.functions)} functions found "
            f"across {len(self.pipeline_map.modules)} modules"
        )
        return self.pipeline_map

    def _collect_python_files(self) -> List[str]:
        """Walk project directory and collect all .py files."""
        py_files = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = [
                d for d in dirs
                if d not in self.SKIP_DIRS
                and not d.startswith(".")
            ]
            for filename in files:
                if filename.endswith(".py"):
                    py_files.append(os.path.join(root, filename))
        return py_files

    def _analyze_file(self, filepath: str):
        """
        Parse a single .py file with ast and extract all functions
        and their call relationships. Skips silently on parse error.
        """
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                source = f.read()
            tree = ast.parse(source, filename=filepath)
        except SyntaxError:
            print(f"[vibe-debug] Skipping (syntax error): {filepath}")
            return
        except Exception:
            return

        rel_path = os.path.relpath(filepath, self.project_root)
        module_name = rel_path.replace(os.sep, ".").replace(".py", "")

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        self.pipeline_map.imports[module_name] = imports

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_key = f"{module_name}.{node.name}"

                docstring = ""
                if (
                    node.body and
                    isinstance(node.body[0], ast.Expr) and
                    isinstance(node.body[0].value, ast.Constant)
                ):
                    docstring = str(node.body[0].value.value)

                calls = []
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        call_name = self._extract_call_name(child)
                        if call_name:
                            calls.append(call_name)

                self.pipeline_map.functions[func_key] = FunctionInfo(
                    name=node.name,
                    file=filepath,
                    line=node.lineno,
                    calls=calls,
                    docstring=docstring,
                    module=module_name,
                )

    def _extract_call_name(self, node: ast.Call) -> Optional[str]:
        """Extract a readable name from a Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            parts = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            return ".".join(reversed(parts))
        return None

    def _detect_entry_points(self):
        """Identify likely entry point files by name."""
        for func_key, info in self.pipeline_map.functions.items():
            filename = os.path.basename(info.file)
            if filename in self.ENTRY_POINT_NAMES:
                self.pipeline_map.entry_points.append(func_key)
            if info.name == "main":
                self.pipeline_map.entry_points.append(func_key)

    def _detect_modules(self):
        """Collect unique module names found in the project."""
        modules = set()
        for func_key in self.pipeline_map.functions:
            module = func_key.rsplit(".", 1)[0]
            modules.add(module)
        self.pipeline_map.modules = sorted(list(modules))

    def to_dict(self) -> dict:
        """Serialize the pipeline map to a plain dict for storage."""
        return {
            "functions": {
                k: {
                    "name": v.name,
                    "file": v.file,
                    "line": v.line,
                    "calls": v.calls,
                    "docstring": v.docstring,
                    "module": v.module,
                }
                for k, v in self.pipeline_map.functions.items()
            },
            "imports": self.pipeline_map.imports,
            "entry_points": self.pipeline_map.entry_points,
            "modules": self.pipeline_map.modules,
        }
