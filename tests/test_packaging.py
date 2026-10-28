import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as file:
        return tomllib.load(file)["project"]


def test_pyproject_is_the_single_dependency_source():
    assert not (ROOT / "requirements.txt").exists(), (
        "requirements.txt duplicates pyproject.toml; install with pip install ."
    )


def test_runtime_dependencies_are_pinned():
    dependencies = _project()["dependencies"]
    assert dependencies, "pyproject.toml declares no runtime dependencies"
    for dependency in dependencies:
        name = dependency.split("[")[0].split("=")[0].split("~")[0].split(">")[0]
        assert any(marker in dependency for marker in ("==", "~=", ">=")), (
            f"unpinned dependency: {dependency}"
        )
        assert name.strip().lower() != "fastmcp", (
            "standalone fastmcp is unused; FastMCP is imported from the mcp SDK"
        )


def test_phoenix_is_exactly_pinned_and_mcp_sdk_is_used():
    dependencies = _project()["dependencies"]
    phoenix = [
        d for d in dependencies if d.lower().startswith("arize-phoenix==")
    ]
    assert phoenix, "arize-phoenix must be exactly pinned (==)"
    assert any(
        d.lower().startswith("arize-phoenix-evals") for d in dependencies
    ), "arize-phoenix-evals must be capped below 3.x for Phoenix 13.x"
    assert any(d.lower().startswith("mcp") for d in dependencies)
