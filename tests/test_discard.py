"""Discarding a reviewed plan releases its resource and budget reservation without applying."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

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


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_discard_planned_operation_is_failed_once_and_idempotent(
    agent_client, recorded_dispatcher, prefix
):
    submitted = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"disc-{'v1' if prefix else 'root'}"},
    ).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(f"{prefix}/operations/{submitted['id']}").json()
    assert planned["state"] == "planned"

    first = agent_client.post(planned["links"]["discard"])
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["state"] == "failed"
    assert body["error"] == "plan discarded before execution"
    assert body["links"]["self"].startswith(f"{prefix}/operations/" if prefix else "/operations/")
    events = ledger.events(submitted["id"], 0, 100)
    discards = [e for e in events if e["action"] == "operation.discard"]
    assert len(discards) == 1 and discards[0]["outcome"] == "accepted"

    repeat = agent_client.post(planned["links"]["discard"])
    assert repeat.status_code == 200
    assert repeat.json() == body
    events_after = ledger.events(submitted["id"], 0, 100)
    assert len([e for e in events_after if e["action"] == "operation.discard"]) == 1


def test_discard_releases_resource_for_new_intent(agent_client, recorded_dispatcher):
    op = submit(agent_client, "disc-busy").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    still_busy = submit(agent_client, "disc-busy-retry", resource_id=op["resource_id"])
    assert still_busy.status_code == 409

    discarded = agent_client.post(planned["links"]["discard"])
    assert discarded.status_code == 200

    freed = submit(agent_client, "disc-busy-after", resource_id=op["resource_id"])
    assert freed.status_code == 202


def test_discard_restores_budget_reservation_for_a_later_resource(placed, recorded_dispatcher):
    first = placed_request(placed, "disc-budget-1")
    assert first.status_code == 202, first.text
    op = first.json()
    assert recorded_dispatcher.run_next()
    planned = placed.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"

    blocked = placed_request(placed, "disc-budget-2")
    assert blocked.status_code == 403

    discard = placed.post(planned["links"]["discard"])
    assert discard.status_code == 200, discard.text

    retried = placed_request(placed, "disc-budget-3")
    assert retried.status_code == 202, retried.text


def test_execute_after_discard_is_409(agent_client, recorded_dispatcher):
    op = submit(agent_client, "disc-then-execute").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    assert agent_client.post(planned["links"]["discard"]).status_code == 200
    execute = agent_client.post(
        planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
    )
    assert execute.status_code == 409


def test_discard_of_queued_operation_is_409_and_refused(agent_client):
    queued = submit(agent_client, "disc-queued").json()
    before = refused_count()
    response = agent_client.post(queued["links"]["discard"])
    assert response.status_code == 409
    assert refused_count() == before + 1


def test_discard_of_succeeded_operation_is_409_and_refused(agent_client, recorded_dispatcher):
    op = submit(agent_client, "disc-succeeded").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    agent_client.post(planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()
    done = agent_client.get(op["links"]["self"]).json()
    assert done["state"] == "succeeded"
    before = refused_count()
    response = agent_client.post(done["links"]["discard"])
    assert response.status_code == 409
    assert refused_count() == before + 1


def test_other_callers_cannot_discard(agent_client):
    op = submit(agent_client, "disc-other-caller").json()
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        assert agent_client.post(op["links"]["discard"]).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_other_business_unit_gets_404_like_execute(placed, monkeypatch):
    accepted = placed_request(placed, "disc-unit").json()
    monkeypatch.setattr(settings, "dev_groups", "hr")
    assert placed.post(accepted["links"]["discard"]).status_code == 404


def test_revoked_deploy_rights_get_403_like_execute(placed, monkeypatch):
    op = placed_request(placed, "disc-revoked").json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="d" * 64)
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["business_units"]["finance"]["environments"]["dev"]["groups"] = ["release-only"]
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    assert placed.get(op["links"]["self"]).status_code == 200
    assert placed.post(op["links"]["discard"]).status_code == 403


def test_discard_capability_advertised(agent_client):
    assert agent_client.get("/agent").json()["capabilities"]["discard_planned_operation"] is True
    assert agent_client.get("/v1/agent").json()["capabilities"]["discard_planned_operation"] is True


def test_concurrent_discard_and_execute_leave_exactly_one_winner(agent_client):
    op = submit(agent_client, "disc-race").json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="c" * 64)
    with ThreadPoolExecutor(max_workers=2) as pool:
        discard_future = pool.submit(lambda: TestClient(app).post(op["links"]["discard"]))
        execute_future = pool.submit(
            lambda: TestClient(app).post(
                op["links"]["execute"], json={"plan_digest": "c" * 64}
            )
        )
        discard_result = discard_future.result()
        execute_result = execute_future.result()
    outcomes = [discard_result.status_code, execute_result.status_code]
    assert 409 in outcomes
    winners = [code for code in outcomes if code != 409]
    assert len(winners) == 1
    assert ledger.get(op["id"])["state"] in {"failed", "apply_queued"}


def test_client_discard_refuses_without_capability(monkeypatch):
    old = json.loads((OLD / "old_discovery.json").read_text())
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: old)
    with pytest.raises(ClientError, match="discard"):
        client.discard("op_" + "a" * 32)

    client._discovery = Discovery.model_validate(
        {**old, "capabilities": {"discard_planned_operation": False}}
    )
    with pytest.raises(ClientError, match="discard"):
        client.discard("op_" + "a" * 32)


def test_client_discard_succeeds_when_advertised(http_client, recorded_dispatcher):
    client = http_client
    discovery = client.discover()
    assert discovery["capabilities"]["discard_planned_operation"] is True
    submitted = client.submit(INTENT, "client-discard")
    op_id = submitted["id"]
    assert recorded_dispatcher.run_next()
    planned = client.status(op_id)
    assert planned["state"] == "planned"
    discarded = client.discard(op_id)
    assert discarded["state"] == "failed"
    assert discarded["resource_id"] == planned["resource_id"]


def test_cli_discard_invokes_client(monkeypatch, capsys):
    result = {"id": "op_" + "a" * 32, "state": "failed"}
    called = []

    def discard(self, operation_id):
        called.append(operation_id)
        return result

    monkeypatch.setattr(Client, "discard", discard)
    assert main(["--url", "http://localhost:8080", "discard", "op_" + "a" * 32]) == 0
    assert called == ["op_" + "a" * 32]
    assert json.loads(capsys.readouterr().out) == result


def _plan_file(op):
    from app import terraform

    path = terraform.deployment_dir(op["resource_id"]) / "work" / "tfplan"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_discard_removes_saved_plan_file_and_execute_stays_refused(
    agent_client, recorded_dispatcher
):
    op = submit(agent_client, "disc-file").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    path = _plan_file(op)
    path.write_bytes(b"plan")
    assert agent_client.post(planned["links"]["discard"]).status_code == 200
    assert not path.exists()
    refused = agent_client.post(
        planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
    )
    assert refused.status_code == 409


def test_discard_without_plan_file_leaves_nothing(agent_client, recorded_dispatcher):
    op = submit(agent_client, "disc-nofile").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    path = _plan_file(op)
    path.unlink(missing_ok=True)
    assert agent_client.post(planned["links"]["discard"]).status_code == 200
    assert not path.exists()
