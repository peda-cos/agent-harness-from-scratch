#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export UV_CACHE_DIR="${UV_CACHE_DIR:-$PWD/.uv-cache}"
uv sync --frozen --all-groups --no-progress
.venv/bin/python -m pytest -q
.venv/bin/python -m mypy src main.py
.venv/bin/python main.py >/dev/null
