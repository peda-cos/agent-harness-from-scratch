# Track

A collection of 10 tested projects built from scratch, one module per capstone lesson.

Each module has its own `README.md`, `verify.sh`, tests, and a deterministic offline demo.

## Modules

| # | Module | What it demonstrates | Verify |
|---:|---|---|---|
| 20 | [Agent Harness Loop Contract](20-agent-harness-loop-contract/) | The harness is the agent. The model is a coprocessor. This lesson freezes the loop contract you can wire any model into. | `cd 20-agent-harness-loop-contract && ./verify.sh` |
| 21 | [Tool Registry with Schema Validation](21-tool-registry-schema-validation/) | A tool the agent cannot validate is a tool the agent cannot call. Build the registry and the schema checker before you build the tools. | `cd 21-tool-registry-schema-validation && ./verify.sh` |
| 22 | [JSON-RPC 2.0 Over Newline-Delimited Stdio](22-jsonrpc-stdio-transport/) | The transport between a model client and a tool server is JSON-RPC over stdio. Hand-rolling it once teaches you what every framing layer is  | `cd 22-jsonrpc-stdio-transport && ./verify.sh` |
| 23 | [Function Call Dispatcher](23-function-call-dispatcher/) | The dispatcher is where the harness pays for every promise the schema made. Timeouts, retries, dedupe, error mapping. All on one seam. | `cd 23-function-call-dispatcher && ./verify.sh` |
| 24 | [Plan-Execute Control Flow](24-plan-execute-control-flow/) | A plan that cannot survive a failure is a script. A script that can replan is an agent. Build the replanner first. | `cd 24-plan-execute-control-flow && ./verify.sh` |
| 25 | [Capstone Lesson 25: Verification Gates and the Observation Budget](25-verification-gates-observation-budget/) | An agent harness without a verification layer is a wish in a trenchcoat. This lesson builds the deterministic gate chain that decides whethe | `cd 25-verification-gates-observation-budget && ./verify.sh` |
| 26 | [Capstone Lesson 26: Sandbox Runner with Denylist and Path Jail](26-sandbox-runner-denylist/) | The verification gate decides whether a tool call should run. The sandbox decides what happens when it does. This lesson ships a subprocess  | `cd 26-sandbox-runner-denylist && ./verify.sh` |
| 27 | [Capstone Lesson 27: Eval Harness with Fixture Tasks](27-eval-harness-fixture-tasks/) | A coding agent is only as good as the suite of tasks you measure it against. This lesson builds an evaluation harness that takes a folder of | `cd 27-eval-harness-fixture-tasks && ./verify.sh` |
| 28 | [Capstone Lesson 28: Observability with OTel GenAI Spans and Prometheus Metrics](28-observability-otel-traces/) | An agent harness without observability is a black box that costs money. This lesson hand-rolls a span builder that emits records compliant w | `cd 28-observability-otel-traces && ./verify.sh` |
| 29 | [Capstone Lesson 29: End-to-End Coding Agent on the Harness](29-end-to-end-coding-task-demo/) | Track A's payoff. This lesson stitches the gate chain, the sandbox, the eval harness, and the OTel spans into one working coding agent that  | `cd 29-end-to-end-coding-task-demo && ./verify.sh` |

## Requirements

- Python 3.12+ and `uv` for Python modules
- Node 22+ / Bun 1.4 for TypeScript modules
- No API keys, GPU, or network needed for the test suites

## Verify all

```bash
make verify
```

## References

- Capstone source: https://github.com/rohitg00/ai-engineering-from-scratch/tree/main/phases/19-capstone-projects
