"""The replacement control plane: intent acceptance, exact-plan execution and durable outcomes."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import ledger, operation_activities
from app.main import app
from app.settings import settings
from tests.conftest import git

INTENT = {
    "pattern": "demo",
    "version": "v1.0.0",
    "inputs": {"filename": "agent.txt", "content": "agent infrastructure"},
}


@pytest.fixture
def agent_client(recorded_dispatcher):
    return TestClient(app)


def submit(client, key="intent-1", **updates):
    return client.post("/operations", json={**INTENT, **updates}, headers={"Idempotency-Key": key})


def test_discovery_and_validation(agent_client):
    assert agent_client.get("/agent").json()["patterns"] == ["demo", "local-file"]
    described = agent_client.get("/patterns/demo").json()
    assert described["input_schema"]["additionalProperties"] is False
    checked = agent_client.post("/intents/validate", json=INTENT)
    assert checked.status_code == 200
    assert not checked.json()["terraform_plan_performed"]
    assert agent_client.get("/operations").json()["items"] == []


def test_annotated_nonversion_tag_does_not_break_default_resolution(
    agent_client, pattern_repo, recorded_dispatcher
):
    git(pattern_repo, "tag", "-a", "stable", "-m", "human alias")
    peeled = git(pattern_repo, "rev-parse", "v1.1.0^{commit}")
    for prefix in ("", "/v1"):
        described = agent_client.get(f"{prefix}/patterns/demo")
        assert described.status_code == 200, described.text
        assert described.json()["versions"] == ["v1.1.0", "v1.0.0"]
        assert described.json()["version"] == "v1.1.0"
        assert described.json()["commit"] == peeled
    body = {key: value for key, value in INTENT.items() if key != "version"}
    responses = [
        agent_client.post(
            f"{prefix}/operations", json=body, headers={"Idempotency-Key": "default-tag"}
        )
        for prefix in ("", "/v1")
    ]
    assert all(response.status_code == 202 for response in responses)
    assert responses[0].json()["id"] == responses[1].json()["id"]
    assert responses[0].json()["version"] == "v1.1.0"
    assert responses[0].json()["commit"] == peeled
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        assert (
            con.execute("SELECT count(*) FROM events WHERE outcome='accepted'").fetchone()[0] == 1
        )
    assert len(recorded_dispatcher.pending) == 1


def test_intent_replay_is_durable_and_conflicting_key_is_refused(agent_client):
    first = submit(agent_client)
    assert first.status_code == 202
    assert first.headers["location"] == first.json()["links"]["self"]
    assert submit(TestClient(app)).json()["id"] == first.json()["id"]
    conflict = submit(agent_client, inputs={"filename": "different.txt", "content": "different"})
    assert conflict.status_code == 409
    assert len(agent_client.get("/operations").json()["items"]) == 1
    with ledger.connect() as con:
        assert (
            con.execute("SELECT count(*) FROM events WHERE outcome='accepted'").fetchone()[0] == 1
        )
        assert con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0] == 1


def test_concurrent_submissions_with_same_key_accept_once(agent_client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: submit(TestClient(app)), range(4)))
    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1


def test_moved_revision_refused_and_replay_does_not_resolve_again(agent_client, pattern_repo):
    commit = git(pattern_repo, "rev-parse", "v1.0.0")
    accepted = submit(agent_client, expected_commit=commit)
    git(pattern_repo, "tag", "-f", "v1.0.0", "v1.1.0^{commit}")
    assert submit(agent_client, expected_commit=commit).json()["id"] == accepted.json()["id"]
    assert submit(agent_client, "new-key", expected_commit=commit).status_code == 409


def test_validation_error_never_echoes_input(agent_client):
    result = submit(agent_client, typo="DO-NOT-ECHO")
    assert result.status_code == 422 and "DO-NOT-ECHO" not in result.text
    assert submit(agent_client, expected_commit="bad-secret").status_code == 422


def test_planning_execution_and_destroy_with_real_terraform(agent_client, recorded_dispatcher):
    op = submit(agent_client).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    assert planned["next_action"] == "execute_with_plan_digest"
    assert planned["changes"][0]["actions"] == ["create"]
    assert not recorded_dispatcher.run_next()  # plan is not applied automatically
    assert (
        agent_client.post(planned["links"]["execute"], json={"plan_digest": "0" * 64}).status_code
        == 409
    )
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    done = agent_client.get(op["links"]["self"]).json()
    assert done["state"] == "succeeded", done
    from pathlib import Path

    assert Path(done["outputs"]["path"]).read_text() == "agent infrastructure"
    assert not recorded_dispatcher.run_next()
    assert (
        agent_client.post(planned["links"]["execute"], json=payload).json()["state"] == "succeeded"
    )
    # A later intent updates the existing resource, using the same Terraform state.
    update = submit(
        agent_client,
        "update",
        resource_id=op["resource_id"],
        inputs={"filename": "agent.txt", "content": "updated"},
    ).json()
    assert recorded_dispatcher.run_next()
    update = agent_client.get(update["links"]["self"]).json()
    agent_client.post(update["links"]["execute"], json={"plan_digest": update["plan_digest"]})
    assert recorded_dispatcher.run_next()
    assert Path(done["outputs"]["path"]).read_text() == "updated"
    cleanup = submit(
        agent_client, "cleanup", action="destroy", resource_id=op["resource_id"]
    ).json()
    assert recorded_dispatcher.run_next()
    cleanup = agent_client.get(cleanup["links"]["self"]).json()
    assert cleanup["changes"][0]["actions"] == ["delete"]
    agent_client.post(cleanup["links"]["execute"], json={"plan_digest": cleanup["plan_digest"]})
    assert recorded_dispatcher.run_next()
    assert agent_client.get(cleanup["links"]["self"]).json()["state"] == "succeeded"
    assert not Path(done["outputs"]["path"]).exists()


def test_tampered_saved_plan_cannot_execute(agent_client, recorded_dispatcher):
    op = submit(agent_client).json()
    recorded_dispatcher.run_next()
    op = ledger.get(op["id"])
    agent_client.post(f"/operations/{op['id']}/execute", json={"plan_digest": op["plan_digest"]})
    operation_activities.plan_path(op).write_bytes(b"tampered")
    recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"


def test_interrupted_execution_is_never_replayed_and_blocks_resource(
    agent_client, recorded_dispatcher
):
    op = submit(agent_client).json()
    claimed = ledger.claim(op["id"], "plan")
    ledger.finish(claimed, "planned", plan_digest="a" * 64)
    ledger.execute(op["id"], "a" * 64, "local")
    assert ledger.claim(op["id"], "apply")["state"] == "applying"
    ledger.interrupted(op["id"], "apply")
    assert ledger.get(op["id"])["state"] == "uncertain"
    assert not recorded_dispatcher.run_next()
    assert submit(agent_client, "replacement", resource_id=op["resource_id"]).status_code == 409
    assert (
        agent_client.post(
            f"/operations/{op['id']}/execute", json={"plan_digest": "a" * 64}
        ).status_code
        == 409
    )


def test_audit_failure_prevents_acceptance(agent_client, monkeypatch):
    import sqlite3

    monkeypatch.setattr(
        ledger, "event", lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError())
    )
    assert submit(agent_client).status_code == 503
    assert agent_client.get("/operations").json()["items"] == []


def test_audit_is_append_only_and_paginated(agent_client):
    import sqlite3

    op = submit(agent_client).json()
    claimed = ledger.claim(op["id"], "plan")
    ledger.finish(claimed, "failed", error="test failure")
    page = agent_client.get(f"/operations/{op['id']}/events?limit=1").json()
    assert page["items"][0]["outcome"] == "accepted"
    assert page["next_after"] is not None
    assert (
        len(
            agent_client.get(f"/operations/{op['id']}/events?after={page['next_after']}").json()[
                "items"
            ]
        )
        == 2
    )
    with pytest.raises(sqlite3.IntegrityError), ledger.connect() as con:
        con.execute("DELETE FROM events")


def test_claim_is_specific_to_operation_and_phase(agent_client):
    one = submit(agent_client, "one").json()
    two = submit(agent_client, "two").json()
    assert ledger.claim(two["id"], "apply") is None
    assert ledger.claim(two["id"], "plan")["id"] == two["id"]
    assert ledger.claim(two["id"], "plan") is None
    assert ledger.get(one["id"])["state"] == "queued"


def test_other_callers_cannot_read_or_execute(agent_client):
    from app.main import caller_context
    from app.tenants import Caller

    op = submit(agent_client).json()
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        assert agent_client.get(op["links"]["self"]).status_code == 404
        assert agent_client.get(op["links"]["events"]).status_code == 404
        assert (
            agent_client.post(op["links"]["execute"], json={"plan_digest": "a" * 64}).status_code
            == 404
        )
        assert agent_client.get("/operations").json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_table_backend_fails_explicitly(agent_client, monkeypatch):
    monkeypatch.setattr(settings, "db_backend", "table")
    result = submit(agent_client)
    assert result.status_code == 503


def test_sensitive_outputs_and_subscription_ids_never_enter_ledger_or_logs(
    agent_client, pattern_repo, monkeypatch, recorded_dispatcher
):
    from app import terraform

    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + """
output "private_value" {
  value = "sensitive-output-marker"
  sensitive = true
}
output "arm_reference" { value = "/subscriptions/hidden-subscription/resourceGroups/test" }
"""
    )
    git(pattern_repo, "commit", "-qam", "sensitive outputs")
    git(pattern_repo, "tag", "v1.3.0")
    monkeypatch.setattr(settings, "azure_subscription_id", "hidden-subscription")
    op = submit(agent_client, version="v1.3.0").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    agent_client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()
    result = agent_client.get(op["links"]["self"])
    assert result.json()["state"] == "succeeded"
    assert set(result.json()["withheld_outputs"]) == {"private_value", "arm_reference"}
    assert "sensitive-output-marker" not in result.text
    assert "hidden-subscription" not in result.text
    assert b"sensitive-output-marker" not in (settings.data_dir / "operations.sqlite").read_bytes()
    log = terraform.log_path(op["resource_id"]).read_text()
    assert "sensitive-output-marker" not in log and "hidden-subscription" not in log
    assert "exit_code=0" in log


def test_apply_error_is_uncertain_and_not_automatically_retried(
    agent_client, monkeypatch, recorded_dispatcher
):
    from app import terraform

    op = submit(agent_client).json()
    recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    agent_client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    calls = []

    def failed(*args, **kwargs):
        calls.append((args, kwargs))
        raise terraform.TerraformError("raw-auth-error-secret")

    monkeypatch.setattr(terraform, "_run", failed)
    recorded_dispatcher.run_next()
    result = agent_client.get(op["links"]["self"])
    assert result.json()["state"] == "uncertain"
    assert result.json()["next_action"] == "reconcile_with_operator"
    assert "raw-auth-error-secret" not in result.text
    assert not recorded_dispatcher.run_next()
    assert len(calls) == 1 and calls[0][1]["_unlock_once"] is False


def test_late_activity_result_cannot_overwrite_timeout(agent_client):
    op = submit(agent_client).json()
    running = ledger.claim(op["id"], "plan")
    ledger.interrupted(op["id"], "plan")
    ledger.finish(running, "planned", plan_digest="e" * 64)
    ledger.interrupted(op["id"], "plan")
    assert ledger.get(op["id"])["state"] == "uncertain"
    assert len([e for e in ledger.events(op["id"], 0, 100) if e["outcome"] == "uncertain"]) == 1


def test_execute_requests_racing_queue_only_one_apply(agent_client):
    op = submit(agent_client).json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="f" * 64)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda _: TestClient(app).post(
                    op["links"]["execute"], json={"plan_digest": "f" * 64}
                ),
                range(4),
            )
        )
    assert all(r.status_code == 202 for r in responses)
    assert ledger.claim(op["id"], "apply")["state"] == "applying"
    assert ledger.claim(op["id"], "apply") is None
    events = ledger.events(op["id"], 0, 100)
    assert len([e for e in events if e["action"] == "operation.execute"]) == 1


def test_openapi_exposes_typed_http_operation_contract(agent_client):
    document = agent_client.get("/openapi.json").json()
    submit = document["paths"]["/operations"]["post"]
    assert submit["operationId"] == "submit_intent"
    assert any(p["name"] == "Idempotency-Key" and p["required"] for p in submit["parameters"])
    response = submit["responses"]["202"]["content"]["application/json"]["schema"]
    assert response["$ref"] == "#/components/schemas/Operation"
    state = document["components"]["schemas"]["Operation"]["properties"]["state"]
    assert "uncertain" in state["enum"] and "planned" in state["enum"]
    assert document["components"]["schemas"]["Intent"]["additionalProperties"] is False
    assert "/mcp" not in document["paths"]
