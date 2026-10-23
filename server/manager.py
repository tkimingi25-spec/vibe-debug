"""Local Phoenix process management for the ``vibe-debug`` CLI."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

PID_FILENAME = ".vibe_phoenix.pid"


def health_url(host="127.0.0.1", port=6006):
    return f"http://{host}:{port}/healthz"


def wait_for_phoenix(host="127.0.0.1", port=6006, timeout=30) -> bool:
    """Poll Phoenix's health endpoint until it is ready or times out."""
    url = health_url(host, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2.0)
            if response.status_code == 200:
                print(f"[vibe-debug] Phoenix ready at {url.removesuffix('/healthz')}")
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    print(f"[vibe-debug] Phoenix did not become ready within {timeout}s")
    return False


def is_phoenix_healthy(host="127.0.0.1", port=6006) -> bool:
    """Return whether a Phoenix server responds to its health endpoint."""
    try:
        return httpx.get(health_url(host, port), timeout=2.0).status_code == 200
    except httpx.HTTPError:
        return False


def _pid_path(project_root=".") -> Path:
    return Path(project_root) / PID_FILENAME


def launch_server(host=None, port=None, timeout=30, project_root=".") -> bool:
    """Start Phoenix in a managed child process and wait for readiness."""
    host = host or os.getenv("PHOENIX_HOST", "127.0.0.1")
    port = int(port or os.getenv("PHOENIX_PORT", "6006"))
    if is_phoenix_healthy(host, port):
        print(f"[vibe-debug] Phoenix is already running at {host}:{port}")
        return True

    environment = os.environ.copy()
    environment["PHOENIX_HOST"] = host
    environment["PHOENIX_PORT"] = str(port)
    working_dir = Path(project_root) / ".vibe_phoenix"
    working_dir.mkdir(parents=True, exist_ok=True)
    environment.setdefault("PHOENIX_WORKING_DIR", str(working_dir.resolve()))
    process = subprocess.Popen(
        [sys.executable, "-m", "phoenix.server.main", "serve"],
        cwd=project_root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    _pid_path(project_root).write_text(str(process.pid), encoding="utf-8")
    if wait_for_phoenix(host, port, timeout):
        return True

    process.terminate()
    _pid_path(project_root).unlink(missing_ok=True)
    return False


def stop_server(project_root=".") -> bool:
    """Stop only the Phoenix process that this project started."""
    pid_path = _pid_path(project_root)
    if not pid_path.exists():
        print("[vibe-debug] No managed Phoenix process is recorded.")
        return False
    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError) as error:
        print(f"[vibe-debug] Could not stop managed Phoenix: {error}")
        return False
    finally:
        pid_path.unlink(missing_ok=True)
    print("[vibe-debug] Managed Phoenix stopped.")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if launch_server() else 1)
