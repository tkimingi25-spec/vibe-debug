"""Safe stale-PID recovery via down --force (Stage 5, limitations task).

Only test-owned sleeper processes are ever signalled; every one is
cleaned up in fixture teardown.
"""

import argparse
import subprocess
import sys
import time

import pytest

from server import manager
from vibe_debug import cli


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    yield proc
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _write_legacy_pid(tmp_path, pid):
    path = tmp_path / manager.PID_FILENAME
    path.write_text(str(pid), encoding="utf-8")
    return path


def _wait_dead(proc, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.1)
    return False


def test_unverifiable_without_force_refused_and_alive(tmp_path, sleeper, capsys):
    _write_legacy_pid(tmp_path, sleeper.pid)
    assert manager.stop_server(str(tmp_path)) is False
    assert "Refusing to signal" in capsys.readouterr().out
    assert sleeper.poll() is None


def test_unverifiable_with_force_signals_and_warns(tmp_path, sleeper, capsys):
    path = _write_legacy_pid(tmp_path, sleeper.pid)
    assert manager.stop_server(str(tmp_path), force=True) is True
    out = capsys.readouterr().out
    assert "could NOT be verified" in out
    assert f"Signalled unverified PID {sleeper.pid}" in out
    assert _wait_dead(sleeper)
    assert not path.exists()


def test_mismatch_never_signalled_even_with_force(tmp_path, sleeper, capsys):
    created = manager._process_creation_time(sleeper.pid)
    path = tmp_path / manager.PID_FILENAME
    path.write_text(
        '{"version": 1, "pid": %d, "created": %s, "marker": "phoenix.server.main"}'
        % (sleeper.pid, created + 100000.0),
        encoding="utf-8",
    )
    assert manager.stop_server(str(tmp_path), force=True) is False
    assert "Refusing to signal" in capsys.readouterr().out
    assert sleeper.poll() is None


def test_verified_stop_works_without_force(tmp_path, sleeper, monkeypatch, capsys):
    _write_legacy_pid(tmp_path, sleeper.pid)
    monkeypatch.setattr(manager, "_verify_process_identity", lambda record: "verified")
    assert manager.stop_server(str(tmp_path)) is True
    assert "Managed Phoenix stopped" in capsys.readouterr().out
    assert _wait_dead(sleeper)


def test_doctor_reports_stale_pid_guidance(tmp_path, sleeper, capsys):
    _write_legacy_pid(tmp_path, sleeper.pid)
    args = argparse.Namespace(project_root=tmp_path)
    assert cli.command_doctor(args) == 0
    out = capsys.readouterr().out
    assert "Stale/unverifiable Phoenix PID file" in out
    assert "vibe-debug down --force" in out
    assert sleeper.poll() is None


def test_cli_parser_down_force_flag():
    args = cli.build_parser().parse_args(["down"])
    assert args.force is False
    args = cli.build_parser().parse_args(["down", "--force"])
    assert args.force is True
