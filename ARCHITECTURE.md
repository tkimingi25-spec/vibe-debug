# vibe-debug Architecture

## 1. Three-Layer Architecture

```text
Layer 1: Data Collection
-------------------------------------------------------------
@vibetrace          auto_instrument          sys_tracer
manual spans        module patching          sys.settrace hook

io_interceptor
HTTP + TCP capture

                         |
                         v

Layer 2: Intelligence
-------------------------------------------------------------
static_analyzer          import_mapper
AST project scan         dependency graph

partial_trace_builder    pipeline_discovery
crash assembly           unified cached pipeline map

                         |
                         v

Layer 3: Exposure
-------------------------------------------------------------
Arize Phoenix UI         MCP bridge           IDE assistant
trace storage/UI         query tools          asks "what failed?"
```

## 2. Startup Sequence: `vibe_debug.start()`

```text
Step 1  -> Static scan (AST)
          ProjectStaticAnalyzer scans Python files without executing them

Step 2  -> Import graph
          ImportMapper builds dependency links between project modules

Step 3  -> Config generation
          vibedebug.toml is generated if it does not already exist

Step 4  -> Tracer init
          OpenTelemetry tracer exports spans to Phoenix

Step 5  -> Auto-instrument
          watched modules are patched with tracing wrappers

Step 6  -> Adapters
          instrument_all_adapters() hook runs

Step 7  -> Runtime tracer (optional)
          UniversalRuntimeTracer starts if universal_trace=True

Step 8  -> I/O interceptor
          HTTP and socket interception starts automatically

`@vibetrace` supports both regular and `async def` functions. Async calls are
awaited inside the active span, so their result, duration, and any exception
are recorded after the coroutine actually completes.
```

## 3. Data Flow for a Single Query

```text
User query
   |
   v
Function calls happen in project code
   |
   v
Spans are created
  - manual decorator spans
  - auto-instrumented spans
  - runtime sys.settrace spans
  - HTTP / TCP spans
   |
   v
OTLP exporter sends spans to Phoenix
   |
   v
Phoenix stores them and exposes them via GraphQL
   |
   v
MCP bridge queries Phoenix
   |
   v
IDE assistant receives answers from:
  - get_last_failure()
  - explain_trace()
  - get_pipeline_map()
```

## 4. Broken Project Scenario

```text
Project crashes at step N
   |
   v
Runtime tracing records what actually ran
   |
   v
Static map fills in steps N+1 ... end as known code paths
   |
   v
Import graph narrows downstream impact to modules that really depend on the crash
   |
   v
PartialTraceBuilder assembles:
  - COMPLETED
  - FAILED
  - NEVER_REACHED
   |
   v
PipelineDiscovery merges this with previous runs and saves cache
   |
   v
IDE gets the full picture:
  - what ran
  - what crashed
  - what never ran
  - which components are crash-prone
```

## End-to-End View

```text
Any Python Project
        |
        v
  vibe_debug.start()
        |
        v
  [Data Collection]
  decorators / auto_instrument / sys_tracer / io_interceptor
        |
        v
  OpenTelemetry spans
        |
        v
  Arize Phoenix
        |
        v
  [Intelligence]
  static_analyzer + import_mapper + partial_trace_builder + pipeline_discovery
        |
        v
  MCP Bridge
        |
        v
  IDE Assistant
```
