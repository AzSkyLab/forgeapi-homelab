"""Deterministic workflow: plan, apply, record failure. No I/O here."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

with workflow.unsafe.imports_passed_through():
    from app import activities

# Terraform failures are almost never fixed by retrying, and a saved plan must not be applied
# twice, so activities run once. Temporal still survives worker/API restarts.
_ONCE = RetryPolicy(maximum_attempts=1)


@workflow.defn
class DeployWorkflow:
    @workflow.run
    async def run(self, deployment_id: str) -> str:
        try:
            await workflow.execute_activity(
                activities.plan, deployment_id,
                start_to_close_timeout=timedelta(minutes=10), retry_policy=_ONCE,
            )
            await workflow.execute_activity(
                activities.apply, deployment_id,
                start_to_close_timeout=timedelta(minutes=30), retry_policy=_ONCE,
            )
        except ActivityError as err:
            message = str(getattr(err.cause, "message", None) or err.cause or err)
            await workflow.execute_activity(
                activities.mark_failed, args=[deployment_id, message],
                start_to_close_timeout=timedelta(seconds=30),
            )
            return "failed"
        return "succeeded"


@workflow.defn
class DestroyWorkflow:
    @workflow.run
    async def run(self, deployment_id: str) -> str:
        try:
            await workflow.execute_activity(
                activities.destroy,
                deployment_id,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=_ONCE,
            )
        except ActivityError as err:
            message = str(getattr(err.cause, "message", None) or err.cause or err)
            await workflow.execute_activity(
                activities.mark_failed,
                args=[deployment_id, message],
                start_to_close_timeout=timedelta(seconds=30),
            )
            return "failed"
        return "destroyed"
