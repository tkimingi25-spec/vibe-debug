"""Local Phoenix process management for the ``vibe-debug`` CLI."""

import ctypes
import json
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

from vibe_debug.phoenix_endpoint import resolve_base_url, trust_env_for

load_dotenv()

PID_FILENAME = ".vibe_phoenix.pid"


def _manager_base_url(host=None, port=None) -> str:
    """Resolve the managed Phoenix base URL via the single resolver.

    Explicit host/port arguments (the historical manager API) are
    combined into an explicit candidate; otherwise resolution falls
    through env > config > default in ``resolve_base_url``.
    """
    if host is not None or port is not None:
        explicit = f"http://{host or '127.0.0.1'}:{port or 6006}"
        return resolve_base_url(explicit)
    return resolve_base_url()


def health_url(host=None, port=None):
    return f"{_manager_base_url(host, port)}/healthz"


def wait_for_phoenix(host=None, port=None, timeout=30) -> bool:
    """Poll Phoenix's health endpoint until it is ready or times out."""
    url = health_url(host, port)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                url, timeout=2.0, trust_env=trust_env_for(url)
            )
            if response.status_code == 200:
                print(f"[vibe-debug] Phoenix ready at {url.removesuffix('/healthz')}")
                return True
        except httpx.HTTPError:
            pass
        time.sleep(1)
    print(f"[vibe-debug] Phoenix did not become ready within {timeout}s")
    return False


def is_phoenix_healthy(host=None, port=None) -> bool:
    """Return whether a Phoenix server responds to its health endpoint."""
    url = health_url(host, port)
    try:
        return (
            httpx.get(url, timeout=2.0, trust_env=trust_env_for(url)).status_code
            == 200
        )
    except httpx.HTTPError:
        return False


def _pid_path(project_root=".") -> Path:
    return Path(project_root) / PID_FILENAME


PHOENIX_MARKER = "phoenix.server.main"
_START_TIME_TOLERANCE = 2.0


def _parse_proc_stat_starttime(text):
    """Extract the starttime field (22) from Linux /proc/<pid>/stat text."""
    try:
        rest = text[text.rindex(")") + 2:]
        fields = rest.split()
        return float(fields[19])
    except Exception:
        return None


def _process_creation_time(pid):
    """Process creation/start time, or None if unavailable. Never raises.

    Windows: FILETIME via ctypes OpenProcess/GetProcessTimes, converted
    to Unix epoch seconds. Linux: /proc/<pid>/stat starttime (clock
    ticks since boot; only ever compared against values from the same
    source). Other platforms: None.
    """
    try:
        if pid is None or int(pid) <= 0:
            return None
        pid = int(pid)
        if platform.system() == "Windows":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                creation = ctypes.c_ulonglong()
                exit_time = ctypes.c_ulonglong()
                kernel = ctypes.c_ulonglong()
                user = ctypes.c_ulonglong()
                ok = kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel),
                    ctypes.byref(user),
                )
                if not ok:
                    return None
                return (creation.value - 116444736000000000) / 10_000_000.0
            finally:
                kernel32.CloseHandle(handle)
        if platform.system() == "Linux":
            text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            return _parse_proc_stat_starttime(text)
    except Exception:
        return None
    return None


def _process_command_line(pid):
    """Best-effort process command line, or None. Never raises.

    Linux: /proc/<pid>/cmdline. macOS/other POSIX: ``ps``. Windows has
    no stdlib-feasible route, so None (creation time is used there).
    """
    try:
        if pid is None or int(pid) <= 0:
            return None
        pid = int(pid)
        if platform.system() == "Linux":
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        if platform.system() != "Windows":
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=5,
            )
            return out.stdout.strip() or None
    except Exception:
        return None
    return None


def _is_process_alive(pid):
    """Best-effort liveness check that never signals the process."""
    try:
        if pid is None or int(pid) <= 0:
            return False
        pid = int(pid)
        if platform.system() == "Windows":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        if platform.system() == "Linux":
            return Path(f"/proc/{pid}").exists()
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _capture_fingerprint(pid, marker=PHOENIX_MARKER):
    """Record identity for a just-launched process. Never raises."""
    return {
        "version": 1,
        "pid": int(pid),
        "created": _process_creation_time(pid),
        "marker": marker,
    }


def _read_pid_file(pid_path):
    """Parse the PID file (JSON or legacy plain int). Never raises.

    Returns a dict with at least ``pid`` (or None if malformed) plus
    any recorded fingerprint fields. Old plain-integer files parse
    with ``created``/``marker`` absent (unverifiable, not an error).
    """
    try:
        text = pid_path.read_text(encoding="utf-8").strip()
        if not text:
            return {"pid": None, "malformed": True}
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("pid") is not None:
            return data
        return {"pid": int(text), "legacy": True}
    except Exception:
        return {"pid": None, "malformed": True}


def _verify_process_identity(record):
    """Return 'verified' | 'mismatch' | 'unavailable' | 'dead'. Never raises.

    The verify->signal race (PID reused in the milliseconds between
    this check and the signal) cannot be eliminated; verifying here
    shrinks the window from the PID file's age to milliseconds.
    """
    try:
        pid = record.get("pid")
        if pid is None or int(pid) <= 0:
            return "dead"
        pid = int(pid)
        if not _is_process_alive(pid):
            return "dead"
        recorded_created = record.get("created")
        if recorded_created is None:
            return "unavailable"
        current_created = _process_creation_time(pid)
        if current_created is None:
            return "unavailable"
        try:
            if abs(float(current_created) - float(recorded_created)) > _START_TIME_TOLERANCE:
                return "mismatch"
        except (TypeError, ValueError):
            return "unavailable"
        cmdline = _process_command_line(pid)
        if cmdline is not None:
            marker = str(record.get("marker") or "phoenix")
            if marker.lower() not in cmdline.lower() and "phoenix" not in cmdline.lower():
                return "mismatch"
        return "verified"
    except Exception:
        return "unavailable"


def launch_server(host=None, port=None, timeout=30, project_root=".") -> bool:
    """Start Phoenix in a managed child process and wait for readiness."""
    from urllib.parse import urlparse

    parsed = urlparse(_manager_base_url(host, port))
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 6006
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
    _pid_path(project_root).write_text(
        json.dumps(_capture_fingerprint(process.pid)), encoding="utf-8"
    )
    if wait_for_phoenix(host, port, timeout):
        return True

    process.terminate()
    _pid_path(project_root).unlink(missing_ok=True)
    return False


def stop_server(project_root=".") -> bool:
    """Stop only the Phoenix process that this project started.

    The recorded PID is signalled ONLY after its identity is verified
    against the fingerprint captured at launch (PIDs are recycled).
    Unverifiable or mismatched PIDs are never signalled: there is no
    interactive confirmation in this CLI path, so refusal is the
    default. Mismatched/dead stale PID files are removed (they no
    longer identify Phoenix); unverifiable files are kept so the user
    can inspect them.
    """
    pid_path = _pid_path(project_root)
    if not pid_path.exists():
        print("[vibe-debug] No managed Phoenix process is recorded.")
        return False
    record = _read_pid_file(pid_path)
    if record.get("pid") is None:
        print("[vibe-debug] Phoenix PID file is unreadable; removing it, nothing signalled.")
        pid_path.unlink(missing_ok=True)
        return False
    pid = int(record["pid"])
    identity = _verify_process_identity(record)
    if identity == "dead":
        print(f"[vibe-debug] Phoenix (PID {pid}) is not running; removing stale PID file.")
        pid_path.unlink(missing_ok=True)
        return False
    if identity == "mismatch":
        print(
            f"[vibe-debug] WARNING: PID {pid} appears to have been reused by "
            "another process (fingerprint mismatch). Refusing to signal it; "
            "removing the stale PID file."
        )
        pid_path.unlink(missing_ok=True)
        return False
    if identity != "verified":
        print(
            f"[vibe-debug] WARNING: cannot verify that PID {pid} is the "
            "Phoenix process Vibe-Debug started (no usable fingerprint). "
            "Refusing to signal it. PID file kept for inspection."
        )
        return False
    # Verified: signal promptly — the verify->signal race window is
    # now milliseconds (see _verify_process_identity).
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as error:
        print(f"[vibe-debug] Could not stop managed Phoenix: {error}")
        return False
    finally:
        pid_path.unlink(missing_ok=True)
    print("[vibe-debug] Managed Phoenix stopped.")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if launch_server() else 1)
