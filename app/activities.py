"""Temporal activities. All I/O (database, git, Terraform) lives here, never in the workflow."""

from temporalio import activity

from app import catalog, db, terraform
from app.models import State


def _source(deployment) -> str:
    # Pinned to the commit resolved when the request was accepted, not to the (movable) tag.
    pattern = catalog.get(deployment.pattern)
    return catalog.Resolved(pattern, deployment.version, deployment.commit).terraform_source


@activity.defn
def plan(deployment_id: str) -> None:
    deployment = db.get(deployment_id)
    db.update(deployment_id, State.planning)
    terraform.plan(deployment_id, _source(deployment), deployment.inputs)


@activity.defn
def apply(deployment_id: str) -> None:
    db.update(deployment_id, State.applying)
    outputs = terraform.apply(deployment_id)
    db.update(deployment_id, State.succeeded, outputs=outputs)


@activity.defn
def mark_failed(deployment_id: str, error: str) -> None:
    db.update(deployment_id, State.failed, error=error)


@activity.defn
def destroy(deployment_id: str) -> None:
    deployment = db.get(deployment_id)
    db.update(deployment_id, State.destroying)
    terraform.destroy(deployment_id, _source(deployment), deployment.inputs)
    db.update(deployment_id, State.destroyed)


ALL = [plan, apply, destroy, mark_failed]
