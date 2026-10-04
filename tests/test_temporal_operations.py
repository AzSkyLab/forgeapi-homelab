"""Real Temporal proves asynchronous acceptance, exact-plan execution and worker restart."""

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
from temporalio.api.enums.v1 import EventType

from app import ledger, terraform
from app.dispatch import OperationDispatcher, get_dispatcher, workflow_id
from app.main import app
from app.worker import build_worker
from tests.operation_support import phase_done, temporal_api
from tests.test_operations import INTENT


def test_async_acceptance_without_worker_and_restart_between_phases():
    async def scenario():
        async with temporal_api() as (api, client):
            headers = {"Idempotency-Key": "async"}
            # No worker exists: 202 proves HTTP acceptance doesn't wait for Terraform.
            responses = await asyncio.wait_for(
                asyncio.gather(
                    api.post("/operations", json=INTENT, headers=headers),
                    api.post("/v1/operations", json=INTENT, headers=headers),
                ),
                10,
            )
            assert all(response.status_code == 202 for response in responses)
            op = responses[0].json()
            assert responses[1].json()["id"] == op["id"]
            assert responses[1].json()["resource_id"] == op["resource_id"]
            assert op["state"] == "queued"
            assert not terraform.deployment_dir(op["resource_id"]).exists()
            with ledger.connect() as con:
                assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
                assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 1
                assert con.execute(
                    "SELECT count(*) FROM events WHERE action='operation.create' "
                    "AND outcome='accepted'"
                ).fetchone()[0] == 1
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *[api.post("/operations", json=INTENT, headers=headers) for _ in range(4)]
                ),
                10,
            )
            assert all(r.status_code == 202 and r.json()["id"] == op["id"] for r in responses)
            async with build_worker(client):
                await phase_done(client, op["id"], "plan")
            planned = (await api.get(op["links"]["self"])).json()
            assert planned["state"] == "planned"
            digest = planned["plan_digest"]
            # First worker stopped. Exact-plan execution queues for a replacement worker.
            responses = await asyncio.wait_for(
                asyncio.gather(
                    *[
                        api.post(path, json={"plan_digest": digest})
                        for path in (
                            op["links"]["execute"],
                            f"/v1/operations/{op['id']}/execute",
                            op["links"]["execute"],
                            f"/v1/operations/{op['id']}/execute",
                        )
                    ]
                ),
                10,
            )
            assert all(r.status_code == 202 for r in responses)
            assert ledger.get(op["id"])["state"] == "apply_queued"
            with ledger.connect() as con:
                assert con.execute(
                    "SELECT count(*) FROM events WHERE action='operation.execute' "
                    "AND outcome='accepted'"
                ).fetchone()[0] == 1
            async with build_worker(client):
                await phase_done(client, op["id"], "apply")
            done = (await api.get(op["links"]["self"])).json()
            assert done["state"] == "succeeded"
            assert Path(done["outputs"]["path"]).read_text() == "agent infrastructure"
            receipts = terraform.log_path(op["resource_id"]).read_text()
            assert receipts.count("$ terraform plan -no-color -out=tfplan") == 1
            assert receipts.count("$ terraform apply -no-color tfplan") == 1
            # Even an explicit late dispatch cannot restart either completed phase.
            dispatcher = OperationDispatcher(client)
            for phase in ("plan", "apply"):
                await dispatcher.dispatch(op["id"], phase)
                history = await client.get_workflow_handle(
                    workflow_id(op["id"], phase)
                ).fetch_history()
                scheduled = [
                    e.activity_task_scheduled_event_attributes
                    for e in history.events
                    if e.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                ]
                assert len(scheduled) == 1
                assert scheduled[0].retry_policy.maximum_attempts == 1
            events = ledger.events(op["id"], 0, 100)
            assert [e["outcome"] for e in events] == [
                "accepted",
                "planning",
                "planned",
                "accepted",
                "applying",
                "succeeded",
            ]

    asyncio.run(scenario())



def test_lost_dispatch_acknowledgement_recovers_same_operation():
    async def scenario():
        async with temporal_api() as (api, client):
            real = OperationDispatcher(client)

            class LostAck:
                async def dispatch(self, operation_id, phase):
                    await real.dispatch(operation_id, phase)
                    raise ConnectionError("raw-auth-error-must-not-leak")

            app.dependency_overrides[get_dispatcher] = LostAck
            headers = {"Idempotency-Key": "lost-response"}
            first = await api.post("/operations", json=INTENT, headers=headers)
            assert first.status_code == 503
            assert first.json()["error"]["code"] == "dispatch_unconfirmed"
            assert "raw-auth" not in first.text
            op_id = first.json()["error"]["operation_id"]
            assert [e["outcome"] for e in ledger.events(op_id, 0, 100)] == ["accepted"]
            app.dependency_overrides[get_dispatcher] = lambda: real
            retry = await api.post("/operations", json=INTENT, headers=headers)
            assert retry.status_code == 202 and retry.json()["id"] == op_id
            async with build_worker(client):
                await phase_done(client, op_id, "plan")
            planned = ledger.get(op_id)
            assert planned["state"] == "planned"
            app.dependency_overrides[get_dispatcher] = LostAck
            execute = f"/operations/{op_id}/execute"
            payload = {"plan_digest": planned["plan_digest"]}
            assert (await api.post(execute, json=payload)).status_code == 503
            app.dependency_overrides[get_dispatcher] = lambda: real
            assert (await api.post(execute, json=payload)).status_code == 202
            async with build_worker(client):
                await phase_done(client, op_id, "apply")
            assert ledger.get(op_id)["state"] == "succeeded"

    asyncio.run(scenario())


def test_dispatch_failure_before_start_can_retry(recorded_dispatcher):
    class Unavailable:
        async def dispatch(self, *_args):
            raise ConnectionError("sensitive rpc failure")

    app.dependency_overrides[get_dispatcher] = Unavailable
    with TestClient(app) as api:
        headers = {"Idempotency-Key": "offline"}
        first = api.post("/operations", json=INTENT, headers=headers)
        assert first.status_code == 503 and "sensitive" not in first.text
        op_id = first.json()["error"]["operation_id"]
        assert ledger.get(op_id)["state"] == "queued"
        app.dependency_overrides[get_dispatcher] = lambda: recorded_dispatcher
        retry = api.post("/operations", json=INTENT, headers=headers)
        assert retry.status_code == 202 and retry.json()["id"] == op_id
        assert list(recorded_dispatcher.pending) == [(op_id, "plan")]


def test_failed_activity_is_not_retried_and_workflow_records_uncertainty():
    from temporalio import activity
    from temporalio.exceptions import ApplicationError
    from temporalio.worker import Worker

    from app.operation_activities import mark_operation_uncertain
    from app.operation_workflow import OperationPhaseWorkflow
    from app.settings import settings

    calls = []

    @activity.defn(name="run_operation_phase")
    async def lost_activity(operation_id: str, phase: str) -> bool:
        calls.append((operation_id, phase))
        assert ledger.claim(operation_id, phase)
        raise ApplicationError("simulated activity loss")

    async def scenario():
        async with temporal_api() as (api, client):
            accepted = await api.post(
                "/operations", json=INTENT, headers={"Idempotency-Key": "lost-activity"}
            )
            op = accepted.json()
            # Worker uses the real workflow and status writer, with a controlled activity failure.
            from concurrent.futures import ThreadPoolExecutor

            with ThreadPoolExecutor(max_workers=1) as executor:
                async with Worker(
                    client,
                    task_queue=settings.task_queue,
                    workflows=[OperationPhaseWorkflow],
                    activities=[lost_activity, mark_operation_uncertain],
                    activity_executor=executor,
                    max_concurrent_activities=1,
                ):
                    await phase_done(client, op["id"], "plan")
            assert calls == [(op["id"], "plan")]
            result = (await api.get(op["links"]["self"])).json()
            assert result["state"] == "uncertain"
            assert result["next_action"] == "reconcile_with_operator"
            refused = await api.post(
                "/operations",
                json={**INTENT, "resource_id": op["resource_id"]},
                headers={"Idempotency-Key": "unsafe-replacement"},
            )
            assert refused.status_code == 409

    asyncio.run(scenario())
