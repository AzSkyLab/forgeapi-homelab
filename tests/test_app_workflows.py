"""App workflows against a real Temporal dev server with scripted activities: liveness and
recovery behaviour that the ledger-level tests in test_apps.py cannot see."""

import asyncio
import json
import uuid
from pathlib import Path

from temporalio import activity
from temporalio.client import WorkflowHistory
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from app.dispatch import OperationDispatcher, app_workflow_id
from app.operation_workflow import (
    AppRolloutWorkflow,
    AppTeardownWorkflow,
    OperationPhaseWorkflow,
)
from app.settings import settings

QUEUE = "app-workflow-tests"


class Script:
    """Scripted activity results: each name pops its next scripted value (the last one repeats);
    an Exception instance is raised instead of returned. Calls are recorded."""

    def __init__(self, **scripts):
        self.scripts = {name: list(values) for name, values in scripts.items()}
        self.calls = []

    def next(self, name, *args):
        self.calls.append((name, *args))
        values = self.scripts.get(name, [None])
        value = values.pop(0) if len(values) > 1 else values[0]
        if isinstance(value, Exception):
            raise value
        return value


def activities(script: Script):
    def make(name):
        @activity.defn(name=name)
        async def scripted(*args):
            return script.next(name, *args)

        return scripted

    names = [
        "check_app",
        "accept_app_router",
        "queued_app_operations",
        "fail_app",
        "check_teardown",
        "accept_teardown_replica_destroys",
        "fail_app_teardown",
        "run_operation_phase",
        "mark_operation_uncertain",
    ]
    return [make(name) for name in names]


def boom():
    return ApplicationError("ledger unavailable", non_retryable=True)


async def run(workflow, args, script, wait_seconds=60):
    async with (
        await WorkflowEnvironment.start_local() as env,
        Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[AppRolloutWorkflow, AppTeardownWorkflow, OperationPhaseWorkflow],
            activities=activities(script),
        ),
    ):
        return await asyncio.wait_for(
            env.client.execute_workflow(
                workflow, args=args, id=f"wf-{uuid.uuid4().hex}", task_queue=QUEUE
            ),
            wait_seconds,
        )


def test_rollout_survives_a_failing_read_only_check():
    # check_app fails all three attempts of the first cycle; the workflow must wait and look
    # again instead of dying with nothing able to restart it.
    script = Script(check_app=[boom(), "ready"])
    assert asyncio.run(run(AppRolloutWorkflow.run, ["app_x"], script)) == "ready"
    assert [c[0] for c in script.calls].count("check_app") >= 2


def test_rollout_survives_a_failing_failure_write():
    script = Script(
        check_app=["router_needed"],
        accept_app_router=[boom()],
        fail_app=[boom(), None],
    )
    assert asyncio.run(run(AppRolloutWorkflow.run, ["app_x"], script)) == "failed"
    assert [c[0] for c in script.calls].count("fail_app") >= 2


def test_rollout_plans_operations_whose_dispatch_was_lost():
    script = Script(
        check_app=["planning_router", "ready"],
        queued_app_operations=[["op_orphan"]],
    )
    assert asyncio.run(run(AppRolloutWorkflow.run, ["app_x"], script)) == "ready"
    assert ("run_operation_phase", "op_orphan", "plan") in script.calls


def test_rollout_ends_when_a_teardown_took_over():
    script = Script(check_app=["superseded"])
    assert asyncio.run(run(AppRolloutWorkflow.run, ["app_x"], script)) == "ready"


def test_teardown_keeps_polling_through_uncertain_until_reconciled():
    script = Script(check_teardown=["uncertain", "replicas_needed", "destroyed"])
    script.scripts["accept_teardown_replica_destroys"] = [{"operations": [], "refused": False}]
    assert asyncio.run(run(AppTeardownWorkflow.run, ["app_x"], script)) == "destroyed"
    assert script.calls.count(("check_teardown", "app_x")) == 3


def test_teardown_retries_a_busy_replica_instead_of_failing():
    script = Script(
        check_teardown=["replicas_needed", "replicas_needed", "destroyed"],
        accept_teardown_replica_destroys=[
            {"operations": [], "refused": False, "retry": True},
            {"operations": ["op_r1"], "refused": False},
        ],
    )
    assert asyncio.run(run(AppTeardownWorkflow.run, ["app_x"], script)) == "destroyed"
    assert ("run_operation_phase", "op_r1", "plan") in script.calls
    assert not [c for c in script.calls if c[0] == "fail_app_teardown"]


def test_teardown_survives_failing_checks_and_failure_writes():
    script = Script(
        check_teardown=[boom(), "replicas_needed"],
        accept_teardown_replica_destroys=[boom()],
        fail_app_teardown=[boom(), None],
    )
    assert asyncio.run(run(AppTeardownWorkflow.run, ["app_x"], script, 90)) == "failed"


def test_a_dead_rollout_workflow_is_started_again_by_a_retried_request():
    async def scenario():
        script = Script(check_app=["planning_replicas"])
        async with (
            await WorkflowEnvironment.start_local() as env,
            Worker(
                env.client,
                task_queue="queue-x",
                workflows=[AppRolloutWorkflow, OperationPhaseWorkflow],
                activities=activities(script),
            ),
        ):
            previous, settings.task_queue = settings.task_queue, "queue-x"
            try:
                dispatcher = OperationDispatcher(env.client)
                await dispatcher.dispatch_app("app_y")
                handle = env.client.get_workflow_handle(app_workflow_id("app_y"))
                first = (await handle.describe()).run_id
                await dispatcher.dispatch_app("app_y")  # running: reused, not duplicated
                assert (await handle.describe()).run_id == first
                await handle.terminate("test")
                await dispatcher.dispatch_app("app_y")  # dead: started again
                again = await handle.describe()
                assert again.run_id != first and again.status.name == "RUNNING"
                await handle.terminate("done")
            finally:
                settings.task_queue = previous

    asyncio.run(scenario())


def test_old_histories_still_replay_after_the_liveness_changes():
    root = Path(__file__).parent / "fixtures/recovery"
    for name, workflows in (
        ("app_rollout_history.json", [AppRolloutWorkflow, OperationPhaseWorkflow]),
        ("app_teardown_history.json", [AppTeardownWorkflow, OperationPhaseWorkflow]),
    ):
        fixture = json.loads((root / name).read_text())
        history = WorkflowHistory.from_json(fixture["workflow_id"], fixture["history"])
        asyncio.run(Replayer(workflows=workflows).replay_workflow(history))
