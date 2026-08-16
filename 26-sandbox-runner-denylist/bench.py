"""Local refusal and spawn measurements; no network or model calls."""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sandbox_runner import Sandbox, SandboxConfig, truncate_bytes  # noqa: E402

ROUNDS = 5
DENIAL_RUNS = 50_000
PATH_RUNS = 50_000
SPAWN_RUNS = 20
TRUNCATE_RUNS = 100_000


def _median_us(samples: list[float]) -> float:
    return statistics.median(samples)


def _bench_denial(sandbox: Sandbox) -> float:
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(DENIAL_RUNS):
            sandbox.run(["rm", "-rf", "."])
        elapsed = time.perf_counter() - started
        samples.append(elapsed / DENIAL_RUNS * 1_000_000.0)
    return _median_us(samples)


def _bench_path(sandbox: Sandbox) -> float:
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(PATH_RUNS):
            sandbox.run([sys.executable, "read.py", "../outside"])
        elapsed = time.perf_counter() - started
        samples.append(elapsed / PATH_RUNS * 1_000_000.0)
    return _median_us(samples)


def _bench_spawn(sandbox: Sandbox) -> float:
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(SPAWN_RUNS):
            sandbox.run([sys.executable, "noop.py"])
        elapsed = time.perf_counter() - started
        samples.append(elapsed / SPAWN_RUNS * 1000.0)
    return statistics.median(samples)


def _bench_truncate() -> float:
    data = b"x" * 8192
    samples: list[float] = []
    for _ in range(ROUNDS):
        started = time.perf_counter()
        for _ in range(TRUNCATE_RUNS):
            truncate_bytes(data, 256)
        elapsed = time.perf_counter() - started
        samples.append(elapsed / TRUNCATE_RUNS * 1_000_000.0)
    return _median_us(samples)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sandbox-bench-") as tmp:
        root = Path(tmp)
        (root / "noop.py").write_text("pass\n", encoding="utf-8")
        (root / "read.py").write_text("import sys\n", encoding="utf-8")
        sandbox = Sandbox(
            SandboxConfig(
                project_root=root,
                max_output_bytes=256,
                timeout_seconds=5.0,
            )
        )
        denial_us = _bench_denial(sandbox)
        path_us = _bench_path(sandbox)
        spawn_ms = _bench_spawn(sandbox)
    truncate_us = _bench_truncate()

    print(
        f"denylist refusal : {denial_us:8.3f} us/call  "
        f"{1_000_000.0 / denial_us:,.0f} calls/s"
    )
    print(
        f"path-jail refusal: {path_us:8.3f} us/call  "
        f"{1_000_000.0 / path_us:,.0f} calls/s"
    )
    print(
        f"clean python run : {spawn_ms:8.3f} ms/call  "
        f"{1_000.0 / spawn_ms:,.1f} calls/s"
    )
    print(
        f"truncate 8 KiB   : {truncate_us:8.3f} us/call  "
        f"{1_000_000.0 / truncate_us:,.0f} calls/s"
    )

    for name, value in (
        ("denylist_denial_us", denial_us),
        ("path_jail_denial_us", path_us),
        ("clean_python_spawn_ms", spawn_ms),
        ("truncate_8k_to_256_us", truncate_us),
    ):
        print(
            json.dumps(
                {"benchmark": name, "value": value},
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
