import importlib
import inspect
import sys

import wrapt

from vibe_debug.config import VibeConfig
from vibe_debug.decorators import vibetrace


def _should_instrument(func_name: str, config: VibeConfig) -> bool:
    """Return True if this function should be instrumented."""
    if func_name in config.ignore_functions:
        return False
    if func_name.startswith("_"):
        return False
    return True


def _get_component_type(module_name: str) -> str:
    """Infer component type from module name for better span labels."""
    name = module_name.lower()
    if "rag" in name or "retriev" in name:
        return "rag"
    elif "router" in name or "route" in name:
        return "router"
    elif "agent" in name:
        return "agent"
    elif "kg" in name or "graph" in name or "neo4j" in name:
        return "knowledge_graph"
    elif "ingest" in name or "document" in name:
        return "ingestion"
    elif "security" in name or "auth" in name:
        return "security"
    elif "embed" in name or "vector" in name or "faiss" in name:
        return "embedding"
    else:
        return "component"


def auto_instrument_module(module_name: str, config: VibeConfig):
    """
    Import a module and wrap all its public functions with vibetrace.
    Uses wrapt to patch functions without modifying source code.
    """
    try:
        main_module = sys.modules.get("__main__")
        main_file = getattr(main_module, "__file__", "")
        if main_file and main_file.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0] == module_name:
            module = main_module
            allowed_module_names = {module_name, "__main__"}
        else:
            module = importlib.import_module(module_name)
            allowed_module_names = {module_name}
        component_type = _get_component_type(module_name)
        patched_count = 0

        for func_name, func_obj in inspect.getmembers(module, inspect.isfunction):
            if not _should_instrument(func_name, config):
                continue

            if func_obj.__module__ not in allowed_module_names:
                continue

            if getattr(func_obj, "_vibe_debug_instrumented", False):
                continue

            traced = vibetrace(component_type=component_type)(func_obj)
            wrapped = wrapt.FunctionWrapper(
                func_obj,
                lambda wrapped, instance, args, kwargs, traced=traced: traced(*args, **kwargs),
            )
            wrapped._vibe_debug_instrumented = True
            setattr(module, func_name, wrapped)
            patched_count += 1
            print(f"[vibe-debug] Auto-instrumented: {module_name}.{func_name}")

        print(
            f"[vibe-debug] Module {module_name}: "
            f"{patched_count} functions instrumented"
        )
        return module

    except ImportError as e:
        print(f"[vibe-debug] Could not import {module_name}: {e}")
        return None
    except Exception as e:
        print(f"[vibe-debug] Error instrumenting {module_name}: {e}")
        return None


def auto_instrument_all(config: VibeConfig):
    """Instrument all modules listed in vibedebug.toml [watch] section."""
    if not config.watch_modules:
        print("[vibe-debug] No modules listed in vibedebug.toml [watch]")
        return

    for module_name in config.watch_modules:
        auto_instrument_module(module_name, config)
