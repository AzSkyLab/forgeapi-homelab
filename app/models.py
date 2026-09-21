import re
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
    # Names of outputs the pattern marks sensitive. Their values are never stored or returned.
    withheld_outputs: list[str] | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


_SECRET_URL = re.compile(
    r"^https://[a-z0-9-]+\.vault\.azure\.net/secrets/[A-Za-z0-9-]+(/[0-9a-f]+)?/?$"
)


def secret_references(outputs: Any, path: str = "") -> dict[str, str]:
    """Key Vault secret IDs found anywhere in the outputs, keyed by where they were found.
    These are pointers, not secrets: reading one needs the caller's own access to that vault."""
    found: dict[str, str] = {}
    if isinstance(outputs, str) and _SECRET_URL.match(outputs):
        found[path] = outputs
    elif isinstance(outputs, dict):
        for key, value in outputs.items():
            found |= secret_references(value, f"{path}.{key}" if path else str(key))
    elif isinstance(outputs, list):
        for index, value in enumerate(outputs):
            found |= secret_references(value, f"{path}[{index}]")
    return found


class DeploymentOut(Deployment):
    links: dict[str, str]
    # Convenience: every Key Vault secret reference in `outputs`. Read them with your own
    # identity, e.g. `az keyvault secret show --id <reference>`.
    secret_references: dict[str, str] = {}
    # Callers never need the subscription ID; placement is the platform's business.
    subscription_id: str | None = Field(default=None, exclude=True)

    @classmethod
    def of(cls, d: Deployment) -> "DeploymentOut":
        base = f"/deployments/{d.id}"
        return cls(
            **d.model_dump(),
            links={"self": base, "logs": f"{base}/logs", "events": f"{base}/events"},
            secret_references=secret_references(d.outputs or {}),
        )
