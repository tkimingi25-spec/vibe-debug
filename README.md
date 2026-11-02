# vibe-debug

`vibe-debug` is a Python debugging and pipeline-observability toolkit. It traces Python functions as OpenTelemetry spans, records exceptions and silent failures (functions that return empty results without raising), exports the spans to a local Arize Phoenix dashboard, and exposes the evidence through a CLI and MCP tools for AI coding assistants. An optional Muse Spark integration can turn recorded failure evidence into a structured explanation in English or Swahili; it is off by default and is not required for any deterministic feature.

## Why Vibe-Debug exists

Pipelines fail in ways that are hard to see after the fact:

- execution crosses many nested functions, and the final error does not show what ran before it;
- a function returns `None`, `[]`, `""`, or `{}` instead of raising, and downstream code fails later or silently produces nothing;
- sharing execution evidence with another developer, or with an AI assistant, usually means copying logs by hand;
- telemetry export failures can be mistaken for application behaviour.

Vibe-Debug records each traced call as a span with its inputs, outputs, file, line, duration, and status, so a failure can be inspected afterwards in Phoenix, in the terminal, or through MCP tools.

## Key features

Deterministic features (no AI involved, work offline against local Phoenix):

- Manual tracing of sync and async functions with the `@vibetrace` decorator.
- Automatic instrumentation of modules listed in configuration, plus optional universal runtime tracing.
- Exception capture: the span records the error and the exception is re-raised, so application behaviour is unchanged.
- Silent-failure capture for empty/falsy results (see "Understanding spans and failures").
- Outbound HTTP/TCP interception spans (`requests`, `httpx`, sockets).
- Static pipeline mapping (AST analysis) before code runs.
- CLI diagnostics (`up`, `down`, `status`, `map`, `failures`, `explain`, `doctor`, `init`, `mcp`).
- MCP tools so an IDE assistant can ask why a pipeline failed.
- Redaction and truncation of span attributes before export.
- Honest export-health reporting based on actual exporter results.

Optional feature (off by default, requires an API credential):

- Muse Spark explanations of a recorded failure, in English (`en`) or Swahili (`sw`), with deterministic fallback when unavailable.

## Architecture at a glance

```text
Traced application
  -> OpenTelemetry spans (inputs/outputs redacted and truncated)
  -> Phoenix (local dashboard + GraphQL)
  -> Shared span fetching (vibe_debug/phoenix_client.py)
  -> CLI / MCP tools / optional Muse Spark explanation
```

Redaction happens twice at the boundaries that matter: when span attributes are written (before export to Phoenix), and again when AI evidence is built (before anything is sent to Muse Spark).

## Requirements

- Python 3.11 or later (`requires-python = ">=3.11"`).
- Arize Phoenix is installed as a package dependency; no separate Phoenix install is needed.
- No internet or API key is required for deterministic features. The optional Muse Spark explanation feature does call an external API.

## Installation

Install from a local clone of this repository:

```powershell
git clone https://github.com/tkimingi25-spec/vibe-debug.git
cd vibe-debug
pip install .
```

For development (editable install plus test dependencies):

```powershell
pip install -e ".[dev]"
```

Verify the installation:

```powershell
vibe-debug --help
```

To update after dependencies change, re-run `pip install .` (or `pip install -e ".[dev]"` for an editable install). `pyproject.toml` is the single dependency source; there is no `requirements.txt`, and you should not create one.

> Installation from PyPI is not claimed here: the commands above install from the local repository, which is the verified path.

## Quick start

1. Start Phoenix:

   ```powershell
   vibe-debug up
   ```

   Open the dashboard at [http://localhost:6006](http://localhost:6006) (adjust the port if you run Phoenix on a custom port — see "Configuration reference").

2. Trace your code. The smallest setup is one line in your application entry point, plus decorated functions:

   ```python
   import vibe_debug

   vibe_debug.start("my-project")

   @vibe_debug.vibetrace(component_type="rag")
   def retrieve_documents(query: str):
       return [{"title": "Doc A", "text": "..."}]

   if __name__ == "__main__":
       print(retrieve_documents("What is vibe-debug?"))
   ```

   `vibe_debug.start()` statically scans the project, creates `vibedebug.toml` if missing, initialises the tracer, auto-instruments the modules listed under `[watch] modules`, starts HTTP interception, and activates framework adapters. It does **not** start Phoenix; use `vibe-debug up` for that.

3. Run your application:

   ```powershell
   python your_app.py
   ```

4. Inspect the results:

   ```powershell
   vibe-debug status
   vibe-debug failures
   vibe-debug explain
   ```

   Or refresh the Phoenix dashboard to see the trace waterfall.

5. Stop Phoenix:

   ```powershell
   vibe-debug down
   ```

A realistic 4-module sample pipeline is included in [`sample_app/`](sample_app/README.md):

```powershell
vibe-debug up
python sample_app/main.py
python sample_app/main.py --fail
vibe-debug --project-root sample_app status
vibe-debug --project-root sample_app map
vibe-debug --project-root sample_app failures
vibe-debug --project-root sample_app explain
```

## Manual tracing

Import and decorate functions directly when you want explicit control:

```python
import asyncio

import vibe_debug
from vibe_debug.decorators import vibetrace

vibe_debug.start("my-project")

@vibetrace(component_type="rag")
def retrieve_documents(query: str):
    return [{"title": "Doc A"}]

@vibetrace(component_type="agent")
async def async_agent_step(prompt: str):
    return f"answer to: {prompt}"

@vibetrace(component_type="pipeline")
def run_pipeline(query: str):
    docs = retrieve_documents(query)
    return docs

if __name__ == "__main__":
    print(run_pipeline("hello"))
    print(asyncio.run(async_agent_step("hello")))
```

- Sync and async functions are both supported; async functions are awaited properly inside the span.
- Nested traced calls produce parent/child spans in one trace.
- Arguments and return values are recorded (after redaction/truncation — see "Privacy and security").
- Exceptions are recorded on the span (`error.type`, status) and then re-raised unchanged.
- Empty results are flagged as silent failures (next section).

Use `init_vibedebug(project_name)` directly if you only want tracer setup without the scan/config/auto-instrumentation performed by `start()`.

## Automatic instrumentation

`vibe_debug.start()` enables three layers; know which one you are getting:

| Layer | How to enable | What it traces | Limits |
|---|---|---|---|
| Config auto-instrumentation | Always on in `start()` | Functions in modules listed under `[watch] modules` in `vibedebug.toml` | Only listed modules; mutations elsewhere are not traced |
| Universal runtime tracing | `vibe_debug.start(..., universal_trace=True)` | Project function calls via `sys.settrace` | Project files only; adds overhead; captures call/return previews, not full decorator semantics |
| I/O interception | Always on in `start()`; socket level only with `patch_socket=True` | Outbound `requests`/`httpx` calls, and TCP connections when socket patching is enabled | Patching is process-wide while active |

Configured redaction and truncation apply to auto-instrumented functions exactly as they do to decorated ones (auto-instrumentation wraps through the same decorator). There is no separate "stop" API for `start()`'s layers in the same process; use a fresh process, or `reset_tracer()` (see "Phoenix lifecycle and tracer reset") for tracer state.

## Understanding spans and failures

Every traced call records `vibe.input`, `vibe.output`, `vibe.file`, `vibe.line`, `vibe.module`, duration, and status.

- **Successful span:** status `OK`; the function returned a truthy result.
- **Exception:** status `ERROR`; the exception is recorded and re-raised.
- **Silent failure:** status `ERROR` with a `silent_failure:` status detail. A result counts as a silent failure when it is `None`, `False`, numeric zero (`0`, `0.0`, `Decimal("0")`), or an empty `str`/`bytes`/`list`/`tuple`/`dict`/`set`. Truthy results are never silent failures.

Deliberately not detected: secrets inside free-text values, failures that return non-empty-but-wrong results, and exceptions raised by a return value's own comparison methods (detection is non-throwing by design — a value whose `__eq__` or `__bool__` raises is treated as not a silent failure and passes through untouched).

## CLI guide

Global option: `--project-root <path>` (default: current directory) targets a specific project folder.

| Command | Purpose | Example output |
|---|---|---|
| `vibe-debug init` | Statically scan the project and generate `vibedebug.toml` | Prints discovered components/entry points |
| `vibe-debug up` | Start managed Phoenix in the background and wait for health | `Phoenix ready at http://127.0.0.1:6006` |
| `vibe-debug down` | Stop the managed Phoenix (identity-verified, see lifecycle section) | `Managed Phoenix stopped.` |
| `vibe-debug status` | Phoenix reachability, project config, cached component count | Counts and project name |
| `vibe-debug map` | Print discovered entry points, components, call counts, failure statuses | Pipeline component list |
| `vibe-debug failures` | List recent `ERROR` spans, newest first | `ERROR route_query (5012.0ms) RouterError` style lines |
| `vibe-debug explain [--language en\|sw]` | Deterministically explain the first recent error span; optional AI explanation (see AI section) | Failure explanation block |
| `vibe-debug doctor` | Smoke-check Python, config, Phoenix reachability, and this process's export health | `OK`/`WARN` lines plus a `Telemetry export:` line |
| `vibe-debug mcp` | Run the stdio MCP server for IDE assistants | MCP protocol on stdio |

Common failure conditions: `failures`/`explain` print a "no failures / could not reach Phoenix" style message when Phoenix is not running; `up` reports if Phoenix does not become ready in time. Run `vibe-debug <command> --help` for exact options.

## Phoenix dashboard

`vibe-debug up` serves the dashboard at the resolved base URL — `http://localhost:6006` by default, or your custom host/port when configured. In the dashboard, look for: the failing span in the trace waterfall, its file/line and error status, parent/child relationships, and input/output attributes (already redacted). Phoenix ingestion can lag a few seconds behind span export; if a just-run trace is missing, wait briefly and refresh.

## MCP integration

`vibe-debug mcp` starts a stdio MCP server so an AI coding assistant (Cursor, VS Code, Claude Desktop, etc.) can inspect traces without reading raw files. Available tools (exact names):

- `get_last_failure()` — most recent failure: component, file, line, error status.
- `why_did_this_fail(language=None)` — structured failure analysis; optional `language` (`"en"`/`"sw"`) adds a Muse Spark explanation when configured.
- `explain_trace(trace_id=None)` — waterfall summary of the most recent (or given) trace.
- `get_pipeline_map()` — discovered pipeline, entry points, components never observed.
- `suggest_fix()` — deterministic fix recommendations based on the failure pattern.
- `trace_summary()` — aggregated metrics across captured traces.
- `get_component_history()` — failure counts and latency statistics per component.

Example client configuration (adjust the project path):

```json
{
  "mcpServers": {
    "vibe-debug": {
      "command": "python",
      "args": ["-m", "vibe_debug.cli", "--project-root", "C:/path/to/your/project", "mcp"]
    }
  }
}
```

MCP tools read spans from the same resolved Phoenix endpoint as the CLI, so a custom Phoenix port works for MCP tools too.

## Configuration reference

Configuration lives in `vibedebug.toml` in the project root (created by `vibe-debug init` or automatically by `vibe_debug.start()` when missing). Minimal valid example:

```toml
[project]
name = "my-project"

[tracer]
otlp_endpoint = "http://localhost:6006/v1/traces"

[watch]
modules = ["my_pipeline"]
ignore_functions = ["__init__", "__repr__", "__str__"]

[redaction]
max_attr_length = 1024
redact_keys = []

[explainer]
enabled = false
model = "muse-spark-1.3"
endpoint = "https://api.meta.ai/v1/chat/completions"
timeout_seconds = 10.0
max_evidence_size = 8000
language = "en"
```

Notes on sections:

- `[tracer] otlp_endpoint` is where spans are **sent** (OTLP/HTTP). The Phoenix **query** base URL is derived from it by stripping the path — do not append `/graphql` yourself.
- `[redaction] redact_keys` **extends** the built-in sensitive-key list (it does not replace it). `[redaction] max_attr_length` truncates span attributes.
- `[explainer]` settings are optional; see the AI section. Never put an API key in this file.

### Endpoint precedence

Every component resolves the Phoenix query base URL the same way (`resolve_base_url` in `vibe_debug/phoenix_endpoint.py`):

1. Explicit argument (where a function/option provides one);
2. `PHOENIX_HOST` / `PHOENIX_PORT` environment variables;
3. The configured `otlp_endpoint`, parsed down to scheme + host + port;
4. Default: `http://localhost:6006`.

### Environment variables

| Variable | Purpose |
|---|---|
| `PHOENIX_HOST`, `PHOENIX_PORT` | Phoenix location for the manager and endpoint resolution |
| `OTLP_ENDPOINT` | Overrides the OTLP span-export endpoint in the tracer |
| `NO_PROXY` | e.g. `localhost,127.0.0.1`; useful for *other* tools behind a proxy (Vibe-Debug's own loopback calls skip proxies automatically) |
| `MUSE_SPARK_API_KEY` | Muse Spark credential for the optional AI explanations — environment only, never a config file |
| `VIBE_PROJECT_ROOT` | Set by the CLI when launching the MCP server |
| `VIBE_EXPLAINER_LIVE` | Set to `1` only to opt into the live-provider test in the test suite |

`.env` files: Vibe-Debug's tracer code loads a `.env` file if one is present in the working directory (via `python-dotenv`), and Phoenix server processes inherit the process environment. Treat any `.env` as local secret storage: it must be git-ignored and must never be committed. The API key in particular must only ever live in the process environment or such a local, ignored file.

## Privacy and security

What is captured on spans: function name, module, file, line, arguments, return values, duration, status, and (for I/O interception) request host/URL/method/status and a bounded request preview.

Redaction and truncation behaviour (implemented in `vibe_debug/redaction.py`, applied before export):

- Key-name-based redaction: keys such as `api_key`/`apikey`/`api-key`, `access_token`/`auth_token`/bare `token`, `client_secret`/bare `secret`, `password`/`passwd`, `authorization`, `private_key`, `cookie`/`set-cookie` are replaced with `***`, case-insensitively, including nested dicts/lists. `token_count` and `secret_count` are **not** redacted (no substring matching).
- HTTP interception redacts sensitive URL query values, URL authority passwords (`user:***@host`), query-like fragments, and structured request bodies before recording them.
- Attributes are truncated to the configured `max_attr_length` (default 1024) including the `…[truncated]` marker.
- Positional arguments matched to declared parameter names are redacted by that name. `*args` and unknown-signature positionals cannot be matched, and secrets inside free-text values under non-sensitive keys are **not** detected — redaction is key-name matching, not content scanning.
- `prompt`-like fields are **not** redacted by default; add them via `[redaction] redact_keys` (which extends the defaults) if your traces contain sensitive prompts.
- Redaction never raises and never falls back to the raw value when a value cannot be stringified.

Redaction reduces risk; it does not make traces completely safe. Phoenix stores spans locally on disk, and free-text can still contain anything your code puts in it. Review sensitive traces before sharing them.

## Export health and diagnostics

`vibe-debug doctor` prints, in addition to its `OK`/`WARN` checks, exactly one export-health line derived from this process's actual exporter results:

- `Telemetry export: OK (last success 3s ago; successes=4, failures=0)`
- `Telemetry export: FAILED (last error: <bounded error text>; successes=0, failures=1)`
- `Telemetry export: no export attempted yet`

Health is recorded from the exporter's real `export()` outcomes (counted per export batch, under a lock), never from a span merely being queued. Two separate concepts appear in `doctor`: **Phoenix reachability** (can this machine talk to Phoenix?) and **this process's export success** (did *this* process's exporter actually deliver?). Export health is per-process: `doctor` in a fresh process will usually say "no export attempted yet" even while another process exports happily.

## AI explanations and Swahili

Without AI, `explain` and `why_did_this_fail` print deterministic facts: component, status, latency/error, and (for MCP) a structured analysis with inputs/outputs and downstream impact.

To enable Muse Spark explanations:

1. Set the credential in the process environment (never in `vibedebug.toml`):

   ```powershell
   $env:MUSE_SPARK_API_KEY = "<your key>"
   ```

2. Either enable it in configuration (`[explainer] enabled = true`), or explicitly request a language at call time.

Usage:

```powershell
vibe-debug explain --language en
vibe-debug explain --language sw
```

In MCP, call `why_did_this_fail` with `{"language": "sw"}`.

Language values: `en`, `sw`, and the aliases `English`, `Swahili`, `Kiswahili` all map to the canonical codes. Precedence: explicit CLI/MCP argument, then configuration `language`, then `en`. Unsupported languages fall back to the deterministic explanation with a clear note.

Privacy semantics, stated plainly: **requesting an explanation this way sends one small, redacted evidence package to the configured Muse Spark API.** This happens when the explainer is enabled in config, or when you explicitly pass `--language` / `language` (an explicit language request triggers one live call when the credential is present, even if `enabled = false`). No evidence is sent when the feature is disabled and no qualifying explicit request is made. The package contains only the failing span's function/status/error/duration, bounded input/output previews, and a bounded list of related spans — every value redacted first.

The model must return a validated structure (`summary`, `likely_cause`, `evidence`, `suggested_fix`, `confidence`, `missing_information`); invalid, slow, or failing responses fall back to the deterministic explanation. Identifiers (`route_query`, `TimeoutError`, file paths, JSON keys) are preserved by instruction and by verbatim rendering. Expect live calls to take seconds (about 11–13s observed); each explanation is at most one API call, so cost scales with explanations requested, not with trace volume.

Verification status: mocked English/Swahili rendering is fully tested; one live Swahili response was reviewed by the project user and found satisfactory. Broader live Swahili quality across failure types is still being tested — do not treat the feature as translation-certified. Timeout enforcement on the default transport at the configured `timeout_seconds` default is a known follow-up clarification (observed live latencies exceeded 10s), so do not rely on a hard 10s ceiling yet.

## Phoenix lifecycle and tracer reset

- `vibe-debug up` launches Phoenix as a managed child process and records a JSON fingerprint (`{version, pid, created, marker}`) in the project-scoped `.vibe_phoenix.pid` file.
- `vibe-debug down` verifies that the recorded PID is still that same Phoenix process (creation time, plus command-line marker where the platform provides it) **before** signalling it. PIDs are recycled by the OS; an unverified PID is never killed.
- Stale or mismatched PID files are removed. On platforms where identity cannot be verified — including old plain-integer PID files from previous versions — `down` refuses, keeps the PID file, and you must remove the stale Phoenix process manually. That is the correct safe default: silently killing an unverified PID is precisely the bug this behaviour removes.
- Signal used and PID-file location are unchanged from earlier versions; only the identity gate is new.

For tests or long-running processes that need to re-point tracing:

```python
from vibe_debug.tracer import init_vibedebug, reset_tracer

init_vibedebug("project-a")
# ... later, in the same process:
reset_tracer()
init_vibedebug("project-b")
```

`reset_tracer()` shuts the current provider down (failures swallowed), clears provider and export-health references so `doctor`-style health reads report the pre-initialisation state, and is a harmless no-op when never initialised or called twice. After reset + re-init, Vibe-Debug's own tracer acquisition (the decorators, `init_vibedebug`'s return value) genuinely uses the **new** provider. Limitation: OpenTelemetry's global provider can be set only once per process, so code that already obtained tracers via `trace.get_tracer()`, and runtime tracers constructed earlier, keep the first provider.

## Examples

Successful, failing, and silent-failure functions:

```python
import vibe_debug

vibe_debug.start("examples")

@vibe_debug.vibetrace(component_type="rag")
def fetch_documents(query: str):
    return [{"title": "Doc A"}]

@vibe_debug.vibetrace(component_type="rag")
def explode(query: str):
    raise RuntimeError("vector store unavailable")

@vibe_debug.vibetrace(component_type="rag")
def empty_search(query: str):
    return []  # flagged as a silent failure
```

Then inspect with `vibe-debug failures` and `vibe-debug explain`. For a nested pipeline, decorate each step; spans nest automatically. For AI explanations:

```powershell
vibe-debug explain --language en   # English AI explanation (if configured/requested)
vibe-debug explain --language sw   # Swahili AI explanation
```

## Troubleshooting

- **Phoenix does not start:** run `vibe-debug doctor`; check the port is free and that `phoenix` is importable in the same environment.
- **Wrong port / custom port:** set `PHOENIX_HOST`/`PHOENIX_PORT` (or `[tracer] otlp_endpoint`); CLI, MCP, and manager all resolve the same base URL. The dashboard URL changes with the port.
- **No spans arriving:** confirm Phoenix is healthy (`GET /healthz` on the resolved port), confirm your functions are decorated or their module is under `[watch] modules`, and allow a few seconds for ingestion lag before concluding spans are lost.
- **Proxy errors (`502`, connection through a dead proxy):** Vibe-Debug's own loopback Phoenix calls skip environment proxies automatically; remote Phoenix keeps normal proxy behaviour. For other tooling, set `NO_PROXY=localhost,127.0.0.1`.
- **Exporter failures:** `vibe-debug doctor`'s `Telemetry export:` line distinguishes "Phoenix unreachable" from "this process failed to export". "No export attempted yet" in a fresh `doctor` process is expected (per-process state).
- **AI explanation unavailable:** check `MUSE_SPARK_API_KEY` is set in the process environment, `[explainer] enabled` or an explicit `--language` was given, and the configured endpoint/model are reachable. Any failure falls back to the deterministic explanation rather than failing the command.
- **Invalid AI response / unsupported language:** both fall back deterministically; unsupported languages also print a clear note.
- **Phoenix will not stop:** if `down` refuses, the recorded PID could not be verified as Phoenix (see lifecycle section). Check for a stale `.vibe_phoenix.pid`; remove the stale Phoenix process manually in that case.
- **API-key safety:** never commit `.env`, never paste keys into config files or issues. If a key may have leaked, rotate it with your provider.

## Development

Repository layout (abridged):

```text
vibe_debug/            package: start(), decorators, tracer, redaction, explainer,
                       endpoint resolution, shared span fetch, config, runtime/
server/                MCP bridge (mcp_bridge.py) and Phoenix manager (manager.py)
sample_app/            4-module sample pipeline (see sample_app/README.md)
tests/                 pytest suite (unit, CLI, MCP, lifecycle, live-Phoenix-gated)
```

Run tests (PowerShell, from the repo root):

```powershell
pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest tests/test_redaction.py -q   # focused example
```

Full suite: start Phoenix first (`vibe-debug up`), confirm `http://localhost:6006/healthz` returns 200, run `pytest tests/`, then `vibe-debug down`. Tests that need a live Phoenix are skipped or gated when it is not running. Two live-Phoenix tests (`test_auto_instrumentation`, `test_universal_tracer_captures_project_functions`) query Phoenix immediately after exporting and can intermittently fail from ingestion lag; they pass on isolated re-runs — treat an in-suite failure of exactly those tests as a known flake, not a regression, after verifying with a re-run.

Dependency rule: `pyproject.toml` is the single dependency source (Phoenix and other pins live there). Do not add a `requirements.txt`.

## Known limitations

- Export health is per-process; a fresh `doctor` cannot see another process's exporter state.
- Redaction is key-name-based only: free-text secrets under non-sensitive keys are not detected, and some positional-argument shapes (`*args`, unknown signatures) cannot be key-matched. `prompt` redaction is opt-in via config.
- After `reset_tracer()`, the OpenTelemetry global provider still points at the first provider; only Vibe-Debug's own acquisition path switches.
- PID identity verification is best-effort on some platforms (notably macOS) and unavailable for legacy plain-int PID files; `down` refuses there by design.
- Phoenix ingestion lag can make very fresh spans briefly invisible to CLI/MCP queries.
- AI explanations can be wrong (hallucination risk), cost money/latency per request, and depend on external API availability; Swahili quality has been spot-checked, not certified.
- Timeout enforcement on the default Muse Spark transport at the configured default is not yet confirmed (follow-up).
- Auto-instrumentation covers configured modules (and, optionally, project functions via `sys.settrace`); it is not a whole-interpreter tracer.

## Project status

Implemented and tested: pinned dependencies in `pyproject.toml` (no `requirements.txt`); non-throwing silent-failure detection; redaction + truncation (including a correction pass that closed an independent audit); single Phoenix endpoint resolution; loopback-safe HTTP transport; shared CLI/MCP span fetching; honest export-health reporting; safe Phoenix lifecycle with PID identity verification and `reset_tracer()`; optional Muse Spark explanations in English and Swahili (live-verified once per language). Remaining future work is limited to clarifications and broader live testing noted under Known limitations; no further features are claimed here.

## License

See [LICENSE](LICENSE).
