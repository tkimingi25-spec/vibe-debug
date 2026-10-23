# vibe-debug Implementation Journal

## Phase 1 — Core Pipeline (Complete)

### What Was Built
- OpenTelemetry `TracerProvider` with a bounded `BatchSpanProcessor`
- `OTLPSpanExporter` sending spans to Arize Phoenix on `localhost:6006`
- `@vibetrace` decorator with silent failure detection
- MCP bridge with `get_last_failure()` and `explain_trace()`
- Phoenix GraphQL integration for reading spans back

### Key Technical Decisions
- `BatchSpanProcessor` with a five-second export timeout: exports never block application calls while Phoenix is unavailable
- GraphQL over REST: Phoenix read-path GraphQL queries have been stable in this repo, while `/v1/spans` was not the right read endpoint
- `flatten_attrs()` helper: Phoenix returns nested attributes, so readers flatten before lookup
- `sort: {col: startTime, dir: desc}`: newest spans first avoids stale test results

### Files Created
- `vibe_debug/tracer.py` — tracer setup and OTLP export
- `vibe_debug/decorators.py` — optional manual tracing decorator
- `server/mcp_bridge.py` — MCP tools backed by Phoenix GraphQL
- `tests/test_instrument.py` — unit-level decorator behavior
- `tests/test_integration.py` — live Phoenix integration path
- `tests/test_span_send.py` — manual smoke script for direct span sending

### Tests
- `tests/test_instrument.py` — decorator return path, silent-failure handling, exception re-raise
- `tests/test_integration.py` — end-to-end function tracing against Phoenix
- `tests/test_span_send.py` — manual verification that a direct span reaches Phoenix

---

## Phase 2 Week 1 — Exact Location (Complete)

### What Was Built
- `inspect.getfile()` and `inspect.getsourcelines()` added inside `@vibetrace`
- `vibe.file`, `vibe.line`, `vibe.module`, and `vibe.doc` attached to spans
- MCP bridge updated to surface file and line in `get_last_failure()`
- IDE-side navigation can jump directly to the failure location

### Key Technical Decisions
- `inspect` runs inside the wrapper so the traced function metadata is captured at call time
- `TypeError` and `OSError` are caught silently for built-ins and non-standard callables
- `flatten_attrs()` is always applied before reading `vibe.*` fields from Phoenix

### Files Modified
- `vibe_debug/decorators.py`
- `server/mcp_bridge.py`
- `tests/test_integration.py`

### Tests
- `tests/test_integration.py` checks that `vibe.file` and `vibe.line` are present on traced spans

---

## Phase 2 Week 2 — Auto-Instrumentation (Complete)

### What Was Built
- `vibedebug.toml` config file format
- `VibeConfig` dataclass and `load_config()` reader
- `auto_instrument.py` with `wrapt`-based module patching
- `vibe_debug.start()` one-line entry point
- `_get_component_type()` to infer span type from module name
- Silent failure detection extended to include empty dicts

### Key Technical Decisions
- `wrapt` over `sys.settrace()` for module patching because it keeps overhead limited to watched modules
- `setattr(module, func_name, wrapped)` replaces module-level call targets cleanly
- Import after `start()` matters because patching must happen before callers bind original functions
- Tracer initialization is guarded to avoid duplicate provider registration

### Files Created/Modified
- `vibedebug.toml`
- `vibe_debug/config/__init__.py`
- `vibe_debug/config/config.py`
- `vibe_debug/auto_instrument.py`
- `vibe_debug/__init__.py`
- `tests/mock_pipeline.py`
- `tests/test_auto_instrument.py`
- `tests/__init__.py`

### Tests
- `tests/test_auto_instrument.py` verifies zero-decorator tracing through `vibe_debug.start()`

---

## Phase 2 Week 3 — Universal Observability (Complete)

### Week 3A — Static Code Analysis

#### What Was Built
- `ProjectStaticAnalyzer` using Python `ast`
- Safe scan of all `.py` files without importing project code
- `FunctionInfo` extraction: name, file, line, calls, docstring, module
- Syntax-error tolerance: bad files are skipped, scan continues
- `StaticPipelineMap` for project shape before first execution
- `config_generator.py` to auto-generate `vibedebug.toml`

#### Key Technical Decisions
- `ast` over `exec` or dynamic import keeps scanning safe even for broken projects
- `dirs[:]` filtering prevents `os.walk()` from descending into skipped directories
- `ENTRY_POINT_NAMES` gives a simple first-pass entry-point heuristic
- Config generation stays separate from config loading because generation is one-time but loading happens every start

#### Files Created
- `vibe_debug/runtime/static_analyzer.py`
- `vibe_debug/config/config_generator.py`
- `tests/test_static_analyzer.py`

#### Tests
- `tests/test_static_analyzer.py` covers project scan, syntax-error survival, and generated config output

---

### Week 3B — Partial Trace Assembly

#### What Was Built
- `PartialTraceBuilder` and `ComponentStatus`
- `record_start()`, `record_success()`, and `record_failure()`
- `_mark_downstream_unreached()` to create `NEVER_REACHED` components
- `get_last_trace_report()` summarizing completed, failed, and blocked steps
- Crash traces are preserved instead of being dropped

#### Key Technical Decisions
- `ComponentStatus` enum avoids string drift and makes filtering explicit
- `NEVER_REACHED` starts from the static map so first-run crashes still have context
- Fallback keyword filtering limits noise when no better dependency signal exists
- `finish_trace(success=False)` is an explicit marker for partial traces

#### Files Created
- `vibe_debug/runtime/partial_trace_builder.py`
- `tests/test_partial_trace.py`

#### Tests
- `tests/test_partial_trace.py` covers crash assembly, successful completion, and report layout

---

### Week 3C — Import Graph

#### What Was Built
- `ImportMapper` using AST-based import extraction
- `ImportGraph` with both `imports` and `imported_by`
- Recursive downstream traversal for impact analysis
- `get_crash_impact()` for direct and transitive dependency impact
- `load_import_graph()` added to `PartialTraceBuilder`

#### Key Technical Decisions
- AST import extraction is used instead of executing code paths
- Two-pass graph build: collect imports first, then reverse-link `imported_by`
- `_is_project_module()` filters out stdlib and third-party imports
- `ImportGraph` is a data structure, `ImportMapper` does the analysis work

#### Files Created
- `vibe_debug/runtime/import_mapper.py`
- `tests/test_import_mapper.py`

#### Tests
- `tests/test_import_mapper.py` covers graph building, dependency chains, and crash impact

---

### Week 3D — Universal Runtime Tracing

#### What Was Built
- `UniversalRuntimeTracer` using `sys.settrace()`
- `RelevanceFilter` to separate project code from library noise
- Path-fragment and function-name skip lists
- Per-frame `local_trace` callback capturing return values and exceptions
- Lifecycle methods `start()` and `stop()` with counters

#### Key Technical Decisions
- `sys.settrace()` catches code that module patching cannot, including dynamic callbacks
- Returning a per-frame `local_trace` keeps deeper handling scoped to relevant frames
- Project-root prefix check is the cheapest high-signal relevance filter
- `threading.settrace()` extends tracing into worker threads

#### Files Created
- `vibe_debug/runtime/sys_tracer.py`
- `tests/test_sys_tracer.py`

#### Tests
- `tests/test_sys_tracer.py` covers relevance filtering and live runtime span capture

---

### Week 3E — I/O Interception

#### What Was Built
- `IOInterceptor` patching `requests`, `httpx`, and `socket.create_connection`
- HTTP span names like `http.GET host/path`
- `tcp.connect` spans for raw socket traffic
- Phoenix/OTLP exclusion logic to avoid recursive tracing
- Network call counters and patch restore lifecycle

#### Key Technical Decisions
- Library-level HTTP patches preserve URL, method, and status metadata better than raw socket hooks alone
- Socket fallback still catches clients that bypass `requests` and `httpx`
- Phoenix host/port exclusion prevents self-tracing loops
- `functools.wraps` preserves original call signatures

#### Files Created
- `vibe_debug/runtime/io_interceptor.py`
- `tests/test_io_interceptor.py`

#### Tests
- `tests/test_io_interceptor.py` covers patch activation, real HTTP capture, and Phoenix exclusion

---

### Week 3F — Pipeline Discovery

#### What Was Built
- `PipelineDiscovery` merging static analysis, import graph, and runtime traces
- `PipelineComponent` runtime counters and failure metadata
- `PipelineMap` helpers: `get_crash_prone()`, `get_never_observed()`, `get_critical_path()`
- `.vibe_pipeline_cache.json` persistence
- `get_pipeline_map()` MCP tool for IDE access
- `initialize()` for pre-runtime map creation
- `record_trace()` incremental updates after each run

#### Key Technical Decisions
- JSON cache over SQLite keeps the system dependency-free and easy to inspect
- `_load_cache()` runs inside `initialize()` so previous observations survive rescans
- Critical path uses a 50% call threshold to highlight core components
- Cache writes happen after every trace update so crash history survives process exits

#### Files Created
- `vibe_debug/runtime/pipeline_discovery.py`
- `tests/test_pipeline_discovery.py`
- `server/mcp_bridge.py` updated with `get_pipeline_map()`

#### Tests
- `tests/test_pipeline_discovery.py` covers initialization, runtime updates, and cache persistence

---

## Current Test Coverage Summary

- `tests/test_instrument.py` — 3 tests (unit, mocked)
- `tests/test_integration.py` — 1 test (live Phoenix)
- `tests/test_span_send.py` — manual verification script
- `tests/test_auto_instrument.py` — 1 test (auto-instrumentation)
- `tests/test_static_analyzer.py` — 3 tests (AST scanning)
- `tests/test_partial_trace.py` — 3 tests (crash assembly)
- `tests/test_import_mapper.py` — 3 tests (dependency graph)
- `tests/test_sys_tracer.py` — 2 tests (runtime tracing)
- `tests/test_io_interceptor.py` — 3 tests (network capture)
- `tests/test_pipeline_discovery.py` — 3 tests (unified map)

Total automated tests: 22

---

## What Is Not Yet Built

### Week 4 — AI Explanation Layer (Complete)
- `why_did_this_fail()` MCP tool
- `suggest_fix()` MCP tool
- failure-pattern detection and attribute flattening helpers

### Phase 3 — Universal Release (Planned)
- full packaging metadata for `pip install vibe-debug`
- Node.js SDK
- hosted/cloud option
- GitHub integration
- adapter registry
- dedicated web dashboard
