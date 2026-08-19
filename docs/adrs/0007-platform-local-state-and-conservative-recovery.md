# ADR 0007: Platform-local state and conservative crash recovery

- Status: Accepted for MVP

## Context

The repository is iCloud-backed, and SQLite/filesystem effects cannot share a transaction. Runtime state in the repository risks sync corruption and accidental commits; blind retries risk duplicate effects.

## Decision

Default runtime data to the platform-local per-user application-data directory and use temporary roots in tests. Persist intent and atomically claim a grant before a tool commit; persist result afterward. On restart migrate/open the durable projections, verify the journal, expire stale approvals, and mark incomplete calls `UNCERTAIN`. Reconcile only an exact no-overwrite postcondition; absence, mismatch, missing authority, cancellation, or stop state blocks without retry.

## Consequences

Source remains separate from mutable state and an observed exact demo write is recoverable to `VERIFYING` without duplication. A crash window still exists, cancellation cannot undo a completed atomic commit, recovered verification still needs an explicit owner-facing continuation workflow, and unsafe uncertainty requires owner review.
