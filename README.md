# Geth

Geth is a working offline vertical-slice MVP for safe, local-first, auditable
multi-agent orchestration. A human objective is analyzed by bounded roles, challenged,
synthesized with retained dissent, checked by deterministic policy, paused for an exact
owner approval, executed through a narrow capability broker, independently read back and
verified, and recorded in a local audit timeline.

The human owner is the sole root authority. Geth has no independent sovereignty,
self-preservation objective, background daemon, or ability to grant itself permission.

## Implemented status

| Capability | MVP status | Proof |
|---|---|---|
| Seven bounded roles and typed immutable messages | Implemented | Deterministic consensus and orchestration tests |
| `PASS` / `REVISE` / `ESCALATE_TO_OWNER` with retained dissent | Implemented | Two-round/revision/veto tests |
| Exact, expiring, one-use owner approval | Implemented | Binding, expiry, replay, concurrency, cancellation, and rebuild tests |
| Bounded workspace list/read and sandbox write | Implemented | Typed broker, disjoint exact-root routing, traversal/link/limit tests |
| Independent post-effect verification | Implemented | Exact path/content/hash readback followed by a separate Verifier role |
| SQLite event journal and projections | Implemented | Migrations, replay, restart, hash-chain, and corruption tests |
| Conservative interrupted-effect recovery | Implemented | Exact reconciliation or `BLOCKED`; never blind retry |
| Explicit owner-controlled SQLite FTS5 memory | Implemented | Remember/search/export/correct/forget and inert-improvement tests |
| Offline Typer CLI and doctor | Implemented | Console acceptance suite with network guard and temporary state |
| Live/cloud provider, partner administration, daemon, GUI | Deferred | Deliberately outside the MVP authority surface |

## Install and verify

Python 3.12 or newer is required.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/ruff check .
.venv/bin/mypy src tests
```

Dependency installation may use a package index. Runtime, the fake provider, the demo,
doctor, and the test suite require no credentials, cloud service, live model, or network
access.

## Run the vertical slice

Global path options precede the command. If omitted, application state uses the platform
local data directory (`~/Library/Application Support/GethAI` on macOS,
`$XDG_DATA_HOME/geth-ai` on Linux, or the local application-data directory on Windows),
outside this iCloud workspace.

```bash
geth-ai --workspace "$PWD" init
geth-ai --workspace "$PWD" demo
```

`demo` is deliberately no-side-effect by default. It records all planning roles and stops
in `AWAITING_APPROVAL`, displaying the exact tool, target, action digest, expiry, run ID,
retained dissent, and audit command. Redeem only the displayed request in a separate owner
action:

```bash
geth-ai --workspace "$PWD" approve <approval-id>
geth-ai --workspace "$PWD" audit show <run-id>
geth-ai --workspace "$PWD" audit verify
```

Approval re-displays the exact action metadata before execution. The broker then claims it
atomically, creates one absent sandbox file without overwrite, reads it back through an
independent capability, compares exact bytes/path/hash, calls the separate Verifier role,
and reaches `COMPLETED` only on success. Restarting the CLI between `demo` and `approve` is
supported.

To prove zero-effect alternatives, start another demo and use one of:

```bash
geth-ai deny <approval-id>
geth-ai cancel <run-id>
```

## Command surface

```text
geth-ai init
geth-ai demo [--scenario pass|revise_then_pass|escalate|...]
geth-ai run "<objective>" --provider fake
geth-ai task list
geth-ai task show <run-id>
geth-ai approve <approval-id>
geth-ai deny <approval-id>
geth-ai cancel <run-id>
geth-ai audit show <run-id>
geth-ai audit verify
geth-ai memory remember <run-id> "<content>" [--category ...]
geth-ai memory search "<query>"
geth-ai memory export
geth-ai memory correct <memory-id> "<content>" --run-id <run-id>
geth-ai memory forget <memory-id>
geth-ai emergency-stop
geth-ai emergency-reset --confirm RESET
geth-ai doctor
```

Use global `--verbose` for redacted structured diagnostics. Default output is concise and
answer-first. There is no idle loop, scheduler, worker, hidden persistence, or daemon.

## Architecture and safety boundary

The trusted deterministic core owns identity, transitions, policy, budgets, approvals,
capability routing, cancellation, recovery, and audit. Provider responses, objectives,
files, messages, and evidence are untrusted typed data: they can inform a recommendation
but cannot mutate trusted state, approve an action, add a tool, change arguments, weaken
policy, or suppress an event.

The fake provider supplies distinct Steward, Strategist, Skeptic, Synthesizer, Local
Commander, Executor, and Verifier calls. Consensus remains advisory. Policy is default
deny and supports only bounded local reads plus an approval-gated `sandbox.write_text`.
There is no shell, network, credential, messaging, purchasing, general deletion, or
runtime source/prompt/policy activation tool.

Approvals bind canonical arguments, content hash/length, actor, owner, run/work item,
exact root/target, tool/schema/policy versions, expected prior state, budget, expiry, and
nonce. A changed field creates a different digest. Exact action content is retained only
in the live mutable approval projection so a normal restart can continue; canonical audit
events retain safe metadata and digests. If projections are rebuilt from the journal and
exact arguments no longer exist, execution fails closed and requires a fresh approval.

The SQLite journal is append-only through normal repositories and uses canonical JSON,
global/per-run sequences, prior hashes, and chained event hashes. This is tamper-evident,
not tamper-proof: control of both database and code permits valid-chain replacement or
recomputation, and tail truncation needs a separately retained head/count to detect.

## Recovery and memory

Normal restarts preserve runs, messages, budgets, leases, approvals, cancellation, and
audit. Startup validates the chain, expires stale approvals, and treats an interrupted
`RUNNING` write as `UNCERTAIN`. It observes the exact approved no-overwrite postcondition:
a byte-for-byte match is reconciled to `VERIFYING` for independent verification; absence,
mismatch, lost exact arguments, cancellation, or emergency stop blocks for owner review.
Recovery never invokes a provider and never retries a filesystem effect.

Memory is never captured automatically. `memory remember` accepts only explicit facts,
outcomes, owner feedback, or improvement candidates with run provenance and sensitivity.
The owner can inspect, FTS-search, export, correct by supersession, and forget it. Forgetting
removes content and FTS rows while preserving only content-free provenance hashes in the
audit. Improvement candidates cannot activate themselves.

## Documentation map

- `docs/constitution.md`: normalized, enforceable interpretation of the manifesto.
- `docs/requirements.md`: numbered MVP obligations and acceptance criteria.
- `docs/assumptions-and-non-goals.md`: conservative defaults and non-goals.
- `docs/architecture.md`: trust boundaries, protocols, state machines, and recovery.
- `docs/threat-model.md`: assets, entry points, abuse cases, mitigations, residual risk.
- `docs/traceability.md`: principle → requirement → module → automated proof.
- `docs/adrs/`: focused architecture decisions.

## Known limitations and deferred work

Ordered by expected value and safety risk:

1. Add owner-controlled external audit-head anchoring to detect valid-chain replacement and
   tail truncation; this introduces a new privacy/network boundary and needs explicit design.
2. Add an explicit owner command to resume independent verification after recovery has
   reconciled an interrupted effect; the MVP safely leaves such runs in `VERIFYING`.
3. Add a live provider behind the existing protocol only after defining network, credential,
   privacy, cost, and data-retention policy. The default must remain offline.
4. Add OS-level sandboxing and platform keystore integration for stronger protection against
   a compromised adapter or local account; the MVP is an application reference monitor.
5. Add explicit partner enrollment/delegation administration and multi-owner review flows.
6. Consider opt-in bounded background scheduling only with visible leases, cancellation,
   expiry, and health controls; no background process exists today.

No owner decision is required to use the offline MVP. Future live-provider access, external
audit anchoring, partner authority, retention policy, and any background operation all
require separate owner decisions and are not silently enabled.

## License

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
