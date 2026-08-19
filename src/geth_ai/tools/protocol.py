"""Typed contracts for the deliberately small MVP tool surface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from geth_ai.domain.enums import RiskClass


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ListDirectoryInput(ToolModel):
    path: str = "."


class FileEntry(ToolModel):
    path: str
    kind: str
    byte_length: int = Field(ge=0)


class ListDirectoryOutput(ToolModel):
    entries: tuple[FileEntry, ...]


class ReadFileInput(ToolModel):
    path: str
    max_bytes: int = Field(gt=0)


class ReadFileOutput(ToolModel):
    path: str
    content: str
    byte_length: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WriteTextInput(ToolModel):
    path: str
    content: str


class WriteTextOutput(ToolModel):
    path: str
    byte_length: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created: bool = True


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    schema_version: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    risk_class: RiskClass
    allowed_roots: tuple[str, ...]
    timeout_seconds: int
    call_cost: int
    idempotent: bool
    reversible: bool
    description: str

    def validate(self) -> None:
        if not self.name or not self.schema_version or not self.description:
            raise ValueError("tool metadata cannot be empty")
        if not issubclass(self.input_model, BaseModel) or not issubclass(
            self.output_model, BaseModel
        ):
            raise TypeError("tool schemas must be Pydantic models")
        if not self.allowed_roots:
            raise ValueError("tool must declare at least one allowed root")
        if self.timeout_seconds <= 0 or self.call_cost <= 0:
            raise ValueError("tool timeout and call cost must be positive")


type PrecommitCheck = Callable[[], None]


class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    def execute(
        self, value: BaseModel, *, precommit_check: PrecommitCheck | None = None
    ) -> BaseModel: ...


@dataclass(frozen=True, slots=True)
class BrokerResult:
    output: BaseModel
    policy_outcome: str
    action_digest: str
    call_id: str | None
