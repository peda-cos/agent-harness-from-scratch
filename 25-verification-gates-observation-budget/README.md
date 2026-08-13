# Verification Gates and the Observation Budget

An agent harness without a verification layer is a wish in a trenchcoat. This module
implements the deterministic gate chain that decides whether a tool call may fire, how
much of its output the model is allowed to see, and when the session has to stop because
the observation budget is exhausted.

The mechanism is a small ordered chain — whitelist, regex, recency, global budget, and
per-tool budget gates — backed by an observation ledger. The demo, tests, and benchmark
run offline against deterministic fixtures. This is module
`25-verification-gates-observation-budget` of the track repository; it does not import
sibling modules.

Full documentation, measured results, and references are added after the implementation
lands.
