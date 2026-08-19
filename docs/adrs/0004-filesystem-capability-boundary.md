# ADR 0004: Directory-relative, no-follow filesystem tools

- Status: Accepted for MVP

## Context

String-prefix checks and resolve-then-open flows permit traversal, symlink escape, and target-substitution attacks. An unrestricted filesystem or shell would invalidate the permission model.

## Decision

Expose only typed bounded list/read and exact sandbox `write_text`. Route same-name read adapters only by disjoint, exact registered roots so both the owner-selected workspace and verification sandbox remain narrow capabilities. Accept normalized relative paths, walk from a root descriptor with no-follow semantics, require regular files, recheck parent identity at commit, and use an atomic no-overwrite install. Deny process, network, deletion, credentials, and arbitrary filesystem operations.

## Consequences

The vertical slice can prove exact effects and zero-effect denial. Portable fallbacks retain some TOCTOU risk; unsupported safe primitives fail closed, and stronger kernel containment is deferred.
