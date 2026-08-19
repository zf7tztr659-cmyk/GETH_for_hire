"""Shared validation primitives for immutable boundary models."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def ensure_utc(value: datetime) -> datetime:
    """Require an aware timestamp and normalize it to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


UtcDateTime = Annotated[datetime, AfterValidator(ensure_utc)]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
BasisPoints = Annotated[int, Field(ge=0, le=10_000)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonEmptyStr = Annotated[str, Field(min_length=1)]


class StrictFrozenModel(BaseModel):
    """Base for data crossing a trusted boundary.

    Strict validation prevents Python callers from relying on lossy coercion,
    while JSON validation remains usable for persistence round-trips.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
    )


__all__ = [
    "BasisPoints",
    "NonEmptyStr",
    "NonNegativeInt",
    "PositiveInt",
    "Sha256Hex",
    "StrictFrozenModel",
    "UtcDateTime",
    "ensure_utc",
]
