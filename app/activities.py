"""Temporal activities. All I/O (database, git, Terraform) lives here, never in the workflow."""

from temporalio import activity

from app import audit, catalog, db, recovery, terraform
from app.models import State


def _load(deployment_id: str):
    deployment = db.get(deployment_id)
    if deployment is None:
        # The API wrote a record this worker cannot see: the two are not sharing a record store
        # (different FORGEAPI_DB_BACKEND / table settings, or two separate SQLite files).
        raise RuntimeError(
            f"deployment {deployment_id} is not in this worker's record store; the API and the "
            "worker must use the same FORGEAPI_DB_BACKEND and storage settings"
        )
    return deployment


def _source(deployment) -> str:
    # Pinned to the commit resolved when the request was accepted, not to the (movable) tag.
    pattern = catalog.get(deployment.pattern)
    return catalog.Resolved(pattern, deployment.version, deployment.commit).terraform_source


def _outcome(deployment, outcome: str, **detail) -> None:
    audit.record_quietly(
        "deployment.state", outcome, "worker",
        deployment_id=deployment.id, pattern=deployment.pattern, version=deployment.version,
        business_unit=deployment.business_unit, environment=deployment.environment, detail=detail,
    )  # fmt: skip


def _variables(deployment) -> dict:
    # Platform-injected values win over anything the caller sent under the same name.
    return {**deployment.inputs, **(deployment.injected or {})}


@activity.defn
def plan(deployment_id: str) -> None:
    deployment = _load(deployment_id)
    db.update(deployment_id, State.planning)
    with recovery.alive(deployment_id):
        terraform.plan(
            deployment_id, _source(deployment), _variables(deployment), deployment.subscription_id
        )


@activity.defn
def apply(deployment_id: str) -> None:
    deployment = _load(deployment_id)
    db.update(deployment_id, State.applying)
    with recovery.alive(deployment_id):
        outputs, withheld = terraform.apply(
            deployment_id, _source(deployment), _variables(deployment), deployment.subscription_id
        )
    db.update(deployment_id, State.succeeded, outputs=outputs, withheld=withheld)
    _outcome(deployment, "succeeded")


@activity.defn
def mark_failed(deployment_id: str, error: str) -> None:
    db.update(deployment_id, State.failed, error=error)
    deployment = _load(deployment_id)
    if deployment:
        _outcome(deployment, "failed", error=error[:500])


@activity.defn
def destroy(deployment_id: str) -> None:
    deployment = _load(deployment_id)
    db.update(deployment_id, State.destroying)
    with recovery.alive(deployment_id):
        terraform.destroy(
            deployment_id, _source(deployment), _variables(deployment), deployment.subscription_id
        )
    db.update(deployment_id, State.destroyed)
    _outcome(deployment, "destroyed")


ALL = [plan, apply, destroy, mark_failed]
