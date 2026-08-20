# Geth project status and integrations

**Status date:** 2026-08-19

## Executive summary

Geth is a working offline vertical-slice MVP for safe, local-first, auditable
multi-agent orchestration. Its central security property is that the human owner remains
the sole root authority. Agents may analyze, challenge, recommend, execute narrowly
approved actions, and verify results, but deterministic code owns identity, policy,
permissions, budgets, state transitions, audit writing, and final accountability.

The current implementation is a Python 3.12+ package with a Typer CLI, frozen Pydantic
domain models, SQLite persistence, FTS5 memory, a fake offline provider, and a narrow
filesystem capability broker. Runtime and tests do not require credentials or network
access. Live providers, hosted services, a GUI, background scheduling, and broad external
tools remain deliberately deferred.

## Implemented system

The MVP currently includes:

- Seven bounded roles: Steward, Strategist, Skeptic, Synthesizer, Local Commander,
  Executor, and Verifier.
- Typed immutable messages and deterministic state transitions.
- Advisory consensus with `PASS`, `REVISE`, and `ESCALATE_TO_OWNER` outcomes while
  retaining dissent.
- Default-deny policy evaluation.
- Exact, expiring, one-use owner approvals bound to canonical action details.
- Bounded workspace listing and reading plus approval-gated sandbox text creation.
- Atomic approval claiming, no-overwrite filesystem behavior, and independent readback
  and verification.
- An append-oriented SQLite event journal, rebuildable projections, chained event hashes,
  restart support, and conservative interrupted-effect recovery.
- Explicit owner-controlled FTS5 memory with remember, search, export, correction, and
  forgetting operations.
- Emergency stop, cancellation, budgets, leases, redacted diagnostics, audit inspection,
  and health checks.
- Offline unit, component, CLI, and end-to-end acceptance coverage.

The SQLite hash chain is correctly described as tamper-evident rather than tamper-proof.
Without an independently retained external anchor, a party controlling both the database
and code could replace or recompute a valid chain, and tail truncation cannot be detected
reliably.

## Repository map

- `src/geth_ai/domain/`: immutable models, identifiers, messages, canonicalization,
  consensus types, and pure transitions.
- `src/geth_ai/policy/`: authority, actions, delegation, redaction, and deterministic
  policy evaluation.
- `src/geth_ai/application/`: orchestration, consensus, approvals, recovery, memory,
  emergency control, clocks, health, and bootstrap wiring.
- `src/geth_ai/tools/`: typed capability protocol, registry, broker, path containment, and
  bounded filesystem adapters.
- `src/geth_ai/persistence/`: SQLite database setup, migrations, event store, repositories,
  projections, canonical JSON, integrity, and audit safety.
- `src/geth_ai/providers/`: provider protocol and deterministic fake provider.
- `src/geth_ai/observability/`: redacted logging and audit rendering.
- `tests/`: unit, component, CLI, and end-to-end tests.
- `docs/`: constitution, requirements, assumptions, architecture, threat model,
  traceability, and architecture decision records.
- `Prime directive.pdf`: the source directive that must be preserved byte-for-byte.

Generated caches, virtual environments, package metadata, databases, WAL/SHM files,
logs, and local Geth state are excluded through `.gitignore` and must remain outside
version control.

## Local development workflow

Python 3.12 or newer is required. The intended verification commands are:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/mypy src tests
```

The primary local demonstration is:

```bash
geth-ai --workspace "$PWD" init
geth-ai --workspace "$PWD" demo
```

The demo plans a bounded action and stops in `AWAITING_APPROVAL`. The owner must approve
the exact displayed request separately before execution. Denial, cancellation, expiry,
changed arguments, emergency stop, containment failure, or unavailable exact arguments
must fail closed with no unauthorized side effect.

## Git and GitHub setup

GitHub Desktop for Apple silicon is the selected native desktop client. It is responsible
for local visual diffs, commits, branches, pulls, pushes, and publishing or opening the
repository on GitHub.

The GitHub connection installed in Codex is separate from GitHub Desktop. It allows Codex
to inspect and, when explicitly authorized, act on remote repositories, issues, pull
requests, reviews, and workflow checks. Local editing remains rooted in this workspace.

Repository setup history:

- GitHub Desktop had configured the local Git author identity.
- Codex was authenticated to the owner's GitHub account.
- The workspace was initialized locally and its initial project history was committed.
- The real project history was consolidated into `GETH_for_hire`.
- The owner selected the Apache License, Version 2.0 for the published repository.
- The package metadata and top-level `LICENSE` identify the project as Apache-2.0.

Remote GitHub writes and local commits are distinct actions. Repository publishing,
pushing, issue creation, pull-request creation, reviews, merges, and other consequential
actions should remain explicit and owner-authorized.

## Codex connections

### GitHub

The GitHub plugin is installed and authenticated. Its current app permission inherits the
default Codex setting, `Allow low-risk actions`. For alignment with Geth's approval model,
an app-specific `ask before writes` or `always ask` posture should be considered before
using Codex for consequential GitHub operations.

### Codex Security

The Codex Security plugin is installed. It provides standard and deep repository scans,
security diff scans, finding discovery and validation, attack-path analysis, threat-model
support, vulnerability writeups, remediation workflows, hardening proposals, and finding
tracking integrations.

The separate Trusted Access for Cyber status was `not_granted` when checked on
2026-08-19. Enrollment is available at <https://chatgpt.com/cyber>. That status is
advisory, but enrollment is the appropriate next step before relying on the complete
security workflow.

### Connections intentionally deferred

- OpenAI Developers until live-provider network, credential, privacy, cost, and retention
  policies are designed.
- Sentry and Datadog until a deployed or continuously running production service exists.
- PostHog until a GUI exists and telemetry has an explicit privacy policy.
- Slack or Teams until a collaboration workflow exists; neither may become an implicit
  approval authority.
- Supabase because the current persistence architecture is intentionally local SQLite.
- Vercel and Cloudflare because no hosted web application exists.
- Email, calendar, CRM, sales, and marketing connections because they add broad private
  data and side-effect surfaces without serving the current MVP.
- External document systems unless the repository remains the authoritative auditable
  source.

## Security and governance implications

Codex plugins are development-time connections; installing them does not grant equivalent
capabilities to the Geth runtime. Any future Geth integration with GitHub, a model provider,
messaging, cloud storage, telemetry, or another external service requires its own explicit
design and must:

1. Route every call through the capability broker.
2. Default deny and expose only narrow typed operations.
3. Require an exact, expiring, one-use owner approval for each sandbox write or external
   side effect.
4. Recompute the approved action digest and recheck cancellation, emergency stop, policy,
   containment, expiry, and budget immediately before commit.
5. Append a durable audit event before the side effect and record its outcome afterward.
6. Redact secrets before prompts, serialization, logs, audit hashing, or CLI output.
7. Avoid blind retries for uncertain or non-idempotent effects.
8. Update requirements, architecture, threat model, ADRs, tests, and traceability whenever
   a behavior or safety boundary changes.

## Recommended next steps

1. Confirm that ignored caches, databases, logs, local application state, and credentials
   remain absent from GitHub.
2. Configure the GitHub connection to request confirmation for writes.
3. Complete Codex Security enrollment and run a standard repository scan.
4. Triage validated findings, then decide whether a deeper multi-pass scan is warranted.
5. Run `pytest`, Ruff, and mypy before merging changes.
6. Prioritize external audit-head anchoring and an explicit verification-resume command,
   while retaining the default offline and no-background-work posture.

## Authoritative references

- [`README.md`](../README.md)
- [`docs/constitution.md`](constitution.md)
- [`docs/requirements.md`](requirements.md)
- [`docs/architecture.md`](architecture.md)
- [`docs/threat-model.md`](threat-model.md)
- [`docs/traceability.md`](traceability.md)
- [`docs/assumptions-and-non-goals.md`](assumptions-and-non-goals.md)
- [`docs/adrs/`](adrs/)
- [`AGENTS.md`](../AGENTS.md)
