# ADR 0003: Exact, expiring, one-use action approval

- Status: Accepted for MVP

## Context

Conversational approval, role consensus, and broad session grants are vulnerable to argument substitution, replay, confused-deputy behavior, and scope expansion.

## Decision

Every material action pauses. The owner approves a displayed canonical `ActionSpec` whose digest binds run/work item, requester/owner, tool/schema/policy/risk versions, normalized arguments, root/target, content digest/length, prior state, budget, expiry, and nonce. Claim is atomic and one-use. Unsupported classes remain denied. Canonical events store only safe action metadata and digests; ordinary exact arguments live in the approval projection so a normal process restart can continue, while secret-shaped arguments are rejected before persistence.

## Consequences

Changed arguments require a new approval and concurrent redemption has one winner. Journal-only projection rebuild cannot recover executable arguments and fails closed. Approval is deliberately less convenient than a wildcard grant. The MVP trusts the invoking local OS account as owner; stronger authentication is deferred.
