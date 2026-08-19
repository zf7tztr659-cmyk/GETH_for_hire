# ADR 0001: Deterministic trusted core and small architecture

- Status: Accepted for MVP

## Context

Agent output is probabilistic and untrusted, while identity, permissions, transitions, and audit require deterministic enforcement. A large agent framework would enlarge the trusted surface without proving value for the vertical slice.

## Decision

Implement a small Python 3.12 `src/` package. Centralize orchestration, state transitions, policy, approvals, broker routing, budgets, cancellation, and audit in explicit typed code. Treat providers, roles, files, and messages as untrusted inputs. Do not adopt a general agent framework for the MVP.

## Consequences

Behavior is inspectable and testable, but Geth must build its own narrow orchestration and recovery code. Any future framework adoption requires a new ADR demonstrating preserved boundaries and lower total risk.
