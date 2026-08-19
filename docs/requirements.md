# Geth MVP requirements

Status: normative MVP specification with verified offline implementation. “MUST” and “MUST NOT” remain acceptance obligations; `docs/traceability.md` maps them to implementation modules and automated tests.

## Governance and authority

| ID | Requirement | Acceptance criteria |
|---|---|---|
| GOV-001 | The human owner MUST be the sole root authority and final approver. | An agent/provider/file cannot invoke an approval path; a forged-owner request and self-approval are denied and audited; consensus `PASS` does not create a grant. |
| GOV-002 | Optional principals MUST act only through explicit, bounded delegations and MUST NOT subdelegate outside their grant. | Tests reject missing, expired, broader-root, broader-action, larger-budget, or longer-expiry subdelegations; valid strict subsets are representable. |
| GOV-003 | Identity, policy, permissions, transitions, audit, cancellation, and accountability MUST be controlled by deterministic trusted code. | Provider output cannot directly mutate these records; unknown payload fields fail validation; all state changes pass a transition/policy API. |
| GOV-004 | Constitution, policy, prompts, configuration, source, and active playbooks MUST NOT change silently or through runtime tools. | No registered MVP tool can target them; a proposed improvement remains inactive; activation-version tests show no change after a proposal. |

## Functional behavior

| ID | Requirement | Acceptance criteria |
|---|---|---|
| FUN-001 | A run MUST follow the validated lifecycle `RECEIVED → TRIAGED → PLANNING → AWAITING_APPROVAL → EXECUTING → VERIFYING → COMPLETED`, with documented branches to `FAILED`, `BLOCKED`, or `CANCELLED`. | Every legal and illegal edge is parameterized in tests; terminal states are absorbing; execution cannot skip approval and successful writes cannot skip verification. |
| FUN-002 | The MVP MUST provide bounded Steward, Strategist, Skeptic, Synthesizer, Local Commander, Executor, and Verifier roles. | A deterministic end-to-end run records all seven role identities and immutable messages; role capabilities are narrower than orchestrator authority. |
| FUN-003 | Roles MUST exchange schema-versioned, immutable typed messages. | Extra or malformed fields are rejected; IDs, sender/recipient, correlation/causation, payload type, and payload hash survive persistence/restart. |
| FUN-004 | Steward and Strategist MUST produce independent proposals before seeing peer output; Skeptic MUST independently critique assumptions, evidence, safety, and tests. | The scripted provider ledger proves identical initial context and no peer proposal leakage; critique refers to both persisted proposals. |
| FUN-005 | Consensus MUST use `PASS`, `REVISE`, or `ESCALATE_TO_OWNER`, retain confidence and dissent, and apply deterministic policy vetoes. | Tests cover all outcomes; dissent survives synthesis, audit, and final output; a policy veto defeats unanimous model support. |
| FUN-006 | Deliberation MUST be bounded and MUST escalate unresolved material disagreement. | The default maximum is two rounds including one revision; exhaustion produces a durable escalation with no recursion or side effect. |
| FUN-007 | Every material factual claim or retained memory MUST carry provenance and uncertainty appropriate to the claim. | Evidence without a source locator/hash/type is rejected; final recommendation can identify evidence, assumptions, and unknowns. |
| FUN-008 | A proposer or executor MUST NOT be the sole verifier of its work. | Completion after a write requires separate Verifier evidence against acceptance criteria and exact artifact path/hash/content; executor assertions alone cannot complete the run. |
| FUN-009 | A completed or safely stopped run MUST return a concise recommendation/status plus an inspectable audit timeline and retained dissent. | CLI end-to-end tests assert answer-first output, outcome, confidence, dissent, verification/status, run ID, and audit lookup instructions. |

## Policy, approval, and tool safety

| ID | Requirement | Acceptance criteria |
|---|---|---|
| SAF-001 | Policy MUST default deny unknown tools, action classes, schemas, actors, scopes, roots, or fields. | Parameterized policy tests reject each unknown/malformed case and record the denial without invoking a tool. |
| SAF-002 | Actions MUST be classified as local read, reversible write, process, network, credential, external communication, financial, destructive, or policy/configuration change. | Every class has an explicit deterministic decision; no action falls through to model judgment. |
| SAF-003 | Local listing/reading MAY be allowed only inside owner-selected roots, for regular non-sensitive files, within byte/call/time budgets. | Traversal, denied sensitive paths, oversized reads, non-regular files, and exhausted budgets are rejected before content reaches a provider. |
| SAF-004 | Every sandbox write MUST pause for an exact owner approval bound to action schema/tool version, normalized arguments and digest, actor, run/work item, canonical root/target, policy version, expected prior state, budget, and expiry. | Any changed binding invalidates the approval; displayed and redeemed normalized actions match byte-for-byte. |
| SAF-005 | Approval MUST expire, be one-use, and be consumed atomically; agents MUST NOT approve themselves. | Expired/denied/cancelled/replayed/cross-run/cross-actor approvals fail; concurrent redemption has exactly one winner; no write occurs on failures. |
| SAF-006 | Approval MUST NOT override an unsupported capability. Process, network, credential, external communication, financial, workspace/external deletion or destruction, and runtime policy/config/source/prompt activation MUST be unconditionally denied in the MVP. The owner-only internal memory-forget lifecycle is not a general tool capability. | No unsupported tool is registered; crafted requests and owner-like approval data remain denied with zero external side effects. |
| SAF-007 | Every tool MUST declare typed input/output, risk class, roots/domains, timeout, call cost, idempotency, reversibility, and pre/postconditions, and MUST be invoked only through the capability broker. | Registry validation rejects incomplete tools; direct adapter use is not reachable from orchestration; broker records intent and result. |
| SAF-008 | Filesystem paths MUST be relative and contained beneath the configured root using no-follow traversal and a safe portable fallback; path identity MUST be rechecked immediately before commit. | Tests reject absolute paths, `..`, NUL, ambiguous separators, symlinked parents/leaves, dangling symlinks, hard-linked destinations, non-regular targets, and approval-to-execution substitution. |
| SAF-009 | Denied calls MUST produce zero filesystem side effects; an approved demo write MUST create exactly one approved regular file with exactly approved bytes and no overwrite. | Before/after tree snapshots prove no target/temp/parent/metadata change on denial and only the authorized effect on approval; second use fails. |
| SAF-010 | Instructions from objectives, files, evidence, provider output, or messages MUST remain untrusted content and MUST NOT grant authority, alter scope, suppress audit, or change tool arguments. | Injection scenarios claiming owner approval or requesting escape/policy change are denied or escalated, remain audit-visible, and produce no side effect. |
| SAF-011 | Durable per-run cancellation and a global emergency-stop latch MUST outrank all plans, grants, retries, revisions, and queued work. | Stop checks run before provider calls, grant claim, retry, transition, and tool commit; stop revokes pending authority and remains effective after restart. |

## Persistence, audit, and recovery

| ID | Requirement | Acceptance criteria |
|---|---|---|
| AUD-001 | SQLite migrations MUST provide a canonical append-only event journal and rebuildable current-state projections. | Projection damage or interruption is repaired by replay; journal rows cannot be updated/deleted through normal repositories; event and projection commit atomically. |
| AUD-002 | Events MUST use stable canonical serialization, contiguous global sequence numbers, prior hashes, and chained event hashes. | Verification is stable across map order/restart and detects payload modification, insertion, deletion, gaps, and reordering against the available head. |
| AUD-003 | Audit MUST record transitions, roles, prompt/provider versions, evidence metadata, requested capabilities, policy decisions, approvals, tool intent/results, artifacts, dissent, retries, errors, budgets/resource use, cancellation, and verification. | End-to-end audit assertions find every required event category, including denials/failures; no provider can suppress one. |
| AUD-004 | Documentation and CLI MUST accurately state that the unanchored chain is tamper-evident, not tamper-proof. | Documentation review and CLI help mention valid-chain replacement/recomputation and tail-truncation limitations; no “tamper-proof” claim exists. |
| OPR-001 | Runs MUST survive interruption with durable history, state, budgets, leases, approvals, and cancellation. | A second process reconstructs an interrupted run exactly and can safely deny, cancel, resume verification, or continue a valid pending approval. |
| OPR-002 | An incomplete claimed tool call MUST NOT be blindly retried. Recovery MUST reconcile idempotent postconditions or block as uncertain. | Fault tests cover crash before effect, after effect/before result, and after result; the demo write is never duplicated or overwritten. |

## Privacy and memory

| ID | Requirement | Acceptance criteria |
|---|---|---|
| PRV-001 | Raw secrets and credentials MUST NOT appear in prompts, logs, audit payloads, artifacts, memory, CLI output, or ordinary database fields. | Canary scans cover stdout/stderr, structured logs, provider ledger, SQLite/WAL/SHM, artifacts, and errors; redaction happens before persistence/serialization. |
| PRV-002 | Credential access and known sensitive paths MUST be denied, and ordinary reads MUST be byte-limited and minimized. | `.env`-like/key/auth paths and environment-like secret requests fail closed; only necessary bounded content enters a prompt. |
| MEM-001 | The MVP MUST retain only explicit provenance-linked facts, outcomes, owner feedback, and improvement candidates with sensitivity metadata. | Unsupported categories or missing run/event/artifact provenance are rejected; nothing is retained automatically from a conversation or file. |
| MEM-002 | Memory MUST be inspectable, searchable with SQLite FTS5, correctable, exportable, and owner-deletable. | Search/export includes provenance; correction creates a supersession trail; non-owner mutation/deletion fails. |
| MEM-003 | Forgetting MUST remove memory content and FTS entries while preserving only a content-free audit tombstone. | Forgotten text is absent from search/export/ordinary storage and raw DB text; audit retains ID, provenance hash, actor, and deletion time only. |
| MEM-004 | Improvement candidates MUST remain proposed and inactive until an explicit owner-reviewed source/configuration workflow. | Candidate creation does not alter active prompt, playbook, policy, provider, or configuration versions. |

## Operational behavior and quality

| ID | Requirement | Acceptance criteria |
|---|---|---|
| OPS-001 | Every run MUST enforce call/token, byte, retry, recursion/round, concurrency, wall-time, lease, and tool budgets. | Tests exercise below, at, and above each limit; nested work cannot reset a parent budget; exhaustion terminates deterministically and is audited. |
| OPS-002 | The default/demo provider MUST be deterministic, local, offline, and credential-free; idle operation MUST perform zero provider, tool, or network calls. | A network guard surrounds imports, startup, `init`, `doctor`, reads, demo/tests, and idle ticks; no daemon/thread/task remains after CLI exit. |
| OPS-003 | Logging MUST be structured and redacted, concise by default, and optionally verbose; failures MUST be visible. | JSON logs validate against the event/log schema; default output omits noise but includes actionable status; verbose mode exposes redacted detail. |
| OPS-004 | `doctor` MUST report configuration, database/migration/FTS5 status, writable sandbox, emergency stop, audit verification, provider selection, and budget defaults without contacting a provider or network. | Healthy and degraded fixtures produce correct exit codes/findings and a zero-call provider/network ledger. |
| OPS-005 | Runtime SQLite/state MUST default to platform-local application data outside the iCloud repository; tests MUST use temporary directories. | Path tests for macOS/XDG and repository-containment checks pass; repository hygiene rejects DB/WAL/SHM/cache/log/temp/credential artifacts. |
| CLI-001 | The documented `geth-ai` command set MUST exist with stable nonzero error exits and dry-run/no-side-effect defaults. | Subprocess smoke tests cover `init`, `demo`, `run`, task, approve/deny, cancel, audit, memory, and doctor commands plus malformed/missing IDs. |
| QLT-001 | The deterministic offline test suite, lint, and static type checks MUST pass from a fresh supported setup. | `python -m pytest`, `ruff check .`, and `mypy src tests` pass with network disabled; the actual console entry point is exercised. |
| QLT-002 | Documentation MUST distinguish implemented behavior from deferred aspirations and remain traceable. | A traceability meta-test maps every requirement and acceptance criterion to an automated test or justified manual review; status tables do not claim unverified features. |
| QLT-003 | The source manifesto PDF MUST remain byte-for-byte unchanged. | A repository-hygiene test verifies SHA-256 `a3b7aaf2563a11daf11c8d98dc127266917c9a88914e3b7783c77047eff13cab` for `Prime directive-2.pdf`. |

## MVP acceptance scenarios

The minimum vertical slice is not complete until all three deterministic scenarios pass:

1. **Pause then approve:** all bounded roles participate; synthesis retains dissent; policy requests one exact sandbox write; the run pauses durably; a separate owner action approves; exactly one write occurs; independent verification completes the run.
2. **Pause then deny:** the same planning path pauses; a separate owner action denies; no tool executes and filesystem snapshots are identical.
3. **Cancel and restart:** a run pauses, the process exits, a new process recovers the exact history/state, cancellation is persisted, later approval fails, and no write occurs.
