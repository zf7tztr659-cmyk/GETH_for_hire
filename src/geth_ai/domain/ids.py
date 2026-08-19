"""Strongly named identifiers used at trusted domain boundaries."""

from __future__ import annotations

from typing import NewType
from uuid import UUID

PrincipalId = NewType("PrincipalId", UUID)
RunId = NewType("RunId", UUID)
WorkItemId = NewType("WorkItemId", UUID)
MessageId = NewType("MessageId", UUID)
EvidenceId = NewType("EvidenceId", UUID)
DelegationId = NewType("DelegationId", UUID)
ApprovalId = NewType("ApprovalId", UUID)
GrantId = NewType("GrantId", UUID)
ToolCallId = NewType("ToolCallId", UUID)
ArtifactId = NewType("ArtifactId", UUID)
MemoryId = NewType("MemoryId", UUID)

__all__ = [
    "ApprovalId",
    "ArtifactId",
    "DelegationId",
    "EvidenceId",
    "GrantId",
    "MemoryId",
    "MessageId",
    "PrincipalId",
    "RunId",
    "ToolCallId",
    "WorkItemId",
]
