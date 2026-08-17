from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sandbox_runner import (
    DEFAULT_DENYLIST,
    DEFAULT_INTERPRETER_BLOCK,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    DENIED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    TRUNCATION_MARKER,
    SandboxConfig,
    SandboxResult,
    truncate_bytes,
)


def test_config_defaults_and_name_normalization() -> None:
    root = Path.cwd()
    config = SandboxConfig(project_root=root)

    assert config.project_root == Path(os.path.realpath(root))
    assert config.max_output_bytes == DEFAULT_MAX_OUTPUT_BYTES
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert config.denylist == DEFAULT_DENYLIST
    assert config.interpreter_block == DEFAULT_INTERPRETER_BLOCK

    custom = SandboxConfig(
        project_root=root,
        denylist={"RM", " Sudo "},
        interpreter_block={"PYTHON3"},
    )
    assert custom.denylist == frozenset({"rm", "sudo"})
    assert custom.interpreter_block == frozenset({"python3"})


def test_config_rejects_bad_roots_and_limits(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ValueError):
        SandboxConfig(project_root=missing)

    file_root = tmp_path / "file"
    file_root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError):
        SandboxConfig(project_root=file_root)

    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, max_output_bytes=0)
    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, max_output_bytes=True)
    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, timeout_seconds=0)
    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, timeout_seconds=True)
    for invalid_timeout in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            SandboxConfig(project_root=tmp_path, timeout_seconds=invalid_timeout)
    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, denylist={"rm", ""})
    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, denylist="rm")
    with pytest.raises(ValueError):
        SandboxConfig(project_root=tmp_path, denylist={1})  # type: ignore[arg-type]


def test_result_invariants_and_serialization() -> None:
    result = SandboxResult(0, "out", "err", False, False, False, None, 1)
    assert result.duration_ms == 1.0
    expected = {
        "exit_code": 0,
        "stdout": "out",
        "stderr": "err",
        "truncated": False,
        "timed_out": False,
        "denied": False,
        "reason": None,
        "duration_ms": 1.0,
    }
    assert result.to_dict() == expected
    assert json.loads(json.dumps(result.to_dict())) == expected

    denied = SandboxResult(
        DENIED_EXIT_CODE, "", "", False, False, True, "denylist", 0
    )
    assert denied.to_dict()["denied"] is True

    timed_out = SandboxResult(
        TIMEOUT_EXIT_CODE, "", "", False, True, False, "timeout", 0
    )
    assert timed_out.to_dict()["timed_out"] is True

    with pytest.raises(ValueError):
        SandboxResult(0, "", "", False, False, True, "reason", 0)
    with pytest.raises(ValueError):
        SandboxResult(DENIED_EXIT_CODE, "", "", False, False, True, None, 0)
    with pytest.raises(ValueError):
        SandboxResult(
            DENIED_EXIT_CODE, "", "", False, True, True, "reason", 0
        )
    with pytest.raises(ValueError):
        SandboxResult(
            DENIED_EXIT_CODE, "", "", True, False, True, "reason", 0
        )
    with pytest.raises(ValueError):
        SandboxResult(TIMEOUT_EXIT_CODE, "", "", False, True, False, None, 0)
    with pytest.raises(ValueError):
        SandboxResult(
            TIMEOUT_EXIT_CODE, "", "", False, True, False, "timeout", -1
        )
    for invalid_duration in (float("nan"), float("inf"), -float("inf")):
        with pytest.raises(ValueError):
            SandboxResult(0, "", "", False, False, False, None, invalid_duration)
    with pytest.raises(ValueError):
        SandboxResult(True, "", "", False, False, False, None, 0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        SandboxResult(0, "", "", False, False, False, None, True)  # type: ignore[arg-type]


def test_truncate_below_at_and_over_limit() -> None:
    assert truncate_bytes(b"abc", 3) == (b"abc", False)
    assert truncate_bytes(b"abc", 4) == (b"abc", False)
    assert truncate_bytes(b"", 0) == (b"", False)
    assert truncate_bytes(b"abcdef", 0) == (b"", True)

    tiny, tiny_flag = truncate_bytes(b"abcdef", 5)
    assert tiny == b"abcde"
    assert tiny_flag is True

    clipped, flag = truncate_bytes(b"x" * 4096, 64)
    assert flag is True
    assert len(clipped) == 64
    assert TRUNCATION_MARKER in clipped
    assert clipped.startswith(b"x")
    assert clipped.endswith(b"x")


def test_truncate_preserves_head_and_tail() -> None:
    data = bytes(range(100))
    clipped, flag = truncate_bytes(data, 60)
    body = 60 - len(TRUNCATION_MARKER)
    head = (body + 1) // 2
    tail = body - head

    assert flag is True
    assert len(clipped) == 60
    assert clipped[:head] == data[:head]
    assert clipped[-tail:] == data[-tail:]
    assert TRUNCATION_MARKER in clipped


def test_truncate_rejects_bad_input() -> None:
    with pytest.raises(TypeError):
        truncate_bytes("text", 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        truncate_bytes(b"x", -1)
    with pytest.raises(ValueError):
        truncate_bytes(b"x", True)
    with pytest.raises(ValueError):
        truncate_bytes(b"x", 1.5)  # type: ignore[arg-type]
