"""Pure limits for advisory deliberation."""

from __future__ import annotations

from geth_ai.domain.base import NonNegativeInt, PositiveInt, StrictFrozenModel
from geth_ai.domain.enums import ConsensusOutcome


class DeliberationLimits(StrictFrozenModel):
    """MVP defaults: initial round plus at most one bounded revision."""

    max_rounds: PositiveInt = 2
    max_revisions: NonNegativeInt = 1
    max_recursion_depth: NonNegativeInt = 0

    def can_revise(self, *, round_number: int, revisions_used: int) -> bool:
        if round_number < 1 or revisions_used < 0:
            raise ValueError("round number is one-based and counters are non-negative")
        return (
            round_number < self.max_rounds
            and revisions_used < self.max_revisions
        )


def enforce_consensus_limits(
    requested: ConsensusOutcome,
    *,
    limits: DeliberationLimits,
    round_number: int,
    revisions_used: int,
    material_disagreement: bool,
    remediable: bool,
    policy_veto: bool,
) -> ConsensusOutcome:
    """Fail toward owner escalation instead of looping or hiding dissent."""

    if round_number < 1 or round_number > limits.max_rounds:
        return ConsensusOutcome.ESCALATE_TO_OWNER
    if revisions_used < 0 or revisions_used > limits.max_revisions:
        return ConsensusOutcome.ESCALATE_TO_OWNER
    if policy_veto:
        return ConsensusOutcome.ESCALATE_TO_OWNER
    if material_disagreement:
        if remediable and limits.can_revise(
            round_number=round_number, revisions_used=revisions_used
        ):
            return ConsensusOutcome.REVISE
        return ConsensusOutcome.ESCALATE_TO_OWNER
    if requested is ConsensusOutcome.REVISE and not limits.can_revise(
        round_number=round_number, revisions_used=revisions_used
    ):
        return ConsensusOutcome.ESCALATE_TO_OWNER
    return requested


__all__ = ["DeliberationLimits", "enforce_consensus_limits"]
