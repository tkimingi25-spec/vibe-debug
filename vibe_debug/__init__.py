import os

from vibe_debug.adapters import instrument_all_adapters
from vibe_debug.auto_instrument import auto_instrument_all
from vibe_debug.config import generate_config, load_config
from vibe_debug.decorators import vibetrace
from vibe_debug.runtime.import_mapper import ImportMapper
from vibe_debug.runtime.io_interceptor import IOInterceptor
from vibe_debug.runtime.partial_trace_builder import PartialTraceBuilder
from vibe_debug.runtime.static_analyzer import ProjectStaticAnalyzer
from vibe_debug.runtime.sys_tracer import UniversalRuntimeTracer
from vibe_debug.tracer import init_vibedebug

def start(
    project_name: str = None,
    config_path: str = "vibedebug.toml",
    project_root: str = None,
    universal_trace: bool = False,
    patch_socket: bool = False,
):
    """
    One-line entry point for vibe-debug.
    1. Scans project statically with ast (instant, no execution needed)
    2. Auto-generates vibedebug.toml if not present
    3. Initializes tracer
    4. Auto-instruments watched modules
    5. Activates framework adapters
    """
    root = project_root or os.getcwd()
    analyzer = ProjectStaticAnalyzer(project_root=root)
    pipeline_map = analyzer.scan()

    import_mapper = ImportMapper(project_root=root)
    import_graph = import_mapper.build_graph()

    if not os.path.exists(config_path):
        generate_config(
            pipeline_map=pipeline_map,
            project_root=root,
            project_name=project_name,
        )

    config = load_config(config_path)
    if project_name:
        config.project_name = project_name

    trace_builder = PartialTraceBuilder(project_name=config.project_name)
    trace_builder.load_static_map(analyzer.to_dict())
    trace_builder.load_import_graph(import_mapper.to_dict())

    print("[vibe-debug] Pipeline intelligence ready")

    init_vibedebug(project_name=config.project_name)
    auto_instrument_all(config)

    runtime_tracer = None
    if universal_trace:
        runtime_tracer = UniversalRuntimeTracer(
            project_root=root,
            project_name=config.project_name,
        )
        runtime_tracer.start()
        print("[vibe-debug] Universal runtime tracing enabled")

    io_interceptor = IOInterceptor(project_name=config.project_name)
    io_interceptor.start(patch_socket=patch_socket)

    instrument_all_adapters()

    print(f"[vibe-debug] Started - project: {config.project_name}")
    return config, pipeline_map, io_interceptor

__all__ = ["start", "init_vibedebug", "vibetrace", "IOInterceptor"]
