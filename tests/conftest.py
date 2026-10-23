import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
root_str = str(ROOT)
if root_str not in sys.path:
    sys.path.insert(0, root_str)


def _phoenix_running() -> bool:
    try:
        return httpx.get("http://localhost:6006/healthz", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "requires_phoenix: requires a healthy Phoenix service at localhost:6006",
    )


requires_phoenix = pytest.mark.requires_phoenix


def pytest_collection_modifyitems(config, items):
    if _phoenix_running():
        return
    skip = pytest.mark.skip(
        reason="Phoenix not running at localhost:6006; skipping live test"
    )
    for item in items:
        if "requires_phoenix" in item.keywords:
            item.add_marker(skip)
