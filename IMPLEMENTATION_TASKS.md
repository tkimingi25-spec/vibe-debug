# Implementation Task Record

This is the checkable delivery record for the 2026-10-23 implementation proposal. A task is marked complete only after its relevant automated test command has passed. Live-Phoenix tasks remain pending until they can be run against a healthy local service or CI runner.

## Phase 1 — Runtime Blockers

- [x] Add async support to `@vibetrace` and unit-test success, silent-failure, and exception paths.
- [x] Replace the synchronous exporter with a bounded `BatchSpanProcessor` and test its configuration.
- [x] Add server health polling and managed Phoenix lifecycle support.
- [x] Replace CI's fixed Phoenix sleep with a readiness poll.

## Phase 2 — Reliable Test Infrastructure

- [x] Add and register a `requires_phoenix` pytest marker.
- [x] Mark every test that queries live Phoenix, leaving offline tests runnable without it.
- [x] Remove hard-coded workspace paths and remove outdated legacy tests with hardcoded MongoDB/Neo4j/IKR references.

## Phase 3 — CLI

- [x] Add the `vibe-debug` package entry point.
- [x] Implement and smoke-test `init`, `up`, `status`, `map`, `failures`, `explain`, `doctor`, `mcp`, and `down`.
- [x] Run CLI help and doctor smoke checks in CI.

## Phase 4 — Live End-to-End Validation

- [x] Add the four-module deliberate-failure E2E test, guarded by `requires_phoenix`.
- [x] Run the E2E test against a healthy Phoenix service and enable it in CI.

## Phase 5 — Polish

- [x] Correct implementation-document status for the completed Week 4 tools.
- [x] Document async decorator behavior.
- [x] Cap pipeline-cache component growth.
- [x] Clarify generated-config watch-module guidance.

## Stage Log

### Stage 0 — 2026-10-23: inspection and task conversion

- Confirmed the proposal against the source: synchronous decorator/exporter, no console script, blocking server launcher, and fixed CI sleep are all present.
- Baseline status from the supplied proposal: 20 offline tests passing; Phoenix-dependent tests not yet verified in this environment.
- Created this file before implementation so completion remains test-gated.

### Stage 1 — runtime blockers and service operations

- Updated `vibe_debug/decorators.py` with separate sync and async wrappers; the async wrapper awaits the decorated coroutine before recording output or errors.
- Replaced `SimpleSpanProcessor` with `BatchSpanProcessor` in `vibe_debug/tracer.py`, using a 500 ms schedule delay and a 5 s export cap.
- Replaced the blocking server launcher in `server/manager.py` with health polling and a project-scoped managed PID file.
- Added `vibe_debug/cli.py`, the `vibe-debug` console script, and CI readiness/CLI smoke checks.
- Verification: `pytest tests/test_runtime_and_cli.py tests/test_static_analyzer.py -v` — 11 passed.

### Stage 2 — offline-safe test execution and polish

- Added the registered `requires_phoenix` marker and collection-time skipping for each live-Phoenix test; the manual scripts no longer run as pytest import side effects.
- Made workspace and optional IKR paths portable, fixed generated TOML entry-point commas, and updated the configuration guidance.
- Made `IOInterceptor.start()` return its patch results, and capped persisted pipeline components at 500 by evicting least-observed entries.
- Updated architecture and implementation documentation to reflect async tracing and the completed Week 4 tools.
- Verification: `pytest tests/ -v` — 30 passed, 12 skipped (Phoenix unavailable); `python -m vibe_debug.cli --help` and `doctor` passed. `git diff --check` passed.

### Stage 4 — 2026-10-23: live Phoenix validation and sample application workflow

- Created four-module sample application in `sample_app/` (`main.py`, `retriever.py`, `processor.py`, `generator.py`) with configurable failure switch (`--fail`).
- Added live end-to-end integration test in `tests/test_e2e_sample_app.py` verifying static discovery, trace emission, Phoenix GraphQL error capture, pipeline cache generation, CLI commands (`status`, `map`, `failures`, `explain`), and IDE MCP bridge (`why_did_this_fail`).
- Removed outdated legacy tests and scripts containing hardcoded external database and workspace paths (`test_gap1_connection_failures.py`, `test_system_health.py`).
- Cleaned up async background task test in `tests/test_gap3_async_background_tasks.py` to use generic pipeline workers.
- Updated MCP bridge and CLI to support multi-project aggregation so linked projects are inspected accurately.
- Verification: `pytest tests/test_e2e_sample_app.py -v` passed (1 passed in 29.55s). All CLI inspection commands and MCP tool calls succeeded.

