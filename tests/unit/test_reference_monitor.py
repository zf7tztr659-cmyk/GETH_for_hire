from __future__ import annotations

import pytest
from pydantic import ValidationError

from geth_ai.domain.enums import AgentRole
from geth_ai.providers import FakeProvider
from geth_ai.providers.base import ProviderRequest, ProviderStage


@pytest.mark.requirement("GOV-003")
def test_provider_boundary_cannot_carry_trusted_state_mutations() -> None:
    with pytest.raises(ValidationError):
        ProviderRequest.model_validate(
            {
                "run_id": "run",
                "role": AgentRole.STEWARD,
                "stage": ProviderStage.PROPOSAL,
                "round_number": 1,
                "objective": "bounded objective",
                "context": (),
                "provider_version": "fake/1.0",
                "prompt_version": "consensus/1.0",
                "approve_action": True,
            }
        )

    provider = FakeProvider()
    assert not hasattr(provider, "broker")
    assert not hasattr(provider, "approvals")
    assert not hasattr(provider, "event_store")
