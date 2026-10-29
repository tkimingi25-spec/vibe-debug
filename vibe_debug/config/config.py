import os
import tomllib
from dataclasses import dataclass, field
from typing import List


@dataclass
class VibeConfig:
    project_name: str = "vibe-debug"
    otlp_endpoint: str = "http://localhost:6006/v1/traces"
    watch_modules: List[str] = field(default_factory=list)
    ignore_functions: List[str] = field(
        default_factory=lambda: ["__init__", "__repr__", "__str__"]
    )
    # Redaction: extra sensitive keys extend (never replace) the defaults
    # in vibe_debug.redaction; max_attr_length truncates span attributes.
    max_attr_length: int = 1024
    redact_keys: List[str] = field(default_factory=list)


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
            "otlp_endpoint", "http://localhost:6006/v1/traces"
        ),
        watch_modules=data.get("watch", {}).get("modules", []),
        ignore_functions=data.get("watch", {}).get(
            "ignore_functions", ["__init__", "__repr__", "__str__"]
        ),
        max_attr_length=data.get("redaction", {}).get(
            "max_attr_length", 1024
        ),
        redact_keys=data.get("redaction", {}).get("redact_keys", []),
    )
