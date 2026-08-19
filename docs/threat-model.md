# Geth MVP threat model

Status: active security analysis for the implemented offline vertical slice. This model protects against accidental misuse, prompt-driven confused-deputy behavior, bounded local adapter faults, and ordinary history corruption. It does not claim protection from a compromised owner account, executable, dependency, kernel, or database administrator.

## Security goals

1. Only the human owner can authorize a material side effect.
2. Authority cannot be created or widened by agents, providers, files, consensus, or delegated principals.
3. Denial, expiry, cancellation, and emergency stop produce no new external side effect.
4. An approved action can cause only its exact, one-time, scoped effect.
5. Untrusted content cannot change policy, arguments, roots, state, or audit behavior.
6. Secrets and unnecessary sensitive content do not enter prompts or ordinary persistence.
7. Durable history supports inspection and safe restart, with integrity limitations stated accurately.
8. Resource limits terminate loops, retries, recursion, stale work, and excessive reads deterministically.

## Assets

- Owner identity, intent, approvals, cancellation, and emergency-stop state.
- Constitution, requirements, policy, configuration, tool schemas, prompts, provider versions, source, and `Prime directive-2.pdf`.
- Workspace content and the dedicated write sandbox.
- Capability grants, action digests, budgets, leases, and tool preconditions.
- Canonical event journal, projections, artifacts, evidence, dissent, and recovery state.
- Memory content, provenance, corrections, and deletion status.
- Secrets, credentials, sensitive paths, logs, and provider inputs.

## Actors and trust

| Actor/component | Trust in the MVP |
|---|---|
| Human owner through local CLI | Root authority, authenticated only by the local OS account and file permissions |
| Optional principal | Trusted only inside a valid explicit delegation; no production CLI account flow |
| Agent role / fake provider | Untrusted adviser; no approval, state, audit, or direct tool authority |
| Objective, file, evidence, message | Untrusted content, including instruction-like text |
| Orchestrator/state/policy/approval/broker/audit | Trusted reference monitor and primary security boundary |
| Filesystem adapter | Privileged but narrow, constrained, and fallible |
| SQLite, filesystem, clocks | Local dependencies that can fail or be manipulated by a stronger local attacker |
| Local malware/admin, compromised dependency, iCloud peer | Outside the security boundary |
| Future provider/plugin/network service | Untrusted and deferred; no MVP route exists |

## Entry points and trust boundaries

- CLI input crosses from the owner/OS identity assumption into typed application commands.
- Workspace bytes cross into evidence and provider context only after path, type, size, sensitivity, and redaction checks.
- Provider output crosses into strict typed models; it cannot call trusted services directly.
- Action proposals cross from orchestration into deterministic policy, then into exact approval.
- Grants cross into the broker through an atomic claim and are revalidated at tool-commit time.
- Tool results cross back as untrusted observations and require independent verification.
- Events cross into durable SQLite history; recovery trusts verified canonical events over projections.

## Exact MVP permission boundary

| Action | Decision |
|---|---|
| Internal SQLite/audit writes | Implicitly authorized by an explicit Geth command; platform-local or test-temp only |
| Workspace list/read | Allowed only through typed adapters, selected roots, regular non-sensitive files, and budgets |
| Sandbox write | Exact owner approval; one new file, exact bytes, no overwrite, one use |
| Process or shell execution | Unsupported and denied |
| Network or live provider | Unsupported and denied |
| Secret/credential access | Unsupported and denied |
| External communication/publication | Unsupported and denied |
| Financial/cost-bearing action | Unsupported and denied |
| Workspace/external deletion or destructive tool | Unsupported and denied; owner-only internal memory forgetting is separate |
| Runtime source/policy/config/prompt activation | Unsupported and denied; proposal only |

Unknown action types, tools, schemas, fields, principals, roots, and policy versions fail closed. An approval record cannot override an unsupported class.

## Threats and controls

| ID | Threat / abuse case | Primary controls | Residual risk |
|---|---|---|---|
| TM-01 | Agent/provider/file forges owner identity or approval. | Approval service is not in provider/tool APIs; OS-bound owner command; strict typed commands; audit denial. | Compromised local account can impersonate the owner. |
| TM-02 | Prompt injection says “ignore policy,” grants permission, changes arguments, or suppresses audit. | Label all content untrusted; roles only emit proposals; policy/broker construct actions independently; audit writer is outside provider control. | Injection may still degrade recommendation quality. |
| TM-03 | Principal delegates more authority than it has. | Parent-linked delegations; subset checks for action, root, budget, and expiry; default deny. | Single-user owner authentication remains weak. |
| TM-04 | Approval is replayed, raced, used across runs/actors, or used after expiry. | Digest binds every security field; random nonce; atomic compare-and-swap claim; one-use state; fresh revalidation. | SQLite/OS compromise can alter records. |
| TM-05 | Traversal, absolute path, NUL, alternate separator, symlink, dangling link, hard link, or non-regular file escapes a root. | Relative normalized paths; directory-descriptor component walk; no-follow; type checks; target-absent write; hard-link destination rejection. | Portable primitives differ across platforms. |
| TM-06 | Target/parent changes between approval/check and write. | Bind canonical root/resolved target and prior state; hold root/parent descriptors; recheck identity immediately before atomic no-overwrite install. | A narrow filesystem race can remain without kernel “beneath” resolution. |
| TM-07 | Denied call leaves a temp file, directory, metadata, or partial target. | Broker denies before adapter; adapter creates nothing before final authorization checks; full tree snapshot tests. | Filesystem faults may affect unrelated system metadata outside observable scope. |
| TM-08 | Secret leaks through objective, evidence, response, error, log, audit, artifact, memory, SQLite, WAL, or CLI. | Sensitive-path deny, byte minimization, recursive redaction before every sink, no credential tool, canary scanning. | Redaction cannot identify every secret; storage forensics/backups are out of scope. |
| TM-09 | Audit event is mutated, deleted, inserted, reordered, truncated, or suppressed. | Sole append API, DB constraints/triggers, global sequence, canonical payload hashes, chained hashes, event-before-effect protocol, rebuildable audit-derived projections. | DB/code controller can recompute/replace the chain; tail truncation needs an external checkpoint; exact executable approval arguments intentionally cannot be rebuilt from audit. |
| TM-10 | Crash causes duplicate or unauthorized side effect. | Durable intent and atomic grant claim before effect; exact postcondition; `UNCERTAIN` state; digest reconciliation; no blind non-idempotent retry. | DB and filesystem are not atomically coupled; manual review may be required. |
| TM-11 | Cancellation or emergency stop loses a race with queued work/retry. | Durable stop state; revoke pending grants; checks before calls, transitions, retries, claim, and immediate commit; stale lease rejection. | An already completed atomic commit cannot be undone. |
| TM-12 | Infinite deliberation, recursion, retries, concurrent work, oversized reads, or time exhaustion. | Parent-bounded counters, two rounds, one retry, concurrency/time/byte/call/token/tool budgets, provider timeouts, leases, monotonic deadlines. | Local disk/CPU denial, clock manipulation, and an uninterruptible local filesystem syscall remain possible. |
| TM-13 | Malformed or malicious provider output exploits permissive deserialization. | Frozen Pydantic models, `extra="forbid"`, schema versions, bounded validation retry, no dynamic code/tool execution. | Parser/dependency defects remain possible. |
| TM-14 | Synthesizer erases dissent or fabricates confidence/verification. | Source proposals/critiques immutable; confidence bounded; policy independent; Verifier evidence required; event projections retain originals. | One fake provider yields correlated reasoning failures. |
| TM-15 | Memory lacks provenance, silently changes, persists after forget, or activates a self-change. | Allowed categories only; explicit acceptance; provenance required; supersession; owner-only deletion; FTS cleanup; inactive improvement state. | Backups/forensic remnants and owner mistakes remain. |
| TM-16 | Repository/iCloud contains runtime DB, credentials, or generated state, or sync changes source/PDF. | Platform-local data default; temp tests; hygiene checks; PDF hash preservation check; runtime tools cannot edit source. | Other devices or local processes can still change iCloud files. |
| TM-17 | Quiet/background behavior becomes covert or calls a provider while idle. | No daemon; explicit CLI lifecycle; zero-call fake ledger/socket guard; no lingering tasks/threads. | Future background features require a new threat review. |
| TM-18 | Local attacker replaces code, policy, binary, database, or dependencies. | Explicitly outside security boundary; clear documentation and health checks avoid false assurance. | Full compromise; stronger signing/attestation is deferred. |

## Approval and grant controls

An approval binds request ID/nonce, run, work item, requester, owner, tool/schema version, policy version, risk, canonical arguments and digest, canonical workspace/root, resolved target, exact content digest/length, no-overwrite mode, expected `absent` prior state, budget, creation/expiry times, and one-use status. The CLI redisplays that normalized action before decision.

Grant claim is an atomic state transition. A second or mismatched claimant receives no capability. Approval ID knowledge is not authority. A denial, expiry, cancellation, policy/version change, path change, content change, or exhausted budget invalidates the request and produces no tool call.

## Filesystem control details

String-prefix containment and `Path.resolve()` followed by ordinary open are insufficient. The adapter should use root-relative directory descriptors, reject each symlinked component, compare directory identity before install, and allow regular files only. The demonstration write uses a same-directory temporary file and an atomic no-overwrite install where the platform supports it; restrictive directory/file modes and `fsync` reduce crash ambiguity. Unsupported safe primitives fail closed rather than silently weakening the boundary.

Tests include absolute and parent traversal, separator/Unicode/case aliases where platform-relevant, symlinked parent/leaf, dangling links, hard-linked destination, non-regular files, missing parents, and deliberate target substitution between approval and execution.

## Secret and privacy controls

Redaction is centralized and recursive. It recognizes sensitive keys, known token/key/password patterns, denied paths, credential-shaped mappings, and credential-shaped test canaries. It runs before provider request construction, canonicalization, hashing, persistence, structured logging, error rendering, artifact creation, and CLI output. Redaction markers reveal neither the value nor its original length.

The audit journal stores content hashes/lengths and redacted summaries, not raw write bytes or full sensitive prompts. Memory content is separate from immutable event payloads so owner forgetting can remove ordinary content and FTS entries while retaining a content-free accountability tombstone.

## Recovery and fail-closed behavior

On startup, migrations are applied, the emergency-stop state and audit chain are checked, stale approvals expire, and incomplete calls are inspected without contacting a provider. Normal restarts retain durable projections; an explicit journal-only rebuild restores audit-derived state but marks approvals without exact arguments non-executable. Lease checks prevent stale workers from acting. An unreadable stop latch, inconsistent authorization record, ambiguous time, unknown schema, or postcondition mismatch blocks the run.

Fault-injection coverage is required before/after event append, projection update, grant claim, tool commit, result append, and verification. Recovery may re-run pure/idempotent phases within remaining budgets; it never repeats uncertain non-idempotent work.

## Residual-risk statement

The MVP provides a reference architecture for bounded local orchestration, not a hardened sandbox against a hostile machine owner. Its most important residuals are OS-account owner authentication, local code/database compromise, incomplete redaction, portable filesystem races, unanchored audit history, correlated fake-provider roles, storage/clock denial of service, and iCloud synchronization outside Geth’s control. These limitations must appear in owner-facing documentation and health/audit output where relevant.
