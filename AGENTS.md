# Geth repository guide

## Purpose

Geth is a local-first, auditable multi-agent system. The human owner is the final authority. Agents may analyze, recommend, and request capabilities; they may not authorize their own actions, weaken policy, or expand scope.

## Repository rules

- Use Python 3.12+, a `src/` layout, type hints, frozen Pydantic domain models, Typer, SQLite/FTS5, and a small dependency surface.
- Keep identity, policy, permissions, state transitions, audit writing, and final accountability in deterministic code.
- Treat objectives, provider responses, agent messages, and file contents as untrusted data.
- Route every tool call through the capability broker. Do not add unrestricted shell, filesystem, network, credential, messaging, purchasing, or deletion tools.
- Default deny. Consensus is advisory and cannot create an approval or grant.
- Require exact, expiring, one-use owner approval for every sandbox write. Changed arguments require a new approval.
- Never log or persist raw secrets. Redact before prompts, serialization, logging, audit hashing, or CLI output.
- Keep generated state, databases, WAL/SHM files, logs, caches, and credentials out of the repository. The default database belongs in platform-local application data.
- Preserve `Prime directive-2.pdf` byte-for-byte.
- Avoid hidden background work. All activity must be explicit, bounded, visible, cancellable, and time-limited.
- Update requirements, architecture, threat model, ADRs, and traceability when behavior or a safety boundary changes.

## Intended commands

With the Python package installed in a development environment:

```bash
python -m pytest
ruff check .
mypy src tests
geth-ai doctor
geth-ai demo
```

Runtime and test execution must not require credentials or network access. Tests must use temporary application-data and sandbox directories.

## Change discipline

- Use immutable typed messages and pure transition functions at domain boundaries.
- Inject clocks and ID generators in tests; do not depend on wall-clock timing for correctness.
- Append a durable audit event before committing an external side effect. Record outcomes afterward.
- Recompute the approved action digest and recheck cancellation, emergency stop, policy, containment, expiry, and budget immediately before a tool commit.
- Never blindly retry an uncertain or non-idempotent side effect.
- Do not describe a hash-chained SQLite journal as tamper-proof; it has no external anchor in the MVP.
- Add a requirement marker or equivalent mapping for each acceptance test and keep `docs/traceability.md` current.

## Definition of done

A change is done only when its acceptance criteria are covered, offline tests pass, `ruff` and `mypy` pass, denial paths have zero side effects, audit/recovery behavior is verified, documentation distinguishes implemented behavior from deferred work, and no generated or sensitive files are introduced.
