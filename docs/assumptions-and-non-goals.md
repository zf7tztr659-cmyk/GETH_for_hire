# Assumptions, defaults, limitations, and non-goals

Status: accepted decisions for the implemented conservative MVP. These defaults may be changed only through owner-reviewed source/configuration changes with updated tests and traceability.

## Conservative interpretations of ambiguous language

| Source phrase or ambiguity | MVP interpretation |
|---|---|
| “Me” | The human owner, never Geth or an agent. |
| “Partners” | Optional principals with explicit subset delegations; no production partner accounts are implemented. |
| “Multiple minds” | Distinct bounded role calls with typed messages, not consciousness or independent authority. |
| “Knowledgeable, in depth and wide” | Provenance, explicit uncertainty, and bounded evidence collection; never fabricated knowledge. |
| “Top and bottom” | Owner objective/policy flows downward; observed evidence and independent verification flow upward. |
| “Consensus” | Proposal, critique, policy evaluation, synthesis, confidence, and retained dissent; advisory only. |
| “Quiet” | Concise default output and no idle activity, not hidden work or suppressed failures. |
| “Secretive” | Private from unnecessary disclosure, fully visible to the owner, and never covert. |
| “Watchful” | Monitor Geth’s health, resources, leases, and compliance only; no surveillance. |
| “Cold, efficient, indifferent” | Calm and impartial, while respecting human welfare, rights, consent, safety, and preferences. |
| “Exceed expectations” | Better scoped reasoning and verification, not additional permissions or side effects. |
| “Improving over time” | Inspectable memory, evaluations, and inactive versioned proposals requiring owner review. |
| “Background” | No daemon in the MVP. Any future background mode must be explicit, bounded, visible, and cancellable. |

## Authority and trust assumptions

- The local OS account launching `geth-ai` is treated as the owner for the single-user MVP. There is no cryptographic owner login, hardware key, or multi-user authorization service.
- Launching an explicit command implicitly authorizes Geth’s internal platform-local database/audit writes needed to record that command. It does not authorize workspace writes.
- Agents, providers, objectives, messages, and file contents are untrusted. The orchestrator, state machine, policy engine, capability broker, approval service, and event writer form the trusted reference monitor.
- The deterministic fake provider creates independent role contexts, not independent security principals or statistically independent judgments.
- Optional principal/delegation types are modeled and tested, but the MVP CLI exposes owner operation only.
- Consensus never substitutes for authority. An unsupported capability remains denied even if the owner attempts to approve it through crafted data.

## Operational defaults

| Setting | Default | Rationale |
|---|---:|---|
| Provider | deterministic `fake` | Offline and reproducible |
| Consensus rounds | 2 total | One proposal round plus at most one revision |
| Provider calls | 12 per run | Covers seven roles and one bounded revision |
| Tool calls | 3 per run | Minimal vertical slice |
| Retry count | 1 per retryable phase | Prevents loops; validation/policy failures are not retryable |
| Concurrent role calls | 3 | Bounded fan-out |
| Provider timeout | 10 seconds | Fast local failure |
| Tool timeout | 5 seconds | Narrow local tools only |
| Run/worker lease | 60 seconds | Allows restart recovery without long stale authority |
| Approval expiry | 10 minutes | Short-lived human decision |
| Evidence read | 1 MiB per file, 4 MiB per run | Minimizes disclosure and resource use |
| Token-accounting budget | 32,000 deterministic units per run | Testable provider-neutral bound |
| Sandbox write | one new regular file, no overwrite | Reversible, reconcilable demonstration effect |

Deadlines use UTC for durable timestamps and a monotonic clock while a process is alive. Ambiguous rollback, unreadable stop state, stale leases, or inconsistent projections fail closed.

## Storage defaults

- Application data uses the platform-local per-user data directory, conceptually `user_data_dir("geth-ai")`, not the iCloud-backed repository. Tests inject temporary data and sandbox roots.
- The owner explicitly selects a workspace read root. Read capability does not arise from provider text.
- Workspace reads are limited to regular, bounded, non-sensitive files. Credential-like paths, environment files, keys, authentication stores, and VCS-private metadata are denied.
- The dedicated sandbox is application-created with owner-only permissions where supported. Its root is separate from source/configuration/policy targets.
- Memory uses SQLite plus FTS5. No vector store or embedding call exists.

## Known MVP limitations

- A compromised OS account, process, dependency, executable, or database administrator can bypass or rewrite local controls.
- The audit hash chain has no external anchor. A capable local attacker can recompute/replace it, and tail truncation cannot be reliably detected without a separately retained head hash/count.
- Redaction and sensitive-path detection reduce exposure but cannot guarantee discovery of every secret.
- Portable no-follow path traversal narrows symlink and time-of-check/time-of-use attacks but cannot match every platform-specific kernel containment primitive.
- Provider calls have enforced async timeouts and active work is lease/wall bounded at trusted boundaries. The synchronous local filesystem syscalls are deliberately tiny and byte-bounded but cannot be safely preempted mid-syscall by Python; OS-level hard execution deadlines are deferred.
- Filesystem effects and SQLite commits are not one atomic transaction. A crash after grant claim can leave a tool call `UNCERTAIN`; recovery observes the approved postcondition and never blindly repeats it.
- Exact approval arguments are deliberately absent from canonical audit history. Normal restarts keep them in the live projection, but journal-only projection rebuild makes the request non-executable and requires a fresh owner approval.
- Cancellation can prevent new effects after a durable check, but it cannot undo an atomic filesystem commit that has already completed.
- A single fake provider across roles creates correlated failure and does not prove the quality of a future live provider.
- Local denial of service, disk exhaustion, clock manipulation, hardware failure, and malicious iCloud synchronization remain possible.
- Owner-approved memory forgetting removes ordinary content and search entries, but backups or storage-level forensic remnants are outside the MVP’s guarantees.

## Explicit MVP non-goals

- AGI, consciousness, sentience, infallibility, sovereignty, self-preservation, or moral personhood.
- Hidden processes, covert persistence, surveillance, undisclosed collection, or an always-on daemon.
- Autonomous self-modification, self-replication, policy weakening, or automatic activation of improvement proposals.
- Unrestricted shell, filesystem, process, network, credential, messaging, purchasing, publication, deletion, or destructive tools.
- Live OpenAI or other hosted providers, mandatory cloud services, API keys, or network-dependent tests/demos.
- Production partner accounts, multi-tenancy, distributed agents, distributed consensus, or cross-device coordination.
- GUI, web app, mobile app, browser automation, notifications, or background scheduling.
- Vector databases, embeddings, knowledge graphs, or automated long-term memory extraction.
- Cryptographic remote attestation, external audit anchoring, tamper-proof storage, or defense against local administrator compromise.
- General-purpose code execution or arbitrary workspace editing.

## Deferred work, ordered by value then risk

1. External audit-head export/anchoring and owner checkpoint comparison.
2. Explicit owner continuation for a crash-reconciled run left safely in `VERIFYING`.
3. Stronger local owner authentication and explicit partner/delegation workflows.
4. Platform-specific kernel-enforced path containment and syscall deadlines after portable behavior is proven.
5. A live-provider adapter with consent, cost, privacy, and network policy controls; never enabled by default.
6. Additional reversible tools, one at a time, each with a narrow schema and adversarial tests.
7. Reviewed prompt/playbook evaluation and activation workflow.
8. Explicit opt-in bounded background jobs with visible leases and cancellation; no hidden service.
9. Multi-provider diversity for correlated-failure reduction.

Each deferred item requires a new threat review and owner decision. None is part of the MVP merely because an interface anticipates it.
