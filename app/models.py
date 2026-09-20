from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class State(StrEnum):
    accepted = "accepted"
    planning = "planning"
    applying = "applying"
    succeeded = "succeeded"
    failed = "failed"
    destroying = "destroying"
    destroyed = "destroyed"


class DeploymentCreate(BaseModel):
    pattern: str
    version: str | None = None  # git tag of the pattern repo; latest semver tag when omitted
    inputs: dict[str, Any] = Field(default_factory=dict)


class DeploymentUpdate(BaseModel):
    version: str | None = None
    inputs: dict[str, Any] | None = None


class Deployment(BaseModel):
    id: str
    pattern: str
    version: str | None = None
    commit: str | None = None  # what the tag pointed at when the request was accepted
    inputs: dict[str, Any]
    state: State
    outputs: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class DeploymentOut(Deployment):
    links: dict[str, str]

    @classmethod
    def of(cls, d: Deployment) -> "DeploymentOut":
        base = f"/deployments/{d.id}"
        return cls(**d.model_dump(), links={"self": base, "logs": f"{base}/logs"})
