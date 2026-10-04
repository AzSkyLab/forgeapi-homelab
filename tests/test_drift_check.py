"""Continuous drift detection: `POST /resources/{id}/drift-check` and the scheduled sweep.

Real local Terraform through the recorded dispatcher; the sweep runs on real Temporal."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from temporalio.api.enums.v1 import EventType
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from app import drift, ledger, terraform
from app.main import app, caller_context
from app.operation_workflow import DriftSweepWorkflow, OperationPhaseWorkflow
from app.settings import settings
from app.tenants import Caller
from tests.operation_support import temporal_api
from tests.test_drift import DRIFT_DELETE, _deploy, _replan
from tests.test_operation_policy import placed as placed
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit
from tests.test_promotion import promo as promo

SWEEP_ID = "forgeapi-drift-sweep"
FIXTURE = Path(__file__).parent / "fixtures/recovery/drift_sweep_history.json"


def check(client, resource_id, key="check-1", prefix=""):
    return client.post(
        f"{prefix}/resources/{resource_id}/drift-check", headers={"Idempotency-Key": key}
    )


def run_check(client, dispatcher, resource_id, key="check-1"):
    """Submit a drift check and run its plan phase; returns the finished operation."""
    response = check(client, resource_id, key)
    assert response.status_code == 202, response.text
    assert dispatcher.run_next()
    return client.get(response.json()["links"]["self"]).json()


def resource(client, resource_id):
    return client.get(f"/resources/{resource_id}").json()


def age(resource_id):
    """Pretend the resource was last checked long ago, so the sweep considers it due."""
    with ledger.connect() as con:
        row = con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
        body = json.loads(row[0])
        body["drift_checked_at"] = "2000-01-01T00:00:00+00:00"
        con.execute("UPDATE resources SET body=? WHERE id=?", (json.dumps(body), resource_id))


def in_flight_update(client, dispatcher, resource_id, key):
    """A planned (not applied) update: the resource is ready but busy."""
    planned = _replan(client, dispatcher, resource_id, key)
    assert planned["state"] == "planned"
    return planned


def state_file(resource_id):
    return terraform.deployment_dir(resource_id) / "work" / "terraform.tfstate"


def test_in_sync_after_deploy_then_drifted_after_out_of_band_delete_then_redeploy_fixes(
    agent_client, recorded_dispatcher
):
    done = _deploy(agent_client, recorded_dispatcher, "dc-deploy")
    rid = done["resource_id"]
    assert resource(agent_client, rid)["drift_status"] == "in_sync"  # a fresh apply matches state
    before = resource(agent_client, rid)

    clean = run_check(agent_client, recorded_dispatcher, rid, "dc-clean")
    assert clean["action"] == "drift_check"
    assert clean["state"] == "succeeded" and clean["terminal"] and clean["next_action"] == "done"
    assert clean["drift"] == [] and clean["plan_digest"] is None and clean["changes"] is None
    assert resource(agent_client, rid)["drift_status"] == "in_sync"

    Path(done["outputs"]["path"]).unlink()  # out-of-band
    state_before = state_file(rid).read_bytes()
    found = run_check(agent_client, recorded_dispatcher, rid, "dc-drifted")
    assert found["state"] == "succeeded", found
    assert found["drift"] == DRIFT_DELETE
    # Nothing was applied: state is byte-identical, no plan file remains, the file is still gone.
    assert state_file(rid).read_bytes() == state_before
    work = terraform.deployment_dir(rid) / "work"
    assert not list(work.glob("*tfplan*"))
    assert not Path(done["outputs"]["path"]).exists()
    receipts = terraform.log_path(rid).read_text()
    assert receipts.count("$ terraform apply") == 1  # the deploy's; checks never apply
    assert receipts.count("$ terraform plan -refresh-only") == 2

    now = resource(agent_client, rid)
    assert now["state"] == "ready" and now["drift_status"] == "drifted"
    assert now["drift"][0]["address"] == "local_file.this"
    assert now["drift_checked_at"]
    # A check changes nothing else about the resource.
    for field in (
        "version", "commit", "outputs", "managed_objects", "estimated_monthly_cost",
        "latest_operation_id", "labels",
    ):  # fmt: skip
        assert now[field] == before[field], field
    listing = agent_client.get("/resources", params={"drift_status": "drifted"}).json()
    assert [r["id"] for r in listing["items"]] == [rid]
    assert agent_client.get("/resources", params={"drift_status": "in_sync"}).json()["items"] == []
    both = agent_client.get(
        "/resources", params={"drift_status": "drifted", "state": "ready", "pattern": "local-file"}
    ).json()
    assert [r["id"] for r in both["items"]] == [rid]

    # Redeploying (update the same resource) fixes it and resets the verdict.
    replanned = _replan(agent_client, recorded_dispatcher, rid, "dc-redeploy")
    assert replanned["state"] == "planned"
    payload = {"plan_digest": replanned["plan_digest"]}
    assert agent_client.post(replanned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    assert Path(done["outputs"]["path"]).is_file()
    fixed = resource(agent_client, rid)
    assert fixed["drift_status"] == "in_sync" and fixed["drift"] == []
    assert agent_client.get("/resources", params={"drift_status": "drifted"}).json()["items"] == []


def test_check_is_hidden_from_default_operation_listing(agent_client, recorded_dispatcher):
    done = _deploy(agent_client, recorded_dispatcher, "dc-list-deploy")
    op = run_check(agent_client, recorded_dispatcher, done["resource_id"], "dc-list")

    def ids(**params):
        listed = agent_client.get("/operations", params=params).json()["items"]
        return {item["id"] for item in listed}

    assert op["id"] not in ids()
    assert ids(include_checks="true") == {done["id"], op["id"]}
    assert ids(action="drift_check") == {op["id"]}
    assert ids(action="deploy") == {done["id"]}
    assert op["id"] not in ids(resource_id=done["resource_id"])
    # The check itself is readable by ID, and its events show system-free audit of the request.
    assert agent_client.get(f"/operations/{op['id']}").json()["action"] == "drift_check"
    assert [e["outcome"] for e in ledger.events(op["id"], 0, 10)] == [
        "accepted", "planning", "succeeded",
    ]  # fmt: skip


def test_busy_and_non_ready_resources_are_409_and_audited(agent_client, recorded_dispatcher):
    pending = submit(agent_client, "dc-busy").json()  # queued deploy: resource is busy and pending
    busy = check(agent_client, pending["resource_id"], "dc-b1")
    assert busy.status_code == 409
    assert busy.json()["error"]["operation_id"] == pending["id"]
    assert recorded_dispatcher.run_next()  # planned, still busy
    assert check(agent_client, pending["resource_id"], "dc-b2").status_code == 409
    planned = agent_client.get(pending["links"]["self"]).json()
    assert agent_client.post(planned["links"]["discard"]).status_code == 200
    # Idle but never applied: not ready.
    refused = check(agent_client, pending["resource_id"], "dc-b3")
    assert refused.status_code == 409
    with ledger.connect() as con:
        refused_events = con.execute("SELECT count(*) FROM events WHERE outcome='refused'")
        assert refused_events.fetchone()[0] == 3
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
    assert check(agent_client, "res_" + "0" * 32, "dc-b4").status_code == 404


def test_destroyed_resource_cannot_be_checked(agent_client, recorded_dispatcher):
    done = _deploy(agent_client, recorded_dispatcher, "dc-destroy-deploy")
    rid = done["resource_id"]
    destroy = submit(
        agent_client,
        "dc-destroy",
        action="destroy",
        resource_id=rid,
        pattern="local-file",
        version=None,
        inputs={"filename": "drift.txt", "content": "original"},
    ).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(destroy["links"]["self"]).json()
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    gone = resource(agent_client, rid)
    assert gone["state"] == "destroyed" and gone["drift_status"] is None
    assert check(agent_client, rid, "dc-after-destroy").status_code == 409


def test_replay_is_idempotent_across_routes_and_a_changed_body_conflicts(
    agent_client, recorded_dispatcher
):
    done = _deploy(agent_client, recorded_dispatcher, "dc-idem-deploy")
    rid = done["resource_id"]
    first = check(agent_client, rid, "dc-idem")
    again = check(agent_client, rid, "dc-idem", prefix="/v1")
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    assert again.headers["location"] == f"/v1/operations/{first.json()['id']}"
    assert len(recorded_dispatcher.pending) == 1
    with ledger.connect() as con:
        count = con.execute(
            "SELECT count(*) FROM events WHERE action='operation.create' AND outcome='accepted'"
        ).fetchone()[0]
        assert count == 2  # the deploy and exactly one check
    assert recorded_dispatcher.run_next()
    other = _deploy(agent_client, recorded_dispatcher, "dc-idem-deploy-2")
    reused = check(agent_client, other["resource_id"], "dc-idem")
    assert reused.status_code == 409
    assert reused.json()["error"]["reason"] == "idempotency_key_reused"


def test_another_caller_gets_404_and_cannot_see_the_verdict(agent_client, recorded_dispatcher):
    done = _deploy(agent_client, recorded_dispatcher, "dc-other-deploy")
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        assert check(agent_client, done["resource_id"], "dc-other").status_code == 404
        assert (
            agent_client.get("/resources", params={"drift_status": "in_sync"}).json()["items"] == []
        )
    finally:
        app.dependency_overrides.pop(caller_context, None)
    assert not recorded_dispatcher.pending


def test_terraform_failure_ends_failed_with_a_diagnostic_and_status_unknown(
    agent_client, recorded_dispatcher, monkeypatch
):
    done = _deploy(agent_client, recorded_dispatcher, "dc-fail-deploy")
    rid = done["resource_id"]
    real = settings.terraform_bin
    fake = Path(settings.data_dir) / "fail-terraform"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = plan ]; then echo "Error: boom unauthorized" >&2; exit 1; fi\n'
        f'exec {real} "$@"\n'
    )
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "terraform_bin", str(fake))
    failed = run_check(agent_client, recorded_dispatcher, rid, "dc-fail")
    assert failed["state"] == "failed" and failed["next_action"] == "inspect_failure"
    assert failed["diagnostic"].startswith("terraform plan failed:")
    assert "credentials or permissions" in failed["diagnostic"]  # auth text is generalized
    assert failed["drift"] is None
    now = resource(agent_client, rid)
    assert now["state"] == "ready" and now["drift_status"] == "unknown" and now["drift"] is None
    assert check(agent_client, rid, "dc-fail-again").status_code == 202  # not blocked


def test_held_state_lock_fails_at_once_and_is_never_unlocked(
    agent_client, recorded_dispatcher, monkeypatch
):
    done = _deploy(agent_client, recorded_dispatcher, "dc-lock-deploy")
    rid = done["resource_id"]
    real = settings.terraform_bin
    log = Path(settings.data_dir) / "lock-calls"
    fake = Path(settings.data_dir) / "lock-terraform"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$@" >> {log}\n'
        'if [ "$1" = plan ]; then\n'
        '  printf "Error: Error acquiring the state lock\\n\\n  ID:        abcdef12-3456\\n" >&2\n'
        "  exit 1\n"
        "fi\n"
        f'exec {real} "$@"\n'
    )
    fake.chmod(0o755)
    monkeypatch.setattr(settings, "terraform_bin", str(fake))
    failed = run_check(agent_client, recorded_dispatcher, rid, "dc-lock")
    assert failed["state"] == "failed"
    assert "locked by another run" in failed["error"]
    assert failed["diagnostic"] is None
    calls = log.read_text()
    assert "force-unlock" not in calls
    assert "-lock-timeout=0s" in calls and "-refresh-only" in calls
    assert calls.count("plan ") == 1  # run once, never retried


def test_interrupted_check_fails_instead_of_uncertain(agent_client, recorded_dispatcher):
    done = _deploy(agent_client, recorded_dispatcher, "dc-int-deploy")
    op = check(agent_client, done["resource_id"], "dc-int").json()
    ledger.claim(op["id"], "plan")
    ledger.interrupted(op["id"], "plan")
    stored = ledger.get(op["id"])
    assert stored["state"] == "failed" and "run it again" in stored["error"]
    assert resource(agent_client, done["resource_id"])["drift_status"] == "unknown"
    assert check(agent_client, done["resource_id"], "dc-int-2").status_code == 202  # released


def test_check_never_changes_a_deploy_plan_or_the_state_of_a_never_planned_workspace(
    agent_client, recorded_dispatcher
):
    """`plan -refresh-only` without applying must not write state; also covers a first check."""
    done = _deploy(agent_client, recorded_dispatcher, "dc-bytes-deploy")
    rid = done["resource_id"]
    snapshot = {
        p.name: p.read_bytes()
        for p in (terraform.deployment_dir(rid) / "work").glob("terraform.tfstate*")
    }
    run_check(agent_client, recorded_dispatcher, rid, "dc-bytes")
    after = {
        p.name: p.read_bytes()
        for p in (terraform.deployment_dir(rid) / "work").glob("terraform.tfstate*")
    }
    assert after == snapshot


def test_old_stored_resource_without_drift_fields_reads_null(agent_client, recorded_dispatcher):
    done = _deploy(agent_client, recorded_dispatcher, "dc-old-deploy")
    with ledger.connect() as con:
        row = json.loads(
            con.execute("SELECT body FROM resources WHERE id=?", (done["resource_id"],)).fetchone()[
                0
            ]
        )
        for key in ("drift_status", "drift_checked_at", "drift"):
            row.pop(key)
        con.execute(
            "UPDATE resources SET body=? WHERE id=?", (json.dumps(row), done["resource_id"])
        )
    old = resource(agent_client, done["resource_id"])
    assert (old["drift_status"], old["drift_checked_at"], old["drift"]) == (None, None, None)


def test_discovery_advertises_drift_checks(agent_client):
    for prefix in ("", "/v1"):
        assert agent_client.get(f"{prefix}/agent").json()["capabilities"]["drift_checks"] is True


# --- scheduled sweep --------------------------------------------------------------------------


def test_sweep_checks_idle_resources_as_the_system_actor_and_skips_busy_ones(
    agent_client, recorded_dispatcher, monkeypatch
):
    monkeypatch.setattr(settings, "drift_sweep_minutes", 1 / 60)  # one second
    idle = _deploy(agent_client, recorded_dispatcher, "sw-idle")["resource_id"]
    other = submit(agent_client, "sw-busy", pattern="local-file", version=None,
                   inputs={"filename": "busy.txt", "content": "b"}).json()  # fmt: skip
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(other["links"]["self"]).json()
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    busy = other["resource_id"]
    in_flight_update(
        agent_client, recorded_dispatcher, busy, "sw-busy-update"
    )  # ready but busy (planned)
    pending = submit(agent_client, "sw-never", inputs={"filename": "n.txt", "content": "n"}).json()
    ledger.finish(ledger.claim(pending["id"], "plan"), "failed", error="x")  # pending, idle
    # A freshly checked resource is not due; make the other two old enough to be.
    assert drift.sweep_drift()["operations"] == []
    age(idle)
    age(busy)

    result = drift.sweep_drift()
    assert result["enabled"] and result["sleep_seconds"] == 1.0
    assert len(result["operations"]) == 1
    swept = ledger.get(result["operations"][0])
    assert swept["actor"] == drift.SYSTEM_ACTOR and swept["resource_id"] == idle
    assert swept["action"] == "drift_check" and swept["state"] == "queued"
    # Idempotent within an interval: a retry accepts nothing new and re-reports the queued check.
    assert drift.sweep_drift()["operations"] == [swept["id"]]
    with ledger.connect() as con:
        count = con.execute(
            "SELECT count(*) FROM operations WHERE actor=?", (drift.SYSTEM_ACTOR,)
        ).fetchone()[0]
        assert count == 1
        trail = con.execute(
            "SELECT actor, outcome FROM events WHERE operation_id=?", (swept["id"],)
        )
        assert [tuple(r) for r in trail] == [(drift.SYSTEM_ACTOR, "accepted")]
    # A user's check sees the in-flight sweep check as busy.
    assert check(agent_client, idle, "sw-user").status_code == 409


def test_sweep_is_off_without_the_setting(monkeypatch):
    monkeypatch.setattr(settings, "drift_sweep_minutes", None)
    assert drift.sweep_drift() == {"enabled": False, "operations": [], "sleep_seconds": 0.0}


def _deploy_other(client, dispatcher):
    other = submit(client, "sw-t-other", pattern="local-file", version=None,
                   inputs={"filename": "busy.txt", "content": "b"}).json()  # fmt: skip
    assert dispatcher.run_next()
    planned = client.get(other["links"]["self"]).json()
    payload = {"plan_digest": planned["plan_digest"]}
    assert client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert dispatcher.run_next()
    return other


def test_sweep_workflow_on_real_temporal_checks_idle_and_skips_busy(
    agent_client, recorded_dispatcher, monkeypatch
):
    from app.worker import build_worker

    monkeypatch.setattr(settings, "drift_sweep_minutes", 0.03)  # 1.8 seconds

    async def scenario():
        # Resources are created by the recorded dispatcher (synchronously), then the real
        # Temporal sweep checks them.
        idle = await asyncio.to_thread(_deploy, agent_client, recorded_dispatcher, "sw-t-idle")
        idle_id = idle["resource_id"]
        Path(idle["outputs"]["path"]).unlink()  # drift to be found
        other = await asyncio.to_thread(_deploy_other, agent_client, recorded_dispatcher)
        busy_id = other["resource_id"]
        await asyncio.to_thread(
            in_flight_update, agent_client, recorded_dispatcher, busy_id, "sw-t-busy-update"
        )
        age(idle_id)
        age(busy_id)
        async with temporal_api() as (_api, client), build_worker(client):
            handle = await client.start_workflow(
                DriftSweepWorkflow.run,
                id=SWEEP_ID,
                task_queue=settings.task_queue,
            )
            for _ in range(300):
                if ledger.resource(idle_id).get("drift_status") == "drifted":
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("sweep never recorded the drift")
            # Turning the sweep off ends the workflow at its next cycle.
            monkeypatch.setattr(settings, "drift_sweep_minutes", None)
            assert await asyncio.wait_for(handle.result(), 60) == "disabled"
            history = await handle.fetch_history()
        checks = ledger.list_operations(drift.SYSTEM_ACTOR, None, 0, 100, action="drift_check")
        assert {c["resource_id"] for c in checks} == {idle_id}  # the busy one is never checked
        assert all(c["actor"] == drift.SYSTEM_ACTOR and c["state"] == "succeeded" for c in checks)
        assert ledger.resource(busy_id)["drift_status"] == "in_sync"  # untouched
        # One plan child per check, under the id a user's request would use.
        assert {f"forgeapi-{c['id']}-plan" for c in checks} <= {
            e.start_child_workflow_execution_initiated_event_attributes.workflow_id
            for e in history.events
            if e.event_type == EventType.EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED
        }
        return history

    history = asyncio.run(scenario())
    assert any(e.event_type == EventType.EVENT_TYPE_TIMER_FIRED for e in history.events)
    if os.environ.get("FORGEAPI_RECORD_DRIFT_FIXTURE"):  # how the replay fixture was captured
        FIXTURE.write_text(
            json.dumps(
                {
                    "provenance": "Captured from a real DriftSweepWorkflow run on local Temporal "
                    "with the real worker and local-file Terraform: one idle drifted resource "
                    "checked (plan child started), one busy resource skipped, then the sweep "
                    "was switched off so the workflow completed. Nothing fabricated.",
                    "workflow_id": SWEEP_ID,
                    "history": json.loads(history.to_json()),
                },
                indent=2,
            )
            + "\n"
        )


def test_sweep_history_replays():
    """A captured real run must keep replaying: the workflow stays deterministic."""
    fixture = json.loads(FIXTURE.read_text())
    events_ = fixture["history"]["events"]
    started = events_[0]["workflowExecutionStartedEventAttributes"]
    assert started["workflowType"]["name"] == "DriftSweepWorkflow"
    assert events_[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"
    scheduled = {
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events_
        if e["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
    }
    assert scheduled == {"sweep_drift"}  # no Terraform activity runs in the sweep itself
    assert any(e["eventType"] == "EVENT_TYPE_TIMER_FIRED" for e in events_)
    assert any(
        e["eventType"] == "EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED" for e in events_
    )
    history = WorkflowHistory.from_json(fixture["workflow_id"], fixture["history"])
    asyncio.run(
        Replayer(workflows=[DriftSweepWorkflow, OperationPhaseWorkflow]).replay_workflow(history)
    )


def test_worker_starts_one_sweep_only_when_enabled(monkeypatch):
    from app.worker import start_drift_sweep

    async def scenario():
        async with temporal_api() as (_api, client):
            monkeypatch.setattr(settings, "drift_sweep_minutes", None)
            await start_drift_sweep(client)
            handle = client.get_workflow_handle(SWEEP_ID)
            with pytest.raises(Exception, match="not found|NotFound"):
                await handle.describe()
            monkeypatch.setattr(settings, "drift_sweep_minutes", 5)
            await start_drift_sweep(client)
            await start_drift_sweep(client)  # a second worker reuses the running one
            first = await handle.describe()
            assert first.status is not None and first.status.name == "RUNNING"
            await handle.terminate()

    asyncio.run(scenario())


def test_drift_check_needs_deploy_rights_not_just_visibility(placed, monkeypatch):
    """A check runs Terraform with the landing zone's placement, so it needs the same rights as
    execute/discard: a member who can still see the resource but may no longer deploy there gets
    403 and nothing is accepted."""
    import yaml

    from app.settings import settings
    from tests.test_operation_policy import request as placed_request

    op = placed_request(placed, "drift-rights").json()
    with ledger.connect() as con:  # stand-in for an applied resource; no Terraform needed here
        body = json.loads(
            con.execute("SELECT body FROM resources WHERE id=?", (op["resource_id"],)).fetchone()[0]
        )
        body["state"] = "ready"
        con.execute("UPDATE resources SET body=? WHERE id=?", (json.dumps(body), op["resource_id"]))
        con.execute("UPDATE operations SET state='succeeded' WHERE id=?", (op["id"],))
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["business_units"]["finance"]["environments"]["dev"]["groups"] = ["release-only"]
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    url = f"/resources/{op['resource_id']}"
    assert placed.get(url).status_code == 200
    with ledger.connect() as con:
        before = con.execute("SELECT count(*) FROM operations").fetchone()[0]
    refused = placed.post(f"{url}/drift-check", headers={"Idempotency-Key": "drift-rights-check"})
    assert refused.status_code == 403, refused.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == before


def test_sweep_accepts_only_what_the_outstanding_checks_leave_of_the_batch(monkeypatch):
    monkeypatch.setattr(settings, "drift_sweep_minutes", 1)
    monkeypatch.setattr(drift, "BATCH", 5)
    seen = []
    monkeypatch.setattr(ledger, "outstanding_count", lambda actor, action: seen_out[0])
    monkeypatch.setattr(ledger, "drift_candidates", lambda cutoff, limit: seen.append(limit) or [])
    seen_out = [3]
    drift.sweep_drift()
    assert seen == [2]  # 5 - 3 outstanding
    seen_out[0] = 5
    result = drift.sweep_drift()
    assert seen == [2]  # skipped the cycle entirely
    assert result["sleep_seconds"] == float(min(60, drift.BACKLOG_SLEEP))


def test_unplaceable_resource_is_stamped_and_refused_once_per_interval(
    promo, recorded_dispatcher, monkeypatch
):
    import yaml

    from tests.test_promotion import deploy

    rid = deploy(promo, recorded_dispatcher)
    age(rid)
    units = yaml.safe_load(settings.tenants_yaml)
    del units["business_units"]["finance"]["environments"]["dev"]
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))
    monkeypatch.setattr(settings, "drift_sweep_minutes", 1)
    for _ in range(3):
        drift.sweep_drift()
    with ledger.connect() as con:
        refused = con.execute(
            "SELECT count(*) FROM events WHERE actor=? AND outcome='refused'", (drift.SYSTEM_ACTOR,)
        ).fetchone()[0]
    assert refused == 1
    stamped = ledger.resource(rid)
    assert stamped["drift_status"] == "unknown" and stamped["drift_checked_at"]
    assert stamped["drift_error"]
    assert [r["id"] for r in ledger.drift_candidates("9999", 20)] == [rid]  # due again later only


def test_check_without_state_on_this_worker_is_unknown_never_in_sync(
    agent_client, recorded_dispatcher
):
    import shutil

    done = _deploy(agent_client, recorded_dispatcher, "dc-nostate")
    rid = done["resource_id"]
    assert ledger.resource(rid)["managed_objects"]
    shutil.rmtree(terraform.deployment_dir(rid) / "work")  # a fresh worker: local state is gone
    failed = run_check(agent_client, recorded_dispatcher, rid, "dc-nostate-check")
    assert failed["state"] == "failed", failed
    assert "state is not available on this worker" in failed["error"]
    now = resource(agent_client, rid)
    assert now["drift_status"] == "unknown" and now["drift"] is None


def test_forgotten_resources_are_not_planned_objects():
    from app.operation_activities import planned_objects_of

    def change(address, actions):
        return {"address": address, "type": "t", "change": {"actions": actions}}

    raw = {
        "resource_changes": [
            change("a", ["no-op"]),
            change("b", ["delete"]),
            change("c", ["forget"]),
            change("d", ["delete", "create"]),
            change("e", ["create"]),
        ]
    }
    assert [o["address"] for o in planned_objects_of(raw)] == ["a", "d", "e"]
