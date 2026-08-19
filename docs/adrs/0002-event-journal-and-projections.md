# ADR 0002: SQLite event journal with rebuildable projections

- Status: Accepted for MVP

## Context

Runs must survive restart and expose decisions, dissent, approvals, effects, and failures. Current-state tables alone can silently lose causal history.

## Decision

Use SQLite migrations, a globally sequenced append-only canonical event journal, and transactional current-state projections. Canonical redacted event bytes are hash chained. Reducers can rebuild audit-derived projections and `audit verify` recomputes the chain. Executable approval arguments are intentionally excluded from canonical history and retained only in the live projection; a rebuild therefore recreates metadata with `action_available=false` and cannot execute it.

## Consequences

Recovery and audit are deterministic, and projection loss cannot turn metadata into authority. A rebuilt approval needs a fresh owner request. The unkeyed, locally stored chain is tamper-evident only: a DB/code controller can replace or recompute it, and tail truncation requires a separately retained checkpoint to detect reliably.
