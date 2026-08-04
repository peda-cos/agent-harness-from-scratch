#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

uv sync --frozen --all-groups --no-progress
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy src main.py
.venv/bin/python main.py >/dev/null
