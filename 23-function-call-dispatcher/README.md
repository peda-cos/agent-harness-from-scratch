# Function Call Dispatcher

A typed dispatch seam between an agent harness and its tool registry. It owns
the mechanics the rest of the loop should not: per-attempt timeouts, jittered
retries, idempotency-key dedupe, one JSON-RPC-shaped error envelope, and a
bounded fan-out.

The implementation is Python-only, offline, and deterministic. No model,
network service, credentials, or paid API is involved.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd 23-function-call-dispatcher
./verify.sh
.venv/bin/python main.py
.venv/bin/python bench.py
```

`verify.sh` installs the pinned test/typecheck dependencies into a local
ignored `.venv`, runs the test suite and strict mypy, then runs the
self-verifying demo. It is safe to run twice.

## Layout

```text
main.py                       offline demo with self-checks
bench.py                      local dispatch throughput measurement
src/function_dispatcher/
  __init__.py                 public exports
  errors.py                   error kinds and JSON-RPC mapping
  registry.py                 compact tool registry fixture
  budget.py                   tool-call budget protocol
  dispatcher.py               timeout, retry, dedupe, fan-out
tests/
  test_registry.py            registry and validation rules
```
