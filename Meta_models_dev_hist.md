# Meta models dev hist

## Purpose

This file is the running development-history log for `vibe-debug` from the date of its creation onward.

Record here, in chronological order:

- Every idea, feature, implementation, investigation, or documentation request made after this file was created, plus the seed entries below for the session in which it was created.
- Every material finding made while carrying out a request.
- The remedy or correction applied for each finding, or explicitly what was kept unchanged and why.
- Verification performed, including commands run and their observed results.

## Recording rules

1. Append new entries at the end of the Log section. Do not rewrite or delete earlier entries; if an earlier entry is superseded, add a new entry that references it.
2. One entry per request or coherent unit of work.
3. Each entry must state one of the following dispositions for every finding: `Fixed`, `Kept`, `Deferred`, or `Blocked`, with a reason.
4. Record observed results, not intentions. A test is only recorded as passing if it was actually run and seen to pass.
5. Name the files created, changed, or deliberately left unchanged.
6. Never record secrets, tokens, passwords, or personal data in this file.

## Entry template

```markdown
### YYYY-MM-DD — <short title>

- **Request / idea:** <what was asked for or proposed>
- **Work done:** <what was implemented, investigated, or produced>
- **Finding:** <what was discovered, or "None">
- **Remedy / correction applied:** <change made, or "None">
- **Kept:** <what was deliberately left unchanged, and why>
- **Verification:** <command or check run, and the observed result>
- **Files:** <files created, changed, or reviewed>
- **Disposition:** Fixed / Kept / Deferred / Blocked
```

---

## Log

### 2026-10-28 — Session model identification

- **Request / idea:** Identify the available models ("models").
- **Work done:** Identified the model for the current session from observed session state.
- **Finding:** The only identifiable model in this session is `muse-wonder-1.0`, powering Muse Code. The `general-purpose` and `workflow-subagent` entries are agent definitions, not distinct models; they inherit the parent model route. The `vibe-debug` repository itself names no LLM and makes no OpenAI, Anthropic, or local-model calls; it uses Arize Phoenix, OpenTelemetry, and MCP.
- **Remedy / correction applied:** None required.
- **Kept:** Agent definitions recorded as agent definitions, not listed as models.
- **Verification:** Session identity as presented by the host; repository search of `vibe_debug/`, `server/`, `README.md`, and `pyproject.toml` returned no LLM model references.
- **Files:** None changed by this entry.
- **Disposition:** Kept

### 2026-10-28 — Repository analysis report

- **Request / idea:** Analyze the repository and give a report.
- **Work done:** Inspected project structure, packaging, CLI, runtime modules, MCP bridge, Phoenix manager, configuration, CI workflow, documentation, sample app, and tests. Ran the full test suite.
- **Finding:**
  1. Test run on Python 3.13.7: `4 failed, 28 passed, 8 skipped in 41.96s`.
  2. Two failures are live-Phoenix tests (`tests/test_auto_instrument.py`, sys-tracer test) that POST to `http://localhost:6006/graphql` without the `requires_phoenix` marker, so they fail instead of skipping when Phoenix is down. This contradicts `IMPLEMENTATION_TASKS.md`, which states every live test is marked.
  3. Two failures are async decorator tests failing with `PermissionError: [WinError 10013]` inside the `asyncio` `socket.socketpair()` self-pipe — a sandbox restriction on loopback listeners, not a decorator defect.
  4. This machine sets `HTTP_PROXY`/`HTTPS_PROXY` with an empty `NO_PROXY`; the first test run routed `localhost:6006` through a proxy and received `502 Bad Gateway`. With `NO_PROXY=localhost,127.0.0.1`, the failure became a plain connection error, confirming the diagnosis. Phoenix was not running, accounting for the 8 skips.
  5. `vibe_debug/decorators.py` stringifies all arguments, kwargs, and return values into `vibe.input` / `vibe.output` span attributes with no redaction or truncation — secrets or PII passing through traced functions would be stored in Phoenix.
  6. The silent-failure check in `vibe_debug/decorators.py` (`result == [] or result == "" or result == {}`) can raise on NumPy/pandas-style results where `==` returns an array with ambiguous truth value.
  7. CLI (`vibe_debug/cli.py`) and MCP bridge (`server/mcp_bridge.py`) hardcode `http://localhost:6006` despite configurable `otlp_endpoint` and `PHOENIX_HOST`/`PHOENIX_PORT`; localhost `httpx` clients also honor proxy environment variables.
  8. `LoggingBatchSpanProcessor.on_end` in `vibe_debug/tracer.py` prints "Export succeeded", but `BatchSpanProcessor.on_end` only enqueues the span, so failed exports are reported as successes at span level.
  9. Span-fetch GraphQL logic is duplicated between `vibe_debug/cli.py` and `server/mcp_bridge.py`.
  10. Dependencies are unpinned and duplicated between `pyproject.toml` and `requirements.txt`, including both `mcp[cli]` and `fastmcp`.
  11. Minor: `stop_server` in `server/manager.py` signals a recorded PID without verifying it still belongs to Phoenix; the tracer `_initialized` global has no reset path; a local `.env` exists but is covered by `.gitignore` and is not committed. Working tree was clean on `main` at commit `7842288`.
- **Remedy / correction applied:** None. The request was analysis only; no code was changed.
- **Kept:** All source, tests, and configuration left exactly as found. Findings 2 and 5 are the recommended first fixes when implementation is requested.
- **Verification:** `python -m pytest tests/ -q` — 28 passed, 8 skipped, 4 failed as itemized above. Re-ran the failing subset with `NO_PROXY` set to confirm the proxy diagnosis, and re-ran one async test alone to confirm the sandbox socket cause.
- **Files:** Reviewed — `README.md`, `ARCHITECTURE.md`, `IMPLEMENTATION_TASKS.md`, `pyproject.toml`, `requirements.txt`, `.github/workflows/ci.yml`, `vibe_debug/__init__.py`, `vibe_debug/cli.py`, `vibe_debug/tracer.py`, `vibe_debug/decorators.py`, `vibe_debug/config/config.py`, `vibe_debug/runtime/pipeline_discovery.py`, `server/manager.py`, `server/mcp_bridge.py`, `tests/conftest.py`, `tests/test_auto_instrument.py`, `tests/test_runtime_and_cli.py`. None changed.
- **Disposition:** Deferred (remedies recommended, not yet requested)

### 2026-10-28 — Creation of this development-history file

- **Request / idea:** Create a file in the repository titled "Meta models dev hist". From now on, document in it any idea or implementation requested, and record any finding together with the remedy or correction applied, or what was kept.
- **Work done:** Created this file with its purpose, recording rules, entry template, and seed entries for the current session (model identification and repository analysis, above).
- **Finding:** No pre-existing development-history or changelog file of this kind existed in the repository root. `IMPLEMENTATION_TASKS.md` is a task-delivery record for a specific 2026-10-23 implementation proposal, not an ongoing history log, so a separate file was required rather than appending to it.
- **Remedy / correction applied:** Created this file as the single ongoing log. Future requests, findings, remedies, corrections, and kept-unchanged decisions are to be appended here per the recording rules above.
- **Kept:** `IMPLEMENTATION_TASKS.md` and all other existing files left unchanged; this file links to that record by reference only.
- **Verification:** File created at the repository root and read back to confirm content.
- **Files:** Created — `Meta_models_dev_hist.md` (this file).
- **Disposition:** Fixed

### 2026-10-28 - Improvement 7: Pin dependencies, pyproject.toml as single source

- **Request / idea:** Implement Improvement 7 (pin dependencies and make `pyproject.toml` the single source of truth), verify it end to end, then close out with the stale-reference grep (Test A) and drift-guard positive/negative checks (Test B) before logging.
- **Work done:** Implemented: `pyproject.toml` pinned set (`arize-phoenix==13.18.0` exact; `arize-phoenix-evals~=2.12` and `graphql-core~=3.2.0` capped; `opentelemetry-sdk`/`opentelemetry-exporter-otlp-proto-http~=1.45`, `mcp[cli]~=1.29`, `httpx~=0.28`, `wrapt~=1.17`, `python-dotenv~=1.1`; dev: `pytest~=8.4`, `pytest-asyncio~=1.3`); unused standalone `fastmcp` removed; `requirements.txt` deleted; `README.md` notes `pyproject.toml` is the single source; new drift guard `tests/test_packaging.py` (3 tests); flake hardening only in `tests/test_gap2_successful_operations.py` (poll all projects until spans land; assertions unchanged).
- **Finding:** Unpinned dependencies duplicated across `pyproject.toml` and `requirements.txt` (drift risk); unpinned `arize-phoenix` is a concrete future-breakage risk due to Phoenix GraphQL schema churn; standalone `fastmcp` declared but unused (the code uses `mcp.server.fastmcp` from the `mcp` SDK, confirmed by import grep: only `from mcp.server.fastmcp import FastMCP` in `server/mcp_bridge.py`). Phoenix 13.x also needed two transitive caps: `arize-phoenix-evals` below 3.x (Phoenix imports `phoenix.evals.models`, removed in evals 3.x) and `graphql-core~=3.2.0` (3.3 removed `ExecutionContext` used by the pinned strawberry-graphql).
- **Remedy / correction applied:** Proposed remedy: pin dependencies with `pyproject.toml` as the single source; exact-pin Phoenix, cap evals + `graphql-core`, compatible ranges elsewhere; delete `requirements.txt`; remove only the verified-unused package after an import grep; add an automated drift guard; no new tooling/dependencies. Applied exactly as proposed.
- **Kept:** No uv/lockfile or other new tooling introduced. All other packages kept with loose compatible (`~=`) ranges to avoid over-pinning toil. Gap2 test assertions unchanged (timing hardening only). A pre-existing editable `vibe-debug` install in system Python was noticed and deliberately left untouched.
- **Verification:** Full suite 43 passed in project `.venv` (Phoenix up, CI-equivalent). Fresh-venv install (run unsandboxed as user) exit 0 with all 9 pins resolved; `pip check` clean; `fastmcp` absent from BOTH fresh venv and project `.venv` (`pip show fastmcp`: not found), so the MCP smoke test used the `mcp` SDK, not a leftover package; `tests/test_packaging.py` 3 passed; MCP smoke passed. Test A (stale-reference grep): `requirements.txt` hits only in `README.md` (documents its absence), `tests/test_packaging.py` (the guard), and this file (historical) - no stale references, no fixes needed; `fastmcp` hits only `server/mcp_bridge.py` (`mcp.server.fastmcp`, legitimate), the guard, and this file - no stale references, no blocking code import. Test B: positive run 3 passed; negative check 1 (scratch copy with `arize-phoenix>=13`) FAILED as required with "arize-phoenix must be exactly pinned (==)"; negative check 2 (scratch copy with empty `requirements.txt`) FAILED as required with "requirements.txt duplicates pyproject.toml; install with pip install ."; scratch discarded and real repo proven untouched (no `requirements.txt` in root, exact pin intact at `pyproject.toml:21`).
- **Correction to the earlier report:** The earlier "BLOCKED - network outage / PyPI unreachable" status was wrong. Root cause was the agent sandbox's proxy/TLS path (sandboxed shell forced through `127.0.0.1:55853`; sandboxed user cannot complete TLS, `SEC_E_NO_CREDENTIALS`), not the user's network. Run unsandboxed as the user, PyPI and `files.pythonhosted.org` are reachable, `pip download` succeeds, and the fresh-venv install passes.
Env note: the agent's sandboxed shell cannot complete TLS through its local proxy (SEC_E_NO_CREDENTIALS); sandboxed pip installs hang. Run installs unsandboxed/with escalation. The user's network and pip are healthy. Sandbox sets NO_PROXY empty, so localhost/Phoenix calls from the sandbox may be proxied - set NO_PROXY=localhost,127.0.0.1 session-scoped for agent-run tests.
Status: Implemented and verified. Awaiting approval to commit + push.
- **Files:** Changed - `pyproject.toml`, `README.md`, `tests/test_gap2_successful_operations.py`. Deleted - `requirements.txt`. Created - `tests/test_packaging.py`. Reviewed - `server/mcp_bridge.py`, `.github/workflows/ci.yml`, docs (grep). Appended - `Meta_models_dev_hist.md` (this entry).
- **Disposition:** Fixed
