"""Drift checks: a read-only `terraform plan -refresh-only` of a ready resource.

A check is an ordinary operation (action `drift_check`) accepted through the same ledger path
as every other, so the one-operation-per-resource busy rule, idempotency and audit apply. It
never applies, reserves no budget and leaves the resource's cost, state, managed objects and
version alone; only the resource's `drift_status`, `drift_checked_at` and `drift` change.

The optional sweep (`FORGEAPI_DRIFT_SWEEP_MINUTES`) accepts checks as the system actor
`system:drift-sweep`. That actor has no caller identity or groups, so it cannot go through
`policy.authorize`; instead it reuses the placement the resource was last applied with, and
only for a resource whose business unit and environment are still in the tenant mapping. Its
operations carry the resource's business unit, so under tenancy the unit's members can read
them (with `action=drift_check`); without tenancy only the system actor owns them, and the
verdict is read from the resource. Audit events name the system actor."""

import time
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from temporalio import activity

from app import ledger, policy, tenants
from app.contracts import OperationError
from app.settings import settings
from app.tenants import Caller, TenancyError

SYSTEM_ACTOR = "system:drift-sweep"
BATCH = 20  # resources checked per sweep cycle
BACKLOG_SLEEP = 60  # seconds between cycles while more resources are due than one batch holds

# What a check needs to run Terraform exactly as the last successful apply did. Deliberately
# not `labels`, `input_refs` or cost: a check changes none of them and holds no reservation.
_FIELDS = (
    "pattern", "version", "commit", "source", "inputs", "injected", "cloud", "business_unit",
    "environment", "subscription_id", "cloud_target", "size",
)  # fmt: skip


def intent(resource_id: str) -> dict:
    """The canonical request: its fingerprint is what an idempotency key binds to."""
    return {"action": "drift_check", "resource_id": resource_id}


def resolved(resource: dict) -> dict:
    return {
        **{key: resource[key] for key in _FIELDS if key in resource},
        "previous_operation_id": resource["operation_id"],
    }


def accept(resource_id: str, caller: Caller, key: str) -> dict:
    """A caller's check of a resource they may change. 404 when they cannot see it, 403 when
    they may not deploy there (a check runs Terraform with the zone's placement), 409 when it is
    not ready or another operation is in flight."""
    material = intent(resource_id)
    if existing := ledger.replay(caller.id, key, material):
        return policy.authorize(existing, caller)
    resource = policy.authorize(ledger.resource(resource_id), caller, change=True)
    return ledger.accept(caller.id, key, material, resolved(resource), None)


def accept_system(resource: dict, key: str) -> dict:
    if tenants.enabled():
        unit = tenants.load().get(resource.get("business_unit"))
        if unit is None or resource.get("environment") not in unit.environments:
            raise TenancyError(403, "resource's business unit or environment is not configured")
    return ledger.accept(SYSTEM_ACTOR, key, intent(resource["id"]), resolved(resource), None)


@activity.defn
def sweep_drift() -> dict:
    """One sweep cycle: accept a check for each idle ready resource not checked within the
    interval (bounded batch) and return the operations still to plan, plus how long to sleep.
    Idempotent per resource and interval, so a Temporal retry accepts nothing twice. Reads the
    setting each cycle, so turning the sweep off ends the workflow at its next cycle."""
    minutes = settings.drift_sweep_minutes or 0
    if not minutes:
        return {"enabled": False, "operations": [], "sleep_seconds": 0.0}
    interval = minutes * 60
    cutoff = (datetime.now(UTC) - timedelta(seconds=interval)).isoformat()
    # Checks still waiting or planning count against the batch: a slow plan phase must not let
    # the backlog (and the resources it keeps busy) grow every cycle.
    outstanding = ledger.outstanding_count(SYSTEM_ACTOR, "drift_check")
    allowed = max(0, BATCH - outstanding)
    candidates = ledger.drift_candidates(cutoff, allowed) if allowed else []
    bucket = int(time.time() // interval)
    for resource in candidates:
        try:
            accept_system(resource, f"drift-sweep:{resource['id']}:{bucket}")
        except (OperationError, HTTPException, TenancyError) as error:
            # Lost a race to a user's operation, or no longer placeable. Stamp the resource so
            # it is not retried (and not audited again) until the next interval.
            ledger.refused(SYSTEM_ACTOR, "drift_check")
            ledger.stamp_drift_refusal(
                resource["id"],
                "resource is busy or not placeable"
                if isinstance(error, OperationError)
                else "resource's placement is no longer configured",
                unknown=not isinstance(error, OperationError),
            )
    # Includes checks accepted by a previous attempt that died before they were planned.
    queued = ledger.queued_operations(SYSTEM_ACTOR, "drift_check")
    backlog = outstanding >= BATCH or len(candidates) == allowed
    return {
        "enabled": True,
        "operations": queued,
        "sleep_seconds": float(min(interval, BACKLOG_SLEEP) if backlog else interval),
    }
