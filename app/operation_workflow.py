"""Deterministic orchestration; only IDs and phase names enter Temporal history."""

from contextlib import suppress
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, WorkflowAlreadyStartedError
from temporalio.workflow import ParentClosePolicy

with workflow.unsafe.imports_passed_through():
    from app.app_activities import (
        accept_app_router,
        accept_teardown_replica_destroys,
        check_app,
        check_teardown,
        fail_app,
        fail_app_teardown,
        queued_app_operations,
    )
    from app.drift import sweep_drift
    from app.operation_activities import mark_operation_uncertain, run_operation_phase


@workflow.defn
class OperationPhaseWorkflow:
    @workflow.run
    async def run(self, operation_id: str, phase: str) -> None:
        if phase not in {"plan", "apply"}:
            raise ValueError("unknown operation phase")
        try:
            await workflow.execute_activity(
                run_operation_phase,
                args=[operation_id, phase],
                start_to_close_timeout=timedelta(minutes=10 if phase == "plan" else 30),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )
        except ActivityError:
            # Only the idempotent status write may retry. Never repeat a Terraform activity.
            await workflow.execute_activity(
                mark_operation_uncertain,
                args=[operation_id, phase],
                start_to_close_timeout=timedelta(seconds=30),
            )


POLL_SECONDS = 5  # how often the rollout looks at its members
SLOW_POLL_SECONDS = 30  # after an hour of waiting (for example on a human approval)
SLOW_AFTER_SECONDS = 3600
MAX_WAIT_SECONDS = 7 * 24 * 3600  # the rollout gives up (app `failed`) after a week


READ_ONLY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1))


async def start_plans(operation_ids: list[str]) -> None:
    for operation_id in operation_ids:
        # Same workflow ID as POST /operations uses for a plan phase.
        with suppress(WorkflowAlreadyStartedError):
            await workflow.start_child_workflow(
                OperationPhaseWorkflow.run,
                args=[operation_id, "plan"],
                id=f"forgeapi-{operation_id}-plan",
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                parent_close_policy=ParentClosePolicy.ABANDON,
            )


async def start_queued_plans(app_id: str) -> None:
    """A restarted workflow starts the plans of operations that were accepted but never planned
    (the dispatch was lost). Patched: histories recorded before this step replay unchanged."""
    if not workflow.patched("app-plan-queued"):
        return
    try:
        queued = await workflow.execute_activity(
            queued_app_operations,
            app_id,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=READ_ONLY,
        )
    except ActivityError:
        return  # read-only; the next cycle tries again
    await start_plans(queued)


async def fail_quietly(activity_fn, app_id: str, reason: str) -> bool:
    """Record a failure; False when the (idempotent, retried) write itself failed, so the caller
    keeps polling rather than ending the workflow with nothing recorded."""
    try:
        await workflow.execute_activity(
            activity_fn,
            args=[app_id, reason],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=READ_ONLY,
        )
    except ActivityError:
        return False
    return True


@workflow.defn
class AppRolloutWorkflow:
    """Waits for an app's replicas, then accepts and plans its router. Never applies anything:
    both applies happen only through `POST /apps/{id}/approve`. Only IDs enter history.

    A failing read-only check or failure write never ends the workflow (it retries on the next
    cycle); it ends once the rollout's router is deployed, the app failed, or a teardown began.
    A failed or terminated run can be started again by the API."""

    @workflow.run
    async def run(self, app_id: str, waited: int = 0) -> str:
        while True:
            try:
                state = await workflow.execute_activity(
                    check_app,
                    app_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=READ_ONLY,
                )
            except ActivityError:
                state = None  # the ledger was briefly unavailable: wait and look again
            if state == "router_needed":
                try:
                    router_id = await workflow.execute_activity(
                        accept_app_router,
                        app_id,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RetryPolicy(
                            maximum_attempts=3, initial_interval=timedelta(seconds=2)
                        ),
                    )
                except ActivityError:
                    if await fail_quietly(
                        fail_app, app_id, "router could not be accepted; submit a new app"
                    ):
                        return "failed"
                else:
                    if router_id != "retry":  # "retry": busy for now, try again next cycle
                        await start_plans([router_id] if router_id else [])
                        continue
            if state in ("ready", "failed"):
                return state
            if state == "superseded":
                return "ready"  # a teardown took over
            if state and state.startswith("planning"):
                await start_queued_plans(app_id)
            if waited >= MAX_WAIT_SECONDS and await fail_quietly(
                fail_app, app_id, "rollout timed out waiting for the members to finish"
            ):
                return "failed"
            pause = POLL_SECONDS if waited < SLOW_AFTER_SECONDS else SLOW_POLL_SECONDS
            await workflow.sleep(pause)
            waited += pause
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(args=[app_id, waited])


@workflow.defn
class AppTeardownWorkflow:
    """Ordered teardown of an app. The router's destroy is accepted and planned by the request;
    this workflow waits for it to succeed (someone approves it with `POST /apps/{id}/approve`),
    then accepts and plans the replicas' destroys and waits for those. It never applies
    anything: both applies happen only through the approval gates. Only IDs enter history.

    It keeps polling through `uncertain` (an operator reconciling a destroy as succeeded lets it
    carry on) and ends at `destroyed`, `failed` or a week's timeout. A busy replica (a drift
    check) is retried next cycle, not treated as a refusal. `POST /apps/{id}/destroy` starts it
    again if it stopped."""

    @workflow.run
    async def run(self, app_id: str, waited: int = 0) -> str:
        while True:
            try:
                state = await workflow.execute_activity(
                    check_teardown,
                    app_id,
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=READ_ONLY,
                )
            except ActivityError:
                state = None
            if state == "replicas_needed":
                try:
                    accepted = await workflow.execute_activity(
                        accept_teardown_replica_destroys,
                        app_id,
                        start_to_close_timeout=timedelta(minutes=2),
                        retry_policy=RetryPolicy(
                            maximum_attempts=3, initial_interval=timedelta(seconds=2)
                        ),
                    )
                except ActivityError:
                    if await fail_quietly(
                        fail_app_teardown,
                        app_id,
                        "replica destroys could not be accepted; start a new teardown",
                    ):
                        return "failed"
                else:
                    if not accepted.get("retry"):  # retry: a replica is busy, try next cycle
                        await start_plans(accepted["operations"])
                        continue
            if state in ("destroyed", "failed"):
                return state
            if state == "uncertain" and not workflow.patched("teardown-poll-uncertain"):
                return state  # histories recorded before it polled through uncertain
            if state and state.startswith("planning"):
                await start_queued_plans(app_id)
            if waited >= MAX_WAIT_SECONDS and await fail_quietly(
                fail_app_teardown, app_id, "teardown timed out waiting for the destroys to finish"
            ):
                return "failed"
            pause = POLL_SECONDS if waited < SLOW_AFTER_SECONDS else SLOW_POLL_SECONDS
            await workflow.sleep(pause)
            waited += pause
            if workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(args=[app_id, waited])


DRIFT_CYCLES_PER_RUN = 100  # then continue as new, so history stays small


@workflow.defn
class DriftSweepWorkflow:
    """Scheduled drift checks. Each cycle one activity accepts drift checks for idle ready
    resources not checked within `FORGEAPI_DRIFT_SWEEP_MINUTES` and this workflow starts their
    plan phases; then it sleeps. A drift check never applies anything, and the busy rule keeps
    it to one check per resource at a time. Only operation IDs enter history. Ends when the
    setting is turned off."""

    @workflow.run
    async def run(self, cycles: int = 0) -> str:
        while True:
            result = await workflow.execute_activity(
                sweep_drift,
                start_to_close_timeout=timedelta(minutes=2),
                # Acceptance is idempotent per resource and interval, so a retry is safe.
                retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=1)),
            )
            if not result["enabled"]:
                return "disabled"
            for operation_id in result["operations"]:
                # Same workflow ID as POST /operations uses for a plan phase.
                with suppress(WorkflowAlreadyStartedError):
                    await workflow.start_child_workflow(
                        OperationPhaseWorkflow.run,
                        args=[operation_id, "plan"],
                        id=f"forgeapi-{operation_id}-plan",
                        id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                        parent_close_policy=ParentClosePolicy.ABANDON,
                    )
            await workflow.sleep(result["sleep_seconds"])
            cycles += 1
            if cycles >= DRIFT_CYCLES_PER_RUN or workflow.info().is_continue_as_new_suggested():
                workflow.continue_as_new(args=[0])
