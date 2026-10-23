import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from server import manager
from vibe_debug import cli
from vibe_debug.cli import build_parser, main
from vibe_debug.decorators import vibetrace
from vibe_debug.runtime.pipeline_discovery import PipelineComponent, PipelineDiscovery
import vibe_debug.tracer as tracer_module


def _mock_span_tracer():
    span = MagicMock()
    span_context = MagicMock()
    span_context.__enter__.return_value = span
    tracer = MagicMock()
    tracer.start_as_current_span.return_value = span_context
    return tracer, span


def test_async_decorator_awaits_and_returns_value():
    tracer, span = _mock_span_tracer()

    @vibetrace("agent")
    async def fetch(value):
        await asyncio.sleep(0)
        return {"value": value}

    with patch("vibe_debug.decorators.trace.get_tracer", return_value=tracer):
        assert asyncio.run(fetch("answer")) == {"value": "answer"}

    span.set_attribute.assert_any_call("vibe.output", "{'value': 'answer'}")


def test_async_decorator_reraises_exception():
    tracer, span = _mock_span_tracer()

    @vibetrace("agent")
    async def explode():
        raise ValueError("boom")

    with patch("vibe_debug.decorators.trace.get_tracer", return_value=tracer):
        with pytest.raises(ValueError, match="boom"):
            asyncio.run(explode())

    span.record_exception.assert_called_once()


def test_wait_for_phoenix_accepts_a_healthy_response():
    response = MagicMock(status_code=200)
    with patch("server.manager.httpx.get", return_value=response):
        assert manager.wait_for_phoenix(timeout=1) is True


def test_tracer_uses_bounded_batch_exporting(monkeypatch):
    exporter = MagicMock()
    processor = MagicMock()
    monkeypatch.setattr(tracer_module, "_initialized", False)
    with (
        patch.object(tracer_module, "OTLPSpanExporter", return_value=exporter),
        patch.object(tracer_module, "LoggingBatchSpanProcessor", return_value=processor) as factory,
        patch.object(tracer_module.trace, "set_tracer_provider"),
    ):
        tracer_module.init_vibedebug("batch-test")

    factory.assert_called_once_with(
        exporter,
        export_timeout_millis=5000,
        schedule_delay_millis=500,
        max_export_batch_size=32,
    )


def test_cli_exposes_all_planned_commands():
    parser = build_parser()
    command_action = next(
        action for action in parser._actions if action.dest == "command"
    )
    assert set(command_action.choices) == {
        "init", "up", "status", "map", "failures", "explain", "doctor", "mcp", "down"
    }


def test_doctor_is_a_non_blocking_smoke_check(tmp_path):
    assert main(["--project-root", str(tmp_path), "doctor"]) == 0


def test_pipeline_cache_component_count_is_bounded(tmp_path):
    discovery = PipelineDiscovery(str(tmp_path))
    discovery.MAX_COMPONENTS = 2
    discovery.pipeline_map.components = {
        f"component_{index}": PipelineComponent(
            name=f"component_{index}", module="app", file="app.py", line=index,
            times_called=index,
        )
        for index in range(3)
    }

    discovery._save_cache()

    assert len(discovery.pipeline_map.components) == 2
    assert "component_0" not in discovery.pipeline_map.components


def test_cli_commands_dispatch_to_their_services(tmp_path, monkeypatch):
    (tmp_path / "app.py").write_text("def main():\n    return 'ok'\n", encoding="utf-8")
    assert main(["--project-root", str(tmp_path), "init"]) == 0

    cache = {
        "project_name": "sample",
        "components": {"main": {"name": "main", "last_status": "OK"}},
    }
    (tmp_path / ".vibe_pipeline_cache.json").write_text(
        json.dumps(cache), encoding="utf-8"
    )
    assert main(["--project-root", str(tmp_path), "status"]) == 0
    assert main(["--project-root", str(tmp_path), "map"]) == 0

    monkeypatch.setattr(manager, "launch_server", lambda **_: True)
    monkeypatch.setattr(manager, "stop_server", lambda *_: True)
    assert main(["--project-root", str(tmp_path), "up"]) == 0
    assert main(["--project-root", str(tmp_path), "down"]) == 0

    monkeypatch.setattr(
        cli,
        "_fetch_spans",
        lambda *_: [{"node": {"name": "broken_step", "statusCode": "ERROR", "latencyMs": 1}}],
    )
    assert main(["--project-root", str(tmp_path), "failures"]) == 0
    assert main(["--project-root", str(tmp_path), "explain"]) == 0

    with patch("server.mcp_bridge.mcp.run") as run:
        assert main(["--project-root", str(tmp_path), "mcp"]) == 0
    run.assert_called_once()
