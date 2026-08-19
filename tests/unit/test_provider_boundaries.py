from __future__ import annotations

import asyncio

import pytest

from geth_ai.application.consensus import ConsensusCoordinator
from geth_ai.domain.models import BudgetLimits
from geth_ai.providers import FakeProvider, FakeScenario


@pytest.mark.requirement("SAF-011")
@pytest.mark.requirement("OPS-001")
def test_stop_boundary_runs_before_every_provider_attempt_and_retry() -> None:
    provider = FakeProvider(FakeScenario.RETRYABLE_FAILURE)
    checked: list[str] = []
    coordinator = ConsensusCoordinator(
        provider,
        budget=BudgetLimits(max_provider_calls=12, max_tokens=32_000, max_retries=1),
        boundary_check=checked.append,
    )

    asyncio.run(coordinator.deliberate(run_id="run", objective="bounded objective"))

    assert checked == ["run"] * len(provider.ledger)
    assert len(checked) > 4


@pytest.mark.requirement("SAF-011")
def test_stop_boundary_prevents_provider_call() -> None:
    provider = FakeProvider()

    def stopped(_run_id: str) -> None:
        raise RuntimeError("cancelled")

    coordinator = ConsensusCoordinator(provider, boundary_check=stopped)

    with pytest.raises(RuntimeError, match="cancelled"):
        asyncio.run(
            coordinator.deliberate(run_id="run", objective="bounded objective")
        )
    assert provider.ledger == ()
