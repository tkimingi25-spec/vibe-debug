# vibe-debug

Universal AI pipeline observability for vibe coders

## What It Does

`vibe-debug` is a local-first debugging toolkit for Python pipelines.
You add one line to your project:

```python
import vibe_debug
vibe_debug.start("my-project")
```

From that point on, `vibe-debug` can:

- Trace function calls automatically with zero manual decorators
- Capture crashes and silent failures (such as empty returns)
- Attach file paths, line numbers, inputs, and outputs to spans
- Intercept outbound HTTP and TCP socket calls
- Build a pipeline map from static AST code analysis before your code runs
- Store and visualize everything in a local Arize Phoenix dashboard (`http://localhost:6006`)
- Expose structured answers back to your IDE assistant through MCP tools

The goal is simple: when something breaks, your IDE and terminal can tell you what failed, where it failed, why it failed, what never ran, and what the rest of the project looks like.

---

## Quick Start

### 1. Install

From GitHub:
```powershell
pip install "vibe-debug @ git+https://github.com/tkimingi25-spec/vibe-debug.git"
```

Or from local source:
```powershell
git clone https://github.com/tkimingi25-spec/vibe-debug.git
cd vibe-debug
pip install -e .
```

Dependencies are pinned in `pyproject.toml`, which is the single source of truth.
There is no `requirements.txt`; use `pip install -e ".[dev]"` for the test dependencies.

### 2. Start Phoenix (One-command launch)

```powershell
# Starts the Phoenix service in the background and waits until it is healthy
vibe-debug up
```

Open your browser to: **[http://localhost:6006](http://localhost:6006)** to see the live trace dashboard.

### 3. Add to your project

Near your application's entry point (`main.py`, `app.py`):
```python
import vibe_debug
vibe_debug.start("my-project")

# Your normal application code follows:
from my_pipeline import run_pipeline
if __name__ == "__main__":
    run_pipeline()
```

---

## Complete Sample Workflow

A realistic 4-module sample pipeline is included in [`sample_app/`](sample_app/README.md):

```text
main.py -> retriever.py -> processor.py -> generator.py
```

Run both the success and deliberate failure paths:

```powershell
vibe-debug up
python sample_app/main.py
python sample_app/main.py --fail
```

Now inspect the results through the CLI:

```powershell
vibe-debug --project-root sample_app status
vibe-debug --project-root sample_app map
vibe-debug --project-root sample_app failures
vibe-debug --project-root sample_app explain
```

Or refresh your browser at `http://localhost:6006` to inspect the visual trace waterfall.

---

## CLI Reference

The `vibe-debug` CLI provides single-command management for services, diagnostics, and failure analysis:

| Command | Description |
|---|---|
| `vibe-debug up` | Starts the Phoenix trace collector & web UI on `localhost:6006` in the background with health verification. |
| `vibe-debug down` | Gracefully terminates the managed Phoenix server. |
| `vibe-debug init` | Statically scans your project using AST and generates `vibedebug.toml`. |
| `vibe-debug status` | Displays Phoenix reachability, active project configuration, and cached components count. |
| `vibe-debug map` | Prints discovered entry points, pipeline components, call counts, and failure statuses. |
| `vibe-debug failures` | Lists recent error spans, durations, and error types. |
| `vibe-debug explain` | Analyzes and explains the root cause of the latest failure. |
| `vibe-debug doctor` | Smoke-checks Python version, configuration validity, and Phoenix availability. |
| `vibe-debug mcp` | Starts the stdio Model Context Protocol (MCP) server for IDE assistants. |

*(Add `--project-root <path>` to any command to target a specific project folder).*

---

## IDE MCP Integration

`vibe-debug` exposes an MCP server so your AI coding assistant (Cursor, VS Code, Claude Desktop, Antigravity) can inspect traces without reading raw files.

### Available MCP Tools

- `why_did_this_fail()` — Full structured failure analysis: crash point, file, line, inputs, error message, pre-failure execution history, and downstream components that never ran.
- `get_last_failure()` — Concise component name, source file, line number, and error status of the most recent failure.
- `get_pipeline_map()` — Discovered project pipeline, entry points, and components never observed running.
- `explain_trace()` — Waterfall summary of the most recent trace.
- `suggest_fix()` — Automated fix recommendations based on failure pattern.
- `get_component_history()` — Historical failure counts and latency statistics for a given component.
- `trace_summary()` — Aggregated metrics across all captured traces.

### Registering in Other IDEs

#### A. Cursor
1. Go to **Settings** (`Ctrl+,` or `Cmd+,`) ➔ **Features** ➔ **MCP**.
2. Click **+ Add New MCP Server**.
3. Configure:
   * **Name**: `vibe-debug`
   * **Type**: `command`
   * **Command**: `python -m vibe_debug.cli --project-root "C:/path/to/your/project" mcp`

#### B. VS Code (Cline / Roo Code / Claude Extension)
Add to your `mcp_settings.json` or `.vscode/mcp.json`:

```json
{
  "mcpServers": {
    "vibe-debug": {
      "command": "python",
      "args": [
        "-m",
        "vibe_debug.cli",
        "--project-root",
        "C:/path/to/your/project",
        "mcp"
      ],
      "env": {
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

#### C. Claude Desktop
Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "vibe-debug": {
      "command": "python",
      "args": [
        "-m",
        "vibe_debug.cli",
        "--project-root",
        "C:/path/to/your/project",
        "mcp"
      ]
    }
  }
}
```

Once registered, you can ask in the IDE chat:
> *"Why did my pipeline fail?"*
> *"What components were never reached?"*
> *"Show me the pipeline map"*

---

## Errors and Failures Captured

`vibe-debug` is designed to capture common development pitfalls, especially for AI pipelines and beginner developers:

1. **Unhandled Exceptions:**
   `ValueError`, `TypeError`, `KeyError`, `ZeroDivisionError`, etc., are captured with full stack trace, function inputs, file path, and exact line number, then cleanly re-raised so runtime behavior is preserved.
2. **"Silent Failures" (Empty Returns):**
   Functions returning `None`, `[]`, `""`, or `{}` (forgotten return statements, failed searches, or empty vector queries) are automatically flagged with `SILENT_FAILURE` status.
3. **Cascade "Never Reached" Failures:**
   When step A crashes, steps B and C never execute. `vibe-debug` marks downstream dependencies as `NEVER_REACHED`, preventing confusion over which component actually failed.
4. **Syntax Errors During Scans:**
   The static analyzer (`ProjectStaticAnalyzer`) wraps parsing in exception guards. If code has syntax errors during development, `vibe-debug` continues mapping the rest of the project rather than crashing.
5. **Import & Module Errors:**
   If a configured module cannot be imported, a clean warning is logged rather than aborting startup. Runtime import failures inside functions are captured as `ERROR` spans.
6. **Network & Database Outages:**
   Outbound HTTP and socket requests (e.g. Redis, Ollama, OpenAI, external APIs) are intercepted by `IOInterceptor`. Connection refused or timeout errors are marked with destination host and port.

---

## Beyond AI: Factory Chains & Sequential Protocols

While `vibe-debug` was designed with AI pipelines in mind, its underlying architecture is a general **Directed Acyclic Graph (DAG) with step-by-step state observation**. This allows `vibe-debug` to be applied to any sequential process, manufacturing pipeline, or industrial factory chain operation:

| `vibe-debug` Concept | AI Pipeline Equivalent | Factory Chain Equivalent |
|---|---|---|
| **Pipeline Map (`map`)** | Import graph (`retriever` ➔ `processor` ➔ `generator`) | Assembly line blueprint (Station 1 ➔ Station 2 ➔ Station 3) |
| **Span / Trace** | Single query execution | Single product / serial number moving down the line |
| **Span Attributes** | Query text, retrieved document count | Serial number, sensor readings, dimensions, batch ID |
| **Latency Tracking** | Model inference time (ms) | Cycle time / machine operation duration (ms) |
| **Silent Failure** | Function returned `None` or `[]` | Defective unit produced with missing parts, or empty packaging |
| **Downstream Never Reached** | Generator was skipped because retriever crashed | Packaging station is idle because upstream Welder faulted |
| **I/O Interception** | Outbound HTTP / socket calls | Sensor telemetry, PLC communication (MQTT, Modbus, TCP sockets) |
| **MCP AI Assistant** | *"Why did query #44 fail?"* | *"Why did line 2 stop on Batch 902?"* |

Because `vibe-debug` uses standard OpenTelemetry spans, it is protocol-agnostic and works for any sequential operation where stations transform payloads, experience latency, or risk cascading failures.

---

## Manual Decoration (Optional)

If you prefer explicit decoration over zero-config auto-instrumentation, use `@vibetrace`:

```python
from vibe_debug.decorators import vibetrace

@vibetrace(component_type="rag")
def retrieve_documents(query: str):
    """Fetches relevant documents from vector store."""
    return vector_store.search(query)

@vibetrace(component_type="agent")
async def async_agent_step(prompt: str):
    """Async coroutines are awaited properly within the span."""
    return await llm.ainvoke(prompt)
```

---

## Project Structure

```text
vibe-debug/
├── vibe_debug/
│   ├── __init__.py                    ← one-line start() entry point
│   ├── tracer.py                      ← OpenTelemetry tracer/export setup (BatchSpanProcessor)
│   ├── decorators.py                  ← sync & async @vibetrace decorator
│   ├── auto_instrument.py             ← wrapt-based zero-decorator patching
│   ├── cli.py                         ← vibe-debug console entry point (9 commands)
│   ├── config/
│   │   ├── __init__.py                ← config exports
│   │   ├── config.py                  ← vibedebug.toml reader
│   │   └── config_generator.py        ← auto-generates vibedebug.toml
│   └── runtime/
│       ├── __init__.py                ← runtime package marker
│       ├── static_analyzer.py         ← AST-based project scanner
│       ├── import_mapper.py           ← import dependency graph builder
│       ├── partial_trace_builder.py   ← crash + NEVER_REACHED assembly
│       ├── pipeline_discovery.py      ← unified pipeline map cache (capped at 500)
│       ├── sys_tracer.py              ← universal sys.settrace() hook
│       └── io_interceptor.py          ← HTTP/socket interception
├── server/
│   ├── mcp_bridge.py                  ← MCP server with 7 debugging tools
│   └── manager.py                     ← managed Phoenix server lifecycle
├── sample_app/                        ← 4-module realistic sample pipeline
│   ├── main.py                        ← pipeline orchestrator with --fail switch
│   ├── retriever.py                   ← document retriever
│   ├── processor.py                   ← document processor
│   ├── generator.py                   ← response generator
│   └── vibedebug.toml                 ← sample app configuration
├── tests/                             ← unit, CLI, runtime, and e2e sample app tests
├── IMPLEMENTATION_TASKS.md            ← checkable task delivery record
├── vibedebug.toml                     ← root watch list
└── .vibe_pipeline_cache.json          ← persisted discovered pipeline map
```

---

## License

See [LICENSE](LICENSE).
