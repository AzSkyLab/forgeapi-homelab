"""Audit trail: every real action and refusal leaves an event; nothing can be changed."""

import asyncio

import pytest
import yaml

from app import audit, db
from app.main import app
from app.models import State
from app.settings import settings
from tests.test_pipeline import _run_workflow
from tests.test_tenancy import FIN_DEVS, HR_DEVS, as_groups, deploy, tenancy  # noqa: F401


def trail(deployment_id=None):
    events = audit.query(None, None, 500)
    events = [e for e in events if deployment_id is None or e.deployment_id == deployment_id]
    return [(e.action, e.outcome) for e in reversed(events)]  # oldest first


def test_create_records_who_what_where_and_what_the_platform_added(client):
    body = deploy(client, inputs={"name": "x", "api_key": "s3cret"}).json()
    [event] = audit.for_deployment(body["id"], "finance")

    assert (event.action, event.outcome, event.actor) == ("deployment.create", "accepted", "local")
    assert (event.pattern, event.version) == ("sized", "v1.0.0")
    assert (event.business_unit, event.environment) == ("finance", "dev")
    assert event.detail["size"] == "small" and event.detail["estimated_monthly_cost"] == 40.0
    assert event.detail["injected"]["cost_center"] == "CC-1042"
    assert event.detail["inputs"] == {"name": "x", "api_key": "***"}  # sensitive value redacted
    assert "s3cret" not in event.model_dump_json()


def test_refusals_are_recorded_with_the_reason(client, monkeypatch):
    assert deploy(client, environment="prd").status_code == 403  # not in the release group
    assert deploy(client, inputs={"name": "x", "cost_center": "MINE"}).status_code == 422
    deploy(client), deploy(client)
    assert deploy(client).status_code == 403  # over budget

    refused = [e for e in audit.query(["finance"], None, 50) if e.outcome == "refused"]
    assert [e.status for e in reversed(refused)] == [403, 422, 403]
    reasons = [e.detail["reason"] for e in reversed(refused)]
    assert reasons[0] == "you may not deploy to finance/prd"
    assert reasons[2]["message"].startswith("this would exceed the estimated monthly budget")
    assert all(e.business_unit == "finance" and e.actor == "local" for e in refused)


def test_dry_runs_leave_no_trace(client):
    request = {"pattern": "sized", "environment": "dev", "size": "small", "inputs": {"name": "x"}}
    assert client.post("/deployments?dry_run=true", json=request).status_code == 200
    assert (
        client.post("/deployments?dry_run=true", json={**request, "size": "xl"}).status_code == 422
    )
    assert trail() == []


def test_update_retry_and_destroy_are_recorded(client):
    deployment_id = deploy(client).json()["id"]
    db.update(deployment_id, State.succeeded, outputs={})
    assert client.put(f"/deployments/{deployment_id}", json={}).status_code == 202
    assert client.put(f"/deployments/{deployment_id}", json={}).status_code == 409  # now accepted
    db.update(deployment_id, State.failed, error="boom")
    assert client.post(f"/deployments/{deployment_id}/retry").status_code == 202
    db.update(deployment_id, State.succeeded, outputs={})
    assert client.delete(f"/deployments/{deployment_id}").status_code == 202

    assert trail(deployment_id) == [
        ("deployment.create", "accepted"),
        ("deployment.update", "accepted"),
        ("deployment.update", "refused"),
        ("deployment.retry", "accepted"),
        ("deployment.destroy", "accepted"),
    ]
    via_api = client.get(f"/deployments/{deployment_id}/events").json()
    assert [e["action"] for e in via_api][0] == "deployment.destroy"  # newest first


def test_worker_outcomes_are_recorded(monkeypatch):
    from tests.test_tenancy import git_commit

    valid = {"environment": "dev", "business_unit": "f", "cost_center": "c",
             "private_endpoint_subnet_id": "s"}  # fmt: skip
    done = db.create("sized", {"name": "ok"}, "v1.0.0", git_commit(), business_unit="finance",
                     environment="dev", injected=valid)  # fmt: skip
    assert asyncio.run(_run_workflow(done.id)) == "succeeded"
    broken = db.create("sized", {"name": "bad"}, "v1.0.0", git_commit(), business_unit="finance",
                       environment="dev", injected={"environment": "nope"})  # fmt: skip
    assert asyncio.run(_run_workflow(broken.id)) == "failed"

    assert trail(done.id) == [("deployment.state", "succeeded")]
    [failure] = audit.for_deployment(broken.id, "finance")
    assert (failure.outcome, failure.actor) == ("failed", "worker")
    assert "terraform" in failure.detail["error"]


def test_probing_another_units_deployment_is_recorded_for_its_owners(client, monkeypatch):
    deployment_id = deploy(client).json()["id"]
    as_groups(monkeypatch, HR_DEVS)
    assert client.get(f"/deployments/{deployment_id}").status_code == 404
    assert client.delete(f"/deployments/{deployment_id}").status_code == 404
    assert client.get("/events").json() == []  # hr sees nothing of finance's trail

    as_groups(monkeypatch, FIN_DEVS)
    seen = [e for e in client.get("/events").json() if e["action"] == "deployment.access"]
    assert [(e["status"], e["detail"]["wanted_to_change"]) for e in reversed(seen)] == [
        (404, False), (404, True),
    ]  # fmt: skip
    assert client.get("/events?business_unit=hr").status_code == 403


def test_auditors_read_every_units_events_and_nothing_else(client, monkeypatch):
    deployment_id = deploy(client).json()["id"]
    spec = yaml.safe_load(settings.tenants_path.read_text())
    spec["auditors"] = ["grp-audit"]
    settings.tenants_path.write_text(yaml.safe_dump(spec))

    as_groups(monkeypatch, "grp-audit")
    assert [e["business_unit"] for e in client.get("/events").json()] == ["finance"]
    assert client.get("/events?business_unit=hr").json() == []
    assert client.get(f"/deployments/{deployment_id}").status_code == 404  # events only
    assert client.get("/patterns").json() == []


def test_no_audit_no_action(client, monkeypatch, dispatched):
    def broken(_event):
        raise OSError("audit store down")

    monkeypatch.setattr(audit._Sqlite, "insert", broken)
    response = deploy(client)
    assert response.status_code == 503 and "audit trail is unavailable" in response.text
    assert dispatched == []  # nothing was started
    [left] = db.list_for(["finance"])
    assert left.state == State.failed and "audit" in left.error


def test_events_cannot_be_changed_or_deleted_through_the_api():
    methods = {
        method
        for route in app.routes
        if "events" in getattr(route, "path", "")
        for method in route.methods
    }
    assert methods == {"GET"}


@pytest.mark.parametrize("since_future", [True])
def test_since_filter(client, since_future):
    deploy(client)
    assert client.get("/events?since=2999-01-01T00:00:00Z").json() == []
    assert len(client.get("/events?since=2000-01-01T00:00:00Z").json()) == 1
