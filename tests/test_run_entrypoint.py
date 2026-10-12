"""``python -m backend``: config/port pre-flight with readable failures."""

from __future__ import annotations

import os
import socket
import subprocess
import sys

import pytest

from backend.__main__ import main, port_problem
from src.config.settings import repo_root


def run_module(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    full_env = {**os.environ, **env}
    return subprocess.run(
        [sys.executable, "-m", "backend", *args],
        cwd=repo_root(),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_invalid_config_prints_the_problems_and_exits_2() -> None:
    result = run_module("--check", ATLAS_MAX_CONCURRENT_VIDEOS="0", NOTES_PROVIDER="claude")
    assert result.returncode == 2
    assert "Atlas cannot start: invalid configuration" in result.stderr
    assert "max_concurrent_videos: must be at least 1" in result.stderr
    assert "notes_provider: must be one of gemini, openrouter" in result.stderr
    assert "Traceback" not in result.stderr


def test_check_passes_on_a_free_port() -> None:
    result = run_module("--check", ATLAS_PORT=str(free_port()))
    assert result.returncode == 0, result.stderr
    assert "Configuration OK" in result.stdout


def test_a_busy_port_is_reported_in_one_line() -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        message = port_problem("127.0.0.1", port)
    assert message is not None and f"Port {port} on 127.0.0.1 is already in use" in message


def test_main_returns_1_when_the_port_is_busy(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        monkeypatch.setenv("ATLAS_PORT", str(busy.getsockname()[1]))
        assert main(["--check"]) == 1
    assert "already in use" in capsys.readouterr().err
