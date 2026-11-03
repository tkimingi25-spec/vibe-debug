import os
import tomllib
from dataclasses import dataclass, field
from typing import List

from vibe_debug.phoenix_endpoint import DEFAULT_PHOENIX_BASE_URL

DEFAULT_OTLP_ENDPOINT = f"{DEFAULT_PHOENIX_BASE_URL}/v1/traces"


@dataclass
class VibeConfig:
    project_name: str = "vibe-debug"
    otlp_endpoint: str = DEFAULT_OTLP_ENDPOINT
    watch_modules: List[str] = field(default_factory=list)
    ignore_functions: List[str] = field(
        default_factory=lambda: ["__init__", "__repr__", "__str__"]
    )
    # Redaction: extra sensitive keys extend (never replace) the defaults
    # in vibe_debug.redaction; max_attr_length truncates span attributes.
    max_attr_length: int = 1024
    redact_keys: List[str] = field(default_factory=list)
    # Privacy profile: permissive (default, current behaviour) /
    # balanced (pattern scanning) / strict (balanced + payload keys).
    privacy_profile: str = "permissive"
    # Optional Muse Spark explainer (Stage 2). Credentials are never
    # stored here; they come only from the MUSE_SPARK_API_KEY env var.
    explainer_enabled: bool = False
    # Verified flagship defaults (Meta docs, dev.meta.ai); not secrets.
    explainer_model: str = "muse-spark-1.3"
    explainer_endpoint: str = "https://api.meta.ai/v1/chat/completions"
    explainer_timeout_seconds: float = 10.0
    explainer_max_evidence_size: int = 8000
    explainer_language: str = "en"


def load_config(config_path: str = "vibedebug.toml") -> VibeConfig:
    """Load config from vibedebug.toml, return defaults if not found."""
    if not os.path.exists(config_path):
        print("[vibe-debug] No vibedebug.toml found, using defaults")
        return VibeConfig()

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    return VibeConfig(
        project_name=data.get("project", {}).get("name", "vibe-debug"),
        otlp_endpoint=data.get("tracer", {}).get(
            "otlp_endpoint", DEFAULT_OTLP_ENDPOINT
        ),
        watch_modules=data.get("watch", {}).get("modules", []),
        ignore_functions=data.get("watch", {}).get(
            "ignore_functions", ["__init__", "__repr__", "__str__"]
        ),
        max_attr_length=data.get("redaction", {}).get(
            "max_attr_length", 1024
        ),
        redact_keys=data.get("redaction", {}).get("redact_keys", []),
        privacy_profile=str(
            data.get("redaction", {}).get("privacy_profile", "permissive")
            or "permissive"
        ).strip().lower(),
        explainer_enabled=bool(data.get("explainer", {}).get("enabled", False)),
        explainer_model=data.get("explainer", {}).get("model", "muse-spark-1.3"),
        explainer_endpoint=data.get("explainer", {}).get(
            "endpoint", "https://api.meta.ai/v1/chat/completions"
        ),
        explainer_timeout_seconds=data.get("explainer", {}).get(
            "timeout_seconds", 10.0
        ),
        explainer_max_evidence_size=data.get("explainer", {}).get(
            "max_evidence_size", 8000
        ),
        explainer_language=data.get("explainer", {}).get("language", "en"),
    )
