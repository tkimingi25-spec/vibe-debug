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
- **Silent failure:** status `ERROR` with a "Silent Failure detected" status detail. By default a result counts as a silent failure when it is `None` or an empty `str`/`list`/`tuple`/`dict`/`set`/`frozenset`. Detection never invokes a result's own `__eq__`/`__bool__`, and never raises.

Opt-in per-decorator predicate: `@vibetrace("rag", silent_if=lambda r: isinstance(r, dict) and not r.get("valid"))` flags results your own predicate judges invalid (for example non-empty-but-wrong results). The predicate runs inside the same never-raising guard: a predicate that raises is treated as "not flagged" and can never break the traced call. Passing `silent_if=len` flags Sized results with `len(result) == 0` (including custom `__len__` types not covered by the default structural check).

Deliberately not detected by default: secrets inside free-text values, and failures that return non-empty-but-wrong results (use `silent_if` for those).

## CLI guide

Global option: `--project-root <path>` (default: current directory) targets a specific project folder.

| Command | Purpose | Example output |
|---|---|---|
| `vibe-debug init` | Statically scan the project and generate `vibedebug.toml` | Prints discovered components/entry points |
| `vibe-debug up` | Start managed Phoenix in the background and wait for health | `Phoenix ready at http://127.0.0.1:6006` |
| `vibe-debug down [--force]` | Stop the managed Phoenix (identity-verified, see lifecycle section); `--force` signals an unverifiable recorded PID (dangerous, see lifecycle section) | `Managed Phoenix stopped.` |
| `vibe-debug status` | Phoenix reachability, project config, cached component count | Counts and project name |
| `vibe-debug map` | Print discovered entry points, components, call counts, failure statuses | Pipeline component list |
| `vibe-debug failures [--wait <seconds>]` | List recent `ERROR` spans, newest first; `--wait` polls for fresh spans (ingestion lag), default `0` = no wait | `ERROR route_query (5012.0ms) RouterError` style lines |
| `vibe-debug explain [--language en\|sw] [--wait <seconds>]` | Deterministically explain the first recent error span; optional AI explanation (see AI section); `--wait` polls as above | Failure explanation block |
| `vibe-debug doctor` | Smoke-check Python, config, Phoenix reachability, export health, and tracing coverage | `OK`/`WARN` lines, a `Telemetry export:` line, and a `Tracing coverage:` section |
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
privacy_profile = "permissive"

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
- `[redaction] privacy_profile` selects the redaction profile: `permissive` (default; key-name matching only — today's behaviour), `balanced` (plus deterministic pattern scanning for Bearer tokens, private-key blocks, JWTs, and common API-key prefixes), or `strict` (balanced plus payload keys `prompt`/`messages`/`content`/`input`/`output` redacted by name). Unknown values fall back to `permissive`. See Privacy and security for how to choose.
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
- Positional arguments matched to declared parameter names are redacted by that name. Under the default profile, `*args` and unknown-signature positionals cannot be matched, and secrets inside free-text values under non-sensitive keys are **not** detected — redaction is key-name matching, not content scanning.
- `prompt`-like fields are **not** redacted by default; add them via `[redaction] redact_keys` (which extends the defaults) if your traces contain sensitive prompts, or use the `strict` profile.
- Redaction never raises and never falls back to the raw value when a value cannot be stringified.

Privacy profiles (`[redaction] privacy_profile`, default `permissive` — choosing one is a trade-off, so the default does not change existing behaviour):

- `permissive`: key-name matching only, as above.
- `balanced`: additionally scans the rendered text for recognisable secret patterns — Bearer [REDACTED] (token ≥ 8 chars), `-----BEGIN … PRIVATE KEY-----` blocks, JWT-shaped strings, and common API-key prefixes (`sk-…`, `ghp_…`, `AKIA…`, `xox…`, etc.) — and replaces them with `***`, preserving surrounding text. This applies to span inputs/outputs (including `*args` previews), HTTP URL/body previews, runtime previews, and AI evidence when the profile is configured for that path. Pattern scanning can false-positive: a JWT-shaped documentation example or an `sk-`-prefixed identifier is indistinguishable from a real secret and will be redacted. Short or incomplete lookalikes (two-part JWT fragments, `sk-short`, the bare word `Bearer`) are preserved.
- `strict`: everything in `balanced`, plus the payload keys `prompt`, `messages`, `content`, `input`, and `output` are redacted by key name — use this if prompts or model payloads must not be stored in Phoenix at all. Expect span previews to become much less informative.

Choose `balanced` if free-text may contain pasted tokens; choose `strict` if prompts/payloads themselves are sensitive; stay on `permissive` if you need full preview fidelity and control key names yourself.

Redaction reduces risk; it does not make traces completely safe. Phoenix stores spans locally on disk, and free-text can still contain anything your code puts in it. Review sensitive traces before sharing them.

## Export health and diagnostics

`vibe-debug doctor` prints, in addition to its `OK`/`WARN` checks, exactly one export-health line derived from this process's actual exporter results:

- `Telemetry export: OK (last success 3s ago; successes=4, failures=0)`
- `Telemetry export: FAILED (last error: <bounded error text>; successes=0, failures=1)`
- `Telemetry export: no export attempted yet`

Health is recorded from the exporter's real `export()` outcomes (counted per export batch, under a lock), never from a span merely being queued. Two separate concepts appear in `doctor`: **Phoenix reachability** (can this machine talk to Phoenix?) and **export success** (did an exporter actually deliver?).

Each recorded export outcome is also persisted to a project-scoped state file (`.vibe_export_health.json` in the working directory, following the PID-file conventions). It contains only the timestamp, status, success/failure counters, a bounded last-error text, the endpoint, and the writer PID — no span contents and no secrets. Writes are write-temp-then-rename and never raise into the export path. A fresh `doctor` process with no in-process health reads this snapshot and labels it honestly, e.g. `Telemetry export: OK (last success 42s ago, recorded by pid 12345)`; a snapshot older than one hour is labelled `stale snapshot`. A corrupt or partial file degrades to "no export attempted yet" without raising. The snapshot is the most recent recorded outcome, not a live guarantee: it can be stale, and it says nothing about exports after it was written.

`doctor` also prints a `Tracing coverage:` section (local config/cache only — it works with Phoenix down and says so): the watched/configured modules from `[watch] modules`, any configured modules that cannot be found, the auto-instrumentation state, the universal-tracing state in the doctor process itself, and recently observed traced function names from the pipeline cache. If a function produced no span, check here first: its module may not be configured, may not be found, or the app process may never have enabled the relevant tracing.

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

Verification status: mocked English/Swahili rendering is fully tested; one live Swahili response was reviewed by the project user and found satisfactory. An opt-in live evaluation pack (8 fixed synthetic failure categories × English/Swahili, 16 calls, machine-scored only) was re-run after the citation validator correction: 16/16 responses were schema-valid, citation-accepted, and identified the failing span (English 8/8, Swahili 8/8). The previous 5/16 result primarily measured the overly strict citation validator, which rejected substantively correct evidence-field citations; it was not a measure of model quality. This score is a machine-scored result over eight synthetic categories only: it does not certify overall model or Swahili quality, and Swahili quality remains limited by the small sample and that one user-reviewed response. Machine scores are not human review. Do not treat the feature as translation-certified.

Timeout and citation discipline: the configured `explainer_timeout_seconds` is enforced by the underlying HTTP client (`httpx` timeout on the default transport); a provider call exceeding it returns the deterministic fallback within a bounded time. Responses are also citation-checked deterministically: quoted identifiers in the model's `evidence` must occur in the submitted evidence package — span and function names, status values such as `ERROR`, error types, evidence-field names, JSON keys, and input/output preview keys and values that were actually submitted are all permitted citations; an explanation citing an invented identifier absent from the submitted evidence is rejected and falls back. This checks citation tokens only — it is not complete fact-checking: a correctly citing explanation can still contain an incorrect interpretation, and the remaining hallucination risk stands.

## Phoenix lifecycle and tracer reset

- `vibe-debug up` launches Phoenix as a managed child process and records a JSON fingerprint (`{version, pid, created, marker}`) in the project-scoped `.vibe_phoenix.pid` file.
- `vibe-debug down` verifies that the recorded PID is still that same Phoenix process (creation time, plus command-line marker where the platform provides it) **before** signalling it. PIDs are recycled by the OS; an unverified PID is never killed.
- Stale or mismatched PID files are removed. On platforms where identity cannot be verified — including old plain-integer PID files from previous versions — `down` refuses and keeps the PID file. That is the correct safe default: silently killing an unverified PID is precisely the bug this behaviour removes.
- Intentional recovery: if you are **sure** the recorded PID is the stale Phoenix process, run `vibe-debug down --force`. It prints a clear warning that identity could NOT be verified, exactly which PID it signalled, and then signals that PID. There is no interactive prompt — the explicit flag is the confirmation. **Danger:** if the PID has been recycled by an unrelated process, `--force` will terminate that process. A fingerprint *mismatch* (recorded start time does not match the live process) is never signalled, even with `--force`; `down` removes that stale file instead. `vibe-debug doctor` detects a stale/unverifiable PID file and prints the applicable recovery instruction.
- Signal used and PID-file location are unchanged from earlier versions; only the identity gate is new.

For tests or long-running processes that need to re-point tracing:

```python
from vibe_debug.tracer import init_vibedebug, reset_tracer

init_vibedebug("project-a")
# ... later, in the same process:
reset_tracer()
init_vibedebug("project-b")
```

Calling `init_vibedebug()` again **without** `reset_tracer()` does not replace the provider: the new project name, endpoint, exporter, and other settings are ignored, the existing provider and span routing stay in effect, and one warning is printed per ignored call (`[vibe-debug] Tracer already initialized; new settings ignored. Call reset_tracer() before re-initializing.`). Always call `reset_tracer()` before reinitialising with different settings. `reset_tracer()` shuts the current provider down (failures swallowed), clears provider and export-health references so `doctor`-style health reads report the pre-initialisation state, and is a harmless no-op when never initialised or called twice. After reset + re-init, Vibe-Debug's own tracer acquisition (the decorators, `init_vibedebug`'s return value) genuinely uses the **new** provider, and Vibe-Debug's own runtime components (universal runtime tracer, I/O interceptor, partial trace builder) resolve the active tracer dynamically per use, so they re-point too — even if constructed before the reset. Limitation: OpenTelemetry's global provider can be set only once per process, so third-party code that already obtained tracers via `trace.get_tracer()` keeps the first provider; Vibe-Debug cannot redirect tracers it has already handed out.

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
- **No spans arriving:** confirm Phoenix is healthy (`GET /healthz` on the resolved port), confirm your functions are decorated or their module is under `[watch] modules`, and allow a few seconds for ingestion lag before concluding spans are lost. For a just-exported trace, `vibe-debug failures --wait 10` (or `explain --wait 10`) polls instead of returning the first empty result. Check `doctor`'s `Tracing coverage:` section: a configured-but-missing module is listed there.
- **Proxy errors (`502`, connection through a dead proxy):** Vibe-Debug's own loopback Phoenix calls skip environment proxies automatically; remote Phoenix keeps normal proxy behaviour. For other tooling, set `NO_PROXY=localhost,127.0.0.1`.
- **Exporter failures:** `vibe-debug doctor`'s `Telemetry export:` line distinguishes "Phoenix unreachable" from "export failed". In a fresh `doctor` process the line comes from the persisted `.vibe_export_health.json` snapshot and is labelled with its age, writer PID, and `stale snapshot` when older than an hour — if it still says "no export attempted yet", no process has recorded an export for this project yet.
- **AI explanation unavailable:** check `MUSE_SPARK_API_KEY` is set in the process environment, `[explainer] enabled` or an explicit `--language` was given, and the configured endpoint/model are reachable. Any failure falls back to the deterministic explanation rather than failing the command.
- **Invalid AI response / unsupported language:** both fall back deterministically; unsupported languages also print a clear note. An AI response citing span names that are not in the submitted evidence is likewise rejected and falls back.
- **Phoenix will not stop:** if `down` refuses, the recorded PID could not be verified as Phoenix (see lifecycle section). `doctor` prints the recovery instruction for the state it finds. Only if you are sure the recorded PID is the stale Phoenix process, use `vibe-debug down --force` — it warns, names the exact PID, and signals it; if that PID now belongs to an unrelated process, `--force` will kill that process. A fingerprint mismatch is never signalled, even with `--force`.
- **My prompt is/isn't stored:** prompts are stored by default under the `permissive` profile. To keep them out of Phoenix, set `[redaction] privacy_profile = "strict"` (redacts `prompt`/`messages`/`content`/`input`/`output` by name) or add `prompt` to `[redaction] redact_keys`. If previews look over-redacted under `balanced`/`strict`, pattern false positives (JWT-shaped examples, `sk-`-prefixed identifiers) are the likely cause; drop back to `permissive` or narrow with `redact_keys` only.
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

Each item states what was fixed or mitigated and **what remains true**, with severity (impact if it bites) and practical effect.

- **Export health is recorded per process, but persisted (mitigated).** Severity: low. The in-process health state still belongs to the exporting process, but every recorded outcome is written to `.vibe_export_health.json`, so a fresh `doctor` reports the most recent recorded outcome with its age and writer PID instead of always saying "no export attempted yet". What remains: the snapshot can be stale (labelled stale after one hour), reflects only the last recorded outcome per counter set, and cannot prove a *current* export is succeeding.
- **Redaction is key-name-based by default (mitigated, not eliminated).** Severity: medium — the main residual privacy risk. Under the default `permissive` profile, free-text secrets under non-sensitive keys are not detected, and `*args`/unknown-signature positionals cannot be key-matched; `prompt` is stored unless configured otherwise. `balanced` adds pattern scanning (Bearer/private-key/JWT/API-key prefixes) and `strict` additionally redacts payload keys, but patterns only catch recognisable shapes: an unrecognisable secret format in free text still reaches Phoenix storage, and pattern scanning false-positives on lookalikes (JWT-shaped examples, `sk-`-prefixed identifiers). No profile makes traces provably secret-free; review sensitive traces before sharing.
- **Phoenix ingestion lag makes fresh spans briefly invisible (mitigated).** Severity: low. Spans can take seconds to become queryable; `failures`/`explain` accept `--wait <seconds>` to poll (the shared fetch and the MCP bridge's internal fetch accept an equivalent `wait_seconds`, though it is not exposed as an MCP tool argument). What remains: with no wait (the default), an immediate query can legitimately return empty, and two live-Phoenix tests remain intermittently timing-sensitive for the same reason.
- **AI explanations can be wrong, slow, and cost money (mitigated, not eliminated).** Severity: medium. The configured timeout is now enforced by the HTTP transport (bounded fallback), and responses citing span names outside the submitted evidence are rejected — but citation checking is not fact-checking: a fluent, correctly-citing explanation can still assert a wrong cause. Observed live latency is ~11–13s per call and each explicit explanation is one paid API call. Swahili quality: mocked rendering fully tested, one live response user-reviewed as satisfactory; after the citation-validator correction, a machine-scored live run over 8 synthetic categories × en/sw scored 16/16 (the earlier 5/16 primarily measured the overly strict validator, not the model) — broader quality is still unproven and not certified.
- **Unverifiable PIDs strand users without `--force` (mitigated).** Severity: low (safe by design; the danger moved to the explicit flag). `down` still refuses unverifiable PIDs by default, and PID identity verification is best-effort on some platforms (notably macOS) and unavailable for legacy plain-int PID files. `vibe-debug down --force` is the intentional recovery, but it transfers the risk to the user: forcing a recycled PID terminates an unrelated process. A fingerprint mismatch is never signalled, even with `--force`. The verify→signal race (PID recycled in the milliseconds between check and signal) cannot be eliminated, only shrunk.
- **After `reset_tracer()`, handed-out tracers keep the first provider (mitigated for Vibe-Debug's own components).** Severity: low–medium in long-running/test processes. Vibe-Debug's decorators and runtime components now acquire the active tracer dynamically and genuinely re-point after reset + re-init. What remains: OpenTelemetry's global provider can be set only once per process, so third-party code holding tracers from `trace.get_tracer()` keeps exporting through the first provider.
- **Tracing coverage depends on configuration (mitigated).** Severity: low. `doctor` now reports watched/configured modules, configured-but-missing modules, and observed function names, and works with Phoenix down. What remains: coverage is still configuration-dependent — a function in an unwatched module, or in a process that never enabled tracing, produces no span; `doctor` runs in its own process and can only report configuration and cached observations, not another process's live tracing state.
- **Silent-failure detection is structural-emptiness only by default (mitigated).** Severity: low. Non-empty-but-wrong results are not flagged unless you opt in per decorator with `silent_if=...` (or `silent_if=len` for Sized results). What remains: without the opt-in, a plausible-looking wrong result looks successful; with it, the predicate is your own judgement call, evaluated under a never-raising guard.
- **Auto-instrumentation is not a whole-interpreter tracer (unchanged).** Severity: low. It covers configured modules (and, optionally, project functions via `sys.settrace`); library internals and unconfigured code are intentionally skipped.

## Project status

Implemented and tested: pinned dependencies in `pyproject.toml` (no `requirements.txt`); non-throwing silent-failure detection; redaction + truncation (including a correction pass that closed an independent audit); single Phoenix endpoint resolution; loopback-safe HTTP transport; shared CLI/MCP span fetching; honest export-health reporting; safe Phoenix lifecycle with PID identity verification and `reset_tracer()`; optional Muse Spark explanations in English and Swahili (live-verified once per language). A subsequent limitations pass added: persisted export health, bounded `--wait` span polling, enforced explainer timeout with citation validation, privacy profiles (`permissive`/`balanced`/`strict`), `down --force` stale-PID recovery, `doctor` tracing-coverage diagnostics with dynamic runtime tracers, opt-in `silent_if` predicates, and an 8-category AI evaluation pack (offline-tested; machine-scored live run at 16/16 after the citation-validator correction — the earlier 5/16 primarily measured that validator). Remaining future work is limited to the residual limitations noted under Known limitations; no further features are claimed here.

## License

See [LICENSE](LICENSE).
