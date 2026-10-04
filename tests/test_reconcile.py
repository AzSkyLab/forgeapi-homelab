"""Operator reconciliation resolves an `uncertain` operation and releases its reservation."""

import json
from pathlib import Path

import pytest
import yaml

from app import ledger
from app.client import Client, ClientError, main
from app.contracts import Discovery
from app.main import app, caller_context
from app.settings import settings
from app.tenants import Caller
from tests.test_client import http_client as http_client
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import INTENT, submit
from tests.test_operations import agent_client as agent_client

OLD = Path(__file__).parent / "fixtures" / "v1"


def refused_count():
    with ledger.connect() as con:
        return con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0]


def submit_op(client, key, prefix="", **updates):
    return client.post(
        f"{prefix}/operations", json={**INTENT, **updates}, headers={"Idempotency-Key": key}
    )


def apply_uncertain(submitter, client, key, prefix="", **updates):
    """An operation that reached apply before the worker's status write was lost."""
    op = submitter(client, key, prefix, **updates).json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="1" * 64)
    ledger.execute(op["id"], "1" * 64, "local")
    ledger.claim(op["id"], "apply")
    ledger.interrupted(op["id"], "apply")
    return op


def plan_uncertain(submitter, client, key, prefix="", **updates):
    """An operation that never reached apply before the worker's status write was lost."""
    op = submitter(client, key, prefix, **updates).json()
    ledger.claim(op["id"], "plan")
    ledger.interrupted(op["id"], "plan")
    return op


@pytest.fixture
def operated(placed, monkeypatch):
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["operators"] = ["ops"]
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    return placed


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_apply_phase_uncertain_reconciles_succeeded(agent_client, prefix):
    op = apply_uncertain(submit_op, agent_client, f"rec-ok-{'v1' if prefix else 'root'}", prefix)
    response = agent_client.post(
        op["links"]["reconcile"],
        json={"outcome": "succeeded", "reason": "verified applied in the cloud console"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "succeeded"
    assert body["error"] is None
    assert body["outputs"] is None and body["withheld_outputs"] is None
    assert body["terminal"] is True and body["next_action"] == "done"
    assert body["links"]["self"].startswith(f"{prefix}/operations/" if prefix else "/operations/")
    events = ledger.events(op["id"], 0, 100)
    reconciles = [e for e in events if e["action"] == "operation.reconcile"]
    assert len(reconciles) == 1 and reconciles[0]["outcome"] == "accepted"
    assert [e for e in events if e["action"] == "operation.state"][-1]["outcome"] == "succeeded"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_apply_phase_uncertain_reconciles_failed(agent_client, prefix):
    op = apply_uncertain(submit_op, agent_client, f"rec-fail-{'v1' if prefix else 'root'}", prefix)
    response = agent_client.post(
        op["links"]["reconcile"],
        json={"outcome": "failed", "reason": "cloud console shows nothing applied"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "failed"
    assert body["error"] == "reconciled as failed by an operator"
    assert body["terminal"] is True and body["next_action"] == "inspect_failure"


def test_plan_phase_uncertain_cannot_reconcile_succeeded(agent_client):
    op = plan_uncertain(submit_op, agent_client, "rec-plan-succeed")
    response = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "succeeded", "reason": "no apply ever ran"}
    )
    assert response.status_code == 409, response.text
    assert "never executed" in response.json()["error"]["detail"]


def test_plan_phase_uncertain_reconciles_failed(agent_client):
    op = plan_uncertain(submit_op, agent_client, "rec-plan-fail")
    response = agent_client.post(
        op["links"]["reconcile"],
        json={"outcome": "failed", "reason": "operator confirms nothing was ever applied"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "failed"


def test_reconcile_succeeded_releases_resource_and_marks_it_ready(agent_client):
    op = apply_uncertain(submit_op, agent_client, "rec-release")
    still_busy = submit(agent_client, "rec-release-retry", resource_id=op["resource_id"])
    assert still_busy.status_code == 409

    reconciled = agent_client.post(
        op["links"]["reconcile"],
        json={"outcome": "succeeded", "reason": "confirmed applied in the cloud"},
    ).json()
    assert reconciled["state"] == "succeeded"

    freed = submit(agent_client, "rec-release-after", resource_id=op["resource_id"])
    assert freed.status_code == 202
    resource = agent_client.get(reconciled["links"]["resource"]).json()
    assert resource["state"] == "ready"


def test_reconcile_succeeded_destroy_marks_resource_destroyed(agent_client, recorded_dispatcher):
    created = submit(agent_client, "rec-destroy-seed").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(created["links"]["self"]).json()
    agent_client.post(planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()
    assert agent_client.get(created["links"]["self"]).json()["state"] == "succeeded"

    op = apply_uncertain(
        submit_op, agent_client, "rec-destroy", action="destroy", resource_id=created["resource_id"]
    )
    reconciled = agent_client.post(
        op["links"]["reconcile"],
        json={"outcome": "succeeded", "reason": "confirmed destroyed in the cloud"},
    ).json()
    assert reconciled["state"] == "succeeded"
    resource = agent_client.get(reconciled["links"]["resource"]).json()
    assert resource["state"] == "destroyed"


def test_non_operator_who_can_see_the_operation_gets_403_and_refused(operated):
    op = apply_uncertain(placed_request, operated, "rec-403")
    before = refused_count()
    response = operated.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": "no evidence of apply"}
    )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["detail"] == "operator role required"
    assert refused_count() == before + 1


def test_non_operator_who_cannot_see_the_operation_gets_404(operated, monkeypatch):
    op = apply_uncertain(placed_request, operated, "rec-404")
    monkeypatch.setattr(settings, "dev_groups", "hr")
    response = operated.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": "no evidence of apply"}
    )
    assert response.status_code == 404, response.text


def test_operator_from_another_unit_can_reconcile(operated, monkeypatch):
    op = apply_uncertain(placed_request, operated, "rec-op-other")
    monkeypatch.setattr(settings, "dev_groups", "hr,ops")
    response = operated.post(
        op["links"]["reconcile"],
        json={"outcome": "succeeded", "reason": "verified by an operator in another unit"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "succeeded"


def test_tenancy_disabled_other_actor_gets_404_owner_allowed(agent_client):
    op = apply_uncertain(submit_op, agent_client, "rec-owner")
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        other = agent_client.post(
            op["links"]["reconcile"], json={"outcome": "failed", "reason": "not mine"}
        )
        assert other.status_code == 404
    finally:
        app.dependency_overrides.clear()
    owner = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": "owner can reconcile"}
    )
    assert owner.status_code == 200, owner.text


def test_reconcile_of_non_uncertain_operation_is_409_and_refused(agent_client):
    op = submit_op(agent_client, "rec-not-uncertain").json()
    before = refused_count()
    response = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": "premature"}
    )
    assert response.status_code == 409, response.text
    assert refused_count() == before + 1


def test_reconcile_repeat_same_outcome_idempotent_different_outcome_conflicts(agent_client):
    op = apply_uncertain(submit_op, agent_client, "rec-repeat")
    first = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": "first look"}
    ).json()
    repeat = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": "second look, same call"}
    )
    assert repeat.status_code == 200
    assert repeat.json() == first
    events = ledger.events(op["id"], 0, 100)
    reconciles = [e for e in events if e["action"] == "operation.reconcile"]
    assert len(reconciles) == 1

    conflict = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "succeeded", "reason": "changed my mind"}
    )
    assert conflict.status_code == 409


def test_reconcile_reason_never_returned_or_audited(agent_client):
    op = apply_uncertain(submit_op, agent_client, "rec-reason")
    secret = "SECRET-RECONCILE-REASON-not-for-callers"
    response = agent_client.post(
        op["links"]["reconcile"], json={"outcome": "failed", "reason": secret}
    )
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert "reason" not in response.json()
    status = agent_client.get(op["links"]["self"])
    assert secret not in status.text
    assert "reason" not in status.json()
    for event in ledger.events(op["id"], 0, 100):
        assert secret not in json.dumps(event)


def test_late_worker_finish_cannot_overwrite_reconciled_state(agent_client):
    op = submit_op(agent_client, "rec-late").json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="2" * 64)
    ledger.execute(op["id"], "2" * 64, "local")
    running = ledger.claim(op["id"], "apply")
    ledger.interrupted(op["id"], "apply")
    reconciled = agent_client.post(
        op["links"]["reconcile"],
        json={"outcome": "succeeded", "reason": "confirmed applied"},
    )
    assert reconciled.status_code == 200, reconciled.text

    ledger.finish(running, "succeeded", outputs={"should": "not-apply"})
    stored = ledger.get(op["id"])
    assert stored["state"] == "succeeded"
    assert stored["outputs"] is None


def test_reconcile_capability_advertised(agent_client):
    assert agent_client.get("/agent").json()["capabilities"]["operator_reconciliation"] is True
    assert agent_client.get("/v1/agent").json()["capabilities"]["operator_reconciliation"] is True


def test_client_reconcile_refuses_without_capability(monkeypatch):
    old = json.loads((OLD / "old_discovery.json").read_text())
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: old)
    with pytest.raises(ClientError, match="reconcil"):
        client.reconcile("op_" + "a" * 32, "failed", "no evidence")

    client._discovery = Discovery.model_validate(
        {**old, "capabilities": {"operator_reconciliation": False}}
    )
    with pytest.raises(ClientError, match="reconcil"):
        client.reconcile("op_" + "a" * 32, "failed", "no evidence")


def test_client_reconcile_succeeds_when_advertised(http_client, recorded_dispatcher):
    client = http_client
    discovery = client.discover()
    assert discovery["capabilities"]["operator_reconciliation"] is True
    submitted = client.submit(INTENT, "client-reconcile")
    op_id = submitted["id"]
    ledger.finish(ledger.claim(op_id, "plan"), "planned", plan_digest="3" * 64)
    ledger.execute(op_id, "3" * 64, "local")
    ledger.claim(op_id, "apply")
    ledger.interrupted(op_id, "apply")
    reconciled = client.reconcile(op_id, "succeeded", "confirmed applied via the cloud portal")
    assert reconciled["state"] == "succeeded"


def test_cli_reconcile_invokes_client(monkeypatch, capsys):
    result = {"id": "op_" + "a" * 32, "state": "succeeded"}
    called = []

    def reconcile(self, operation_id, outcome, reason):
        called.append((operation_id, outcome, reason))
        return result

    monkeypatch.setattr(Client, "reconcile", reconcile)
    assert (
        main(
            [
                "--url",
                "http://localhost:8080",
                "reconcile",
                "op_" + "a" * 32,
                "--outcome",
                "succeeded",
                "--reason",
                "verified",
            ]
        )
        == 0
    )
    assert called == [("op_" + "a" * 32, "succeeded", "verified")]
    assert json.loads(capsys.readouterr().out) == result
