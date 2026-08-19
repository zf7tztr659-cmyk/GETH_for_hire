# ADR 0006: Deletable memory content with content-free audit tombstones

- Status: Accepted for MVP

## Context

Owner-controlled forgetting conflicts with copying memory content into an immutable journal. Automatic semantic memory also increases privacy and provenance risk.

## Decision

Store only explicit allowed, provenance-linked memory in ordinary SQLite rows plus FTS5. Audit hashes/provenance/status but not raw memory content. Correction supersedes; owner forgetting removes content and search entries and appends a content-free tombstone. Improvement candidates remain inactive.

## Consequences

Ordinary memory is inspectable, exportable, correctable, and deletable without rewriting canonical history. Backups and forensic remnants remain outside the guarantee; vector storage and automatic retention are deferred.
