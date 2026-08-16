from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from sandbox_runner import Sandbox, SandboxConfig


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    (root / "hello.txt").write_text(
        "hello from the sandbox\n", encoding="utf-8"
    )
    (root / "sub").mkdir()
    (root / "sub" / "nested.txt").write_text("nested\n", encoding="utf-8")
    return root


@pytest.fixture
def sandbox(project_root: Path) -> Sandbox:
    return Sandbox(SandboxConfig(project_root=project_root))


@pytest.fixture
def write_script(project_root: Path) -> Callable[[str, str], Path]:
    def write(name: str, source: str) -> Path:
        path = project_root / name
        path.write_text(source, encoding="utf-8")
        return path

    return write
