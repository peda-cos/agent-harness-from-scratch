from __future__ import annotations

from pathlib import Path

from end_to_end_agent import CodingAgentPolicy, ToolCall

MODULE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = MODULE_ROOT / "fixture_repo"
BUGGY_SOURCE = (FIXTURE_ROOT / "src" / "fizz.py").read_text(encoding="utf-8")
EXPECTED_SOURCE = (
    FIXTURE_ROOT / "expected" / "src" / "fizz.py"
).read_text(encoding="utf-8")
FAILURE_OUTPUT = (
    "FAILED (failures=2)\n"
    'AssertionError: Lists differ: [...] != [...]\n'
    "fizzbuzz(15) must end with 'FizzBuzz'\n"
)
LISTING = "src/fizz.py\ntests/test_fizz.py\n"


def _next(policy: CodingAgentPolicy, step: int) -> ToolCall:
    call = policy.next_call(step)
    assert call is not None
    return call


def _survey(policy: CodingAgentPolicy) -> None:
    call = _next(policy, 1)
    assert call.name == "read_file"
    assert call.arguments == {"path": "."}
    policy.observe(call, LISTING, 0)


def test_happy_path_emits_exactly_five_calls_in_order() -> None:
    policy = CodingAgentPolicy()
    assert policy.state == "SURVEY"

    _survey(policy)
    assert policy.state == "RUN_TESTS"

    failure_call = _next(policy, 2)
    assert failure_call.name == "run_tests"
    assert failure_call.arguments == {}
    policy.observe(failure_call, FAILURE_OUTPUT, 1)
    assert policy.state == "INSPECT"

    inspect_call = _next(policy, 3)
    assert inspect_call.name == "read_file"
    assert inspect_call.arguments == {"path": "src/fizz.py"}
    policy.observe(inspect_call, BUGGY_SOURCE, 0)
    assert policy.state == "FIX"

    fix_call = _next(policy, 4)
    assert fix_call.name == "write_file"
    assert fix_call.arguments["path"] == "src/fizz.py"
    assert fix_call.arguments["content"] == EXPECTED_SOURCE
    policy.observe(fix_call, "wrote 649 bytes to src/fizz.py", 0)
    assert policy.state == "VERIFY"

    verify_call = _next(policy, 5)
    assert verify_call.name == "run_tests"
    policy.observe(verify_call, "OK", 0)

    assert policy.state == "SUCCESS"
    assert policy.done
    assert policy.failure_reason is None
    assert policy.next_call(6) is None


def test_tests_passing_in_run_tests_halts_without_inspection() -> None:
    policy = CodingAgentPolicy()
    _survey(policy)
    run_call = _next(policy, 2)
    policy.observe(run_call, "OK", 0)
    assert policy.state == "SUCCESS"
    assert policy.done


def test_unrecognized_failure_halts_before_any_write() -> None:
    policy = CodingAgentPolicy()
    _survey(policy)
    run_call = _next(policy, 2)
    policy.observe(run_call, "FAILED (errors=1)", 1)
    assert policy.state == "FAILURE"
    assert policy.failure_reason is not None
    assert "unrecognized" in policy.failure_reason
    assert policy.next_call(3) is None


def test_source_without_the_known_off_by_one_is_rejected() -> None:
    policy = CodingAgentPolicy()
    _survey(policy)
    run_call = _next(policy, 2)
    policy.observe(run_call, FAILURE_OUTPUT, 1)
    inspect_call = _next(policy, 3)
    policy.observe(inspect_call, EXPECTED_SOURCE, 0)
    assert policy.state == "FAILURE"
    assert policy.failure_reason is not None
    assert "known off-by-one" in policy.failure_reason


def test_failed_verification_halts_with_failure() -> None:
    policy = CodingAgentPolicy()
    _survey(policy)
    failure_call = _next(policy, 2)
    policy.observe(failure_call, FAILURE_OUTPUT, 1)
    inspect_call = _next(policy, 3)
    policy.observe(inspect_call, BUGGY_SOURCE, 0)
    fix_call = _next(policy, 4)
    policy.observe(fix_call, "wrote bytes", 0)
    verify_call = _next(policy, 5)
    policy.observe(verify_call, FAILURE_OUTPUT, 1)
    assert policy.state == "FAILURE"
    assert policy.failure_reason is not None
    assert "still fail" in policy.failure_reason
