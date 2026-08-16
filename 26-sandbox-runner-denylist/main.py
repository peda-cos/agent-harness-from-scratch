from __future__ import annotations

import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sandbox_runner import (  # noqa: E402
    DENIED_EXIT_CODE,
    TIMEOUT_EXIT_CODE,
    TRUNCATION_MARKER,
    Sandbox,
    SandboxConfig,
    SandboxResult,
)

SCRIPTS = {
    "hello.py": 'print("hello from the sandbox")\n',
    "read.py": (
        "import sys\n"
        "try:\n"
        "    with open(sys.argv[1], encoding='utf-8') as handle:\n"
        "        print(handle.read(), end='')\n"
        "except FileNotFoundError:\n"
        "    print(f'missing: {sys.argv[1]}', file=sys.stderr)\n"
        "    raise SystemExit(2)\n"
    ),
    "slow.py": (
        "import time\n"
        "print('started', flush=True)\n"
        "time.sleep(5)\n"
    ),
    "big.py": 'print("0123456789abcdef" * 512)\n',
    "fail.py": (
        "import sys\n"
        "print('failure detail', file=sys.stderr)\n"
        "raise SystemExit(3)\n"
    ),
}


class Checker:
    def __init__(self) -> None:
        self.count = 0

    def check(self, condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(label)
        self.count += 1


def _emit(call: object, result: SandboxResult) -> None:
    print(
        json.dumps(
            {"call": call, "result": result.to_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def run_demo() -> int:
    checker = Checker()
    results: list[SandboxResult] = []

    with tempfile.TemporaryDirectory(prefix="sandbox-demo-") as tmp:
        root = Path(tmp)
        for name, source in SCRIPTS.items():
            (root / name).write_text(source, encoding="utf-8")
        (root / "hello.txt").write_text(
            "hello from the sandbox\n", encoding="utf-8"
        )
        sandbox = Sandbox(
            SandboxConfig(
                project_root=root,
                max_output_bytes=128,
                timeout_seconds=0.5,
            )
        )

        def invoke(
            argv: Sequence[str] | str, *, shell: bool = False
        ) -> SandboxResult:
            result = sandbox.run(argv, shell=shell)
            results.append(result)
            _emit(argv, result)
            return result

        hello = invoke([sys.executable, "hello.py"])
        checker.check(hello.exit_code == 0, "hello exits zero")
        checker.check(
            hello.stdout == "hello from the sandbox\n", "hello stdout"
        )
        checker.check(hello.denied is False, "hello is not denied")

        relative = invoke([sys.executable, "read.py", "hello.txt"])
        checker.check(
            relative.stdout == "hello from the sandbox\n",
            "relative path reads the file",
        )

        absolute = invoke(
            [sys.executable, "read.py", str(root / "hello.txt")]
        )
        checker.check(
            absolute.stdout == "hello from the sandbox\n",
            "absolute path inside the root reads the file",
        )

        denied_name = invoke(["rm", "-rf", "."])
        checker.check(denied_name.denied is True, "rm is denied")
        checker.check(
            denied_name.exit_code == DENIED_EXIT_CODE,
            "denied exit code is the sentinel",
        )
        checker.check(
            denied_name.reason is not None
            and "denylisted" in denied_name.reason,
            "rm denial names the denylist",
        )

        denied_interpreter = invoke(
            [sys.executable, "-c", "print('nope')"]
        )
        checker.check(
            denied_interpreter.denied is True,
            "interpreter one-liner is denied",
        )
        checker.check(
            denied_interpreter.reason is not None
            and "inline-code" in denied_interpreter.reason,
            "interpreter denial names inline code",
        )

        denied_path = invoke(
            [sys.executable, "read.py", "../../etc/passwd"]
        )
        checker.check(
            denied_path.denied is True, "path escape is denied"
        )
        checker.check(
            denied_path.reason is not None
            and "escapes project root" in denied_path.reason,
            "path denial names the root",
        )

        denied_meta = invoke(
            [sys.executable, "hello.py", "hi; rm -rf /"]
        )
        checker.check(
            denied_meta.denied is True, "shell metacharacter is denied"
        )
        checker.check(
            denied_meta.reason is not None
            and "shell metacharacter" in denied_meta.reason,
            "metacharacter denial names the shape",
        )

        timed_out = invoke([sys.executable, "slow.py"])
        checker.check(timed_out.timed_out is True, "slow command times out")
        checker.check(
            timed_out.exit_code == TIMEOUT_EXIT_CODE,
            "timeout exit code is the sentinel",
        )
        checker.check(
            timed_out.reason is not None and "timeout" in timed_out.reason,
            "timeout reason is present",
        )
        checker.check(
            "started" in timed_out.stdout, "timeout keeps partial stdout"
        )

        truncated = invoke([sys.executable, "big.py"])
        checker.check(
            truncated.truncated is True, "large output is truncated"
        )
        checker.check(
            len(truncated.stdout.encode("utf-8")) <= 128,
            "truncated stdout respects the byte limit",
        )
        checker.check(
            TRUNCATION_MARKER.decode("utf-8") in truncated.stdout,
            "truncated stdout carries the marker",
        )

        shell_ok = invoke("echo hello", shell=True)
        checker.check(shell_ok.exit_code == 0, "shell mode can run a builtin")
        checker.check(shell_ok.stdout == "hello\n", "shell mode stdout")

        shell_denied = invoke(["rm", "-rf", "."], shell=True)
        checker.check(
            shell_denied.denied is True,
            "shell mode still checks the denylist",
        )
        checker.check(
            shell_denied.reason is not None
            and "in shell command" in shell_denied.reason,
            "shell denial names shell mode",
        )

        failed = invoke([sys.executable, "fail.py"])
        checker.check(failed.exit_code == 3, "nonzero exit is propagated")
        checker.check(
            "failure detail" in failed.stderr, "stderr is captured"
        )

        missing = invoke([sys.executable, "read.py", "missing.txt"])
        checker.check(
            missing.denied is False,
            "a missing path inside the root is not denied",
        )
        checker.check(missing.exit_code != 0, "missing file exits nonzero")

    checker.check(len(results) == 13, "demo ran thirteen calls")
    checker.check(
        sum(1 for result in results if result.denied) == 5,
        "demo counted five refusals",
    )
    checker.check(
        sum(1 for result in results if result.timed_out) == 1,
        "demo counted one timeout",
    )
    checker.check(
        sum(1 for result in results if result.truncated) == 1,
        "demo counted one truncation",
    )

    print(f"demo verified: {checker.count} checks")
    return 0


def main() -> int:
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())
