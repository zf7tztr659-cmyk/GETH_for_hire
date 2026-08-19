# ADR 0005: Typed advisory consensus behind a provider protocol

- Status: Accepted for MVP

## Context

The MVP must demonstrate multiple bounded roles offline without mistaking conversational agreement for authorization or requiring multiple vendors/machines.

## Decision

Define a small typed provider protocol and deterministic fake implementation. Run independent proposals, critique, deterministic policy, and synthesis with `PASS`, `REVISE`, or `ESCALATE_TO_OWNER`; persist confidence and dissent. Bound deliberation to two rounds. Providers cannot invoke tools or approve.

## Consequences

Tests are reproducible and credential-free. Role contexts are distinct but share one deterministic provider and therefore have correlated failure. Live providers remain deferred adapters requiring privacy, cost, and network controls.
