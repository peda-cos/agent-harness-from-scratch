from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import (
    TIMEOUT_EXIT_CODE,
    TRUNCATION_MARKER,
    Sandbox,
    SandboxConfig,
)

SLOW_SCRIPT = (
    "import time\n"
    "print('started', flush=True)\n"
    "time.sleep(5)\n"
)
SLOW_BIG_SCRIPT = (
    "import time\n"
    "print('x' * 1024, flush=True)\n"
    "time.sleep(5)\n"
)
SPAWNER_SCRIPT = (
    "import subprocess\n"
    "import sys\n"
    "import time\n"
    "child = subprocess.Popen(\n"
    "    [sys.executable, '-c', 'import time; time.sleep(10)']\n"
    ")\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(10)\n"
)


def _proc_state(pid: int) -> str | None:
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, ProcessLookupError):
        return None
    fields = text.split()
    if len(fields) < 3:
        return None
    return fields[2]


def test_timeout_returns_sentinel_and_partial_output(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("slow.py", SLOW_SCRIPT)
    result = Sandbox(
        SandboxConfig(project_root=project_root, timeout_seconds=0.3)
    ).run([sys.executable, "slow.py"])

    assert result.timed_out is True
    assert result.denied is False
    assert result.exit_code == TIMEOUT_EXIT_CODE
    assert result.reason == "wall-clock timeout after 0.3s"
    assert "started" in result.stdout
    assert result.duration_ms >= 200


def test_fast_command_does_not_timeout(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("hello.py", "print('ok')\n")
    result = Sandbox(
        SandboxConfig(project_root=project_root, timeout_seconds=5.0)
    ).run([sys.executable, "hello.py"])

    assert result.timed_out is False
    assert result.denied is False
    assert result.exit_code == 0
    assert result.stdout == "ok\n"


@pytest.mark.skipif(
    os.name != "posix" or not Path("/proc").is_dir(),
    reason="process-group proof needs POSIX /proc",
)
def test_timeout_kills_the_process_group(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("spawner.py", SPAWNER_SCRIPT)
    result = Sandbox(
        SandboxConfig(project_root=project_root, timeout_seconds=0.5)
    ).run([sys.executable, "spawner.py"])

    assert result.timed_out is True
    lines = result.stdout.splitlines()
    assert lines
    assert lines[0].isdigit(), f"spawner did not print a pid: {lines!r}"
    pid = int(lines[0])

    deadline = time.monotonic() + 2.0
    state: str | None = "?"
    while time.monotonic() < deadline:
        state = _proc_state(pid)
        if state is None or state == "Z":
            break
        time.sleep(0.05)

    assert state is None or state == "Z", (
        f"grandchild {pid} survived the process-group kill in state {state}"
    )


def test_partial_output_is_truncated_on_timeout(
    project_root: Path,
    write_script: Callable[[str, str], Path],
) -> None:
    write_script("slowbig.py", SLOW_BIG_SCRIPT)
    result = Sandbox(
        SandboxConfig(
            project_root=project_root,
            max_output_bytes=64,
            timeout_seconds=0.5,
        )
    ).run([sys.executable, "slowbig.py"])

    assert result.timed_out is True
    assert result.truncated is True
    assert len(result.stdout.encode("utf-8")) <= 64
    assert TRUNCATION_MARKER.decode("utf-8") in result.stdout
