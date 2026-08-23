from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from end_to_end_agent import Repo

MODULE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = MODULE_ROOT / "fixture_repo"


@pytest.fixture
def repo() -> Iterator[Repo]:
    with Repo(FIXTURE_ROOT) as value:
        yield value
