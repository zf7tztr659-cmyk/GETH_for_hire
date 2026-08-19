from __future__ import annotations

import pytest

from geth_ai.domain import (
    ConsensusOutcome,
    DeliberationLimits,
    enforce_consensus_limits,
)


@pytest.mark.requirement("FUN-006")
def test_material_disagreement_escalates_at_round_limit() -> None:
    limits = DeliberationLimits(max_rounds=2, max_revisions=1)
    assert (
        enforce_consensus_limits(
            ConsensusOutcome.PASS,
            limits=limits,
            round_number=1,
            revisions_used=0,
            material_disagreement=True,
            remediable=True,
            policy_veto=False,
        )
        is ConsensusOutcome.REVISE
    )
    assert (
        enforce_consensus_limits(
            ConsensusOutcome.REVISE,
            limits=limits,
            round_number=2,
            revisions_used=1,
            material_disagreement=True,
            remediable=True,
            policy_veto=False,
        )
        is ConsensusOutcome.ESCALATE_TO_OWNER
    )


@pytest.mark.requirement("FUN-005")
def test_policy_veto_cannot_be_outvoted() -> None:
    assert (
        enforce_consensus_limits(
            ConsensusOutcome.PASS,
            limits=DeliberationLimits(),
            round_number=1,
            revisions_used=0,
            material_disagreement=False,
            remediable=False,
            policy_veto=True,
        )
        is ConsensusOutcome.ESCALATE_TO_OWNER
    )
