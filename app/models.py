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
    # With business units configured (docs/tenancy.md):
    business_unit: str | None = None  # only needed when you belong to more than one
    environment: str | None = None
    size: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)


class DeploymentUpdate(BaseModel):
    version: str | None = None
    size: str | None = None
    inputs: dict[str, Any] | None = None


class Deployment(BaseModel):
    id: str
    pattern: str
    version: str | None = None
    commit: str | None = None  # what the tag pointed at when the request was accepted
    inputs: dict[str, Any]
    # Placement, set by the platform (None when business units are not configured).
    business_unit: str | None = None
    environment: str | None = None
    subscription_id: str | None = None
    size: str | None = None
    injected: dict[str, Any] | None = None  # values the platform supplied to the pattern
    estimated_monthly_cost: float | None = None  # the pattern's own estimate, at acceptance
    requested_by: str | None = None
    state: State
    outputs: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class DeploymentOut(Deployment):
    links: dict[str, str]
    # Callers never need the subscription ID; placement is the platform's business.
    subscription_id: str | None = Field(default=None, exclude=True)

    @classmethod
    def of(cls, d: Deployment) -> "DeploymentOut":
        base = f"/deployments/{d.id}"
        return cls(**d.model_dump(), links={"self": base, "logs": f"{base}/logs"})
