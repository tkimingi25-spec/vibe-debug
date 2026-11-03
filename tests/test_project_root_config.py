"""Defect A: CLI config resolves relative to --project-root.

From a different cwd, `vibe-debug --project-root sample_app explain`
must load sample_app/vibedebug.toml (not the cwd config). An explicit
--language request is the explainer opt-in: with a credential present
it attempts exactly one provider call even when enabled=false; with
no --language and enabled=false it attempts none.
All provider calls are mocked; no live API calls in this suite.
"""

import json
from pathlib import Path
from types import SimpleNamespace

from vibe_debug import cli, explainer

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_APP = REPO_ROOT / "sample_app"

VALID = {
    "summary": "process_documents raised ValueError.",
    "likely_cause": "process_documents raised ValueError deliberately.",
    "evidence": ["process_documents status ERROR"],
    "suggested_fix": ["Check process_documents."],
    "confidence": "medium",
    "missing_information": [],
}


class RecordingClient:
    def __init__(self, payload=None):
        self.payload = payload if payload is not None else VALID
        self.calls = []

    def __call__(self, request):
        self.calls.append(request)
        return self.payload


def _span():
    return {
        "name": "process_documents",
        "statusCode": "ERROR",
        "latencyMs": 4.0,
        "startTime": "2026-11-01T10:00:03",
        "attributes": json.dumps({"error": {"type": "ValueError"}}),
    }


def _disabled_cfg():
    return SimpleNamespace(
        explainer_enabled=False,
        explainer_model="muse-spark-1.3",
        explainer_endpoint="https://api.meta.ai/v1/chat/completions",
        explainer_timeout_seconds=60.0,
        explainer_language="en",
        explainer_max_evidence_size=8000,
        privacy_profile="permissive",
    )


# -- config resolution -------------------------------------------------------


def test_local_config_loads_project_root_from_other_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # different cwd, no vibedebug.toml here
    config = cli._local_config(SAMPLE_APP)
    assert config is not None
    assert config.project_name == "vibe-debug-sample"
    assert config.explainer_enabled is False
    assert config.explainer_model == "muse-spark-1.3"
    assert config.explainer_endpoint == "https://api.meta.ai/v1/chat/completions"
    assert config.explainer_timeout_seconds == 60


def test_local_config_falls_back_to_cwd(monkeypatch, tmp_path):
    (tmp_path / "vibedebug.toml").write_text(
        '[project]\nname = "cwd-project"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    empty_root = tmp_path / "no-config-here"
    empty_root.mkdir()
    config = cli._local_config(empty_root)
    assert config is not None
    assert config.project_name == "cwd-project"


def test_local_config_no_arg_keeps_cwd_behaviour(monkeypatch, tmp_path):
    (tmp_path / "vibedebug.toml").write_text(
        '[project]\nname = "cwd-only"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    assert cli._local_config().project_name == "cwd-only"


def test_cli_explain_from_other_cwd_uses_project_root_config(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    captured = {}

    def fake_explain(evidence, *, language=None, config=None, fallback_text=None, **kw):
        captured["language"] = language
        captured["config"] = config
        return {"language": language, "source": "fallback", "structured": None,
                "text": fallback_text, "error": None}

    monkeypatch.setattr(cli, "_fetch_spans", lambda limit=30, **kw: [{"node": _span()}])
    monkeypatch.setattr(cli, "explain_failure", fake_explain)
    rc = cli.main(["--project-root", str(SAMPLE_APP), "explain", "--language", "en"])
    assert rc == 0
    assert captured["language"] == "en"
    assert captured["config"] is not None
    assert captured["config"].project_name == "vibe-debug-sample"
    assert captured["config"].explainer_enabled is False


# -- explicit-language opt-in gating ------------------------------------------


def test_explicit_language_attempts_one_call_when_disabled(monkeypatch):
    client = RecordingClient()
    monkeypatch.setattr(
        explainer, "_default_http_client", lambda config: client
    )
    monkeypatch.setenv("MUSE_SPARK_API_KEY", "test-key-presence-only")
    result = explainer.explain_failure(
        {"function": "process_documents", "status": "ERROR"},
        language="en",
        config=_disabled_cfg(),
        fallback_text="DET",
    )
    assert result["source"] == "llm"
    assert len(client.calls) == 1


def test_no_language_and_disabled_attempts_no_call(monkeypatch):
    built = []

    def factory(config):
        built.append(config)
        return RecordingClient()

    monkeypatch.setattr(explainer, "_default_http_client", factory)
    monkeypatch.setenv("MUSE_SPARK_API_KEY", "test-key-presence-only")
    result = explainer.explain_failure(
        {"function": "process_documents", "status": "ERROR"},
        config=_disabled_cfg(),
        fallback_text="DET",
    )
    assert result["source"] == "fallback"
    assert result["text"] == "DET"
    assert built == []
