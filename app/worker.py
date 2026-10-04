"""Temporal worker entrypoint: `uv run python -m app.worker`."""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.worker import Worker

from app import activities, app_activities, drift, operation_activities, recovery
from app.dispatch import DRIFT_SWEEP_WORKFLOW_ID, connect_temporal
from app.operation_workflow import (
    AppRolloutWorkflow,
    AppTeardownWorkflow,
    DriftSweepWorkflow,
    OperationPhaseWorkflow,
)
from app.settings import settings
from app.workflows import DeployWorkflow, DestroyWorkflow


def build_worker(client: Client) -> Worker:
    return Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[
            OperationPhaseWorkflow,
            AppRolloutWorkflow,
            AppTeardownWorkflow,
            DriftSweepWorkflow,
            DeployWorkflow,
            DestroyWorkflow,
        ],
        activities=[
            *activities.ALL,
            operation_activities.run_operation_phase,
            operation_activities.mark_operation_uncertain,
            app_activities.check_app,
            app_activities.accept_app_router,
            app_activities.queued_app_operations,
            app_activities.fail_app,
            app_activities.check_teardown,
            app_activities.accept_teardown_replica_destroys,
            app_activities.fail_app_teardown,
            drift.sweep_drift,
        ],
        max_concurrent_activities=4,
        activity_executor=ThreadPoolExecutor(max_workers=4),
    )


async def start_drift_sweep(client: Client) -> None:
    """Start the one scheduled sweep when `FORGEAPI_DRIFT_SWEEP_MINUTES` is set. Every worker
    may call this; a running sweep is reused, so there is never more than one."""
    if not settings.drift_sweep_minutes:
        return
    await client.start_workflow(
        DriftSweepWorkflow.run,
        id=DRIFT_SWEEP_WORKFLOW_ID,
        task_queue=settings.task_queue,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )


async def main() -> None:
    client = await connect_temporal()
    print(f"worker polling {settings.task_queue!r} on {settings.temporal_address}")
    recovery.sweep_forever()
    await start_drift_sweep(client)
    await build_worker(client).run()


if __name__ == "__main__":
    asyncio.run(main())
