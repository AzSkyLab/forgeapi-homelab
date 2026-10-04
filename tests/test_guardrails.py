"""Plan guardrails: protected resource types and environment-level destroy lock.
See AGENTS.md and app/policy.py `check_guardrails`."""

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit as legacy_submit

DIGEST = "a" * 64


def submit(client, key, **updates):
    body = {
        "pattern": "demo",
        "version": "v1.0.0",
        "environment": "dev",
        "inputs": {"filename": "agent.txt", "content": "hello"},
        **updates,
    }
    return client.post("/operations", json=body, headers={"Idempotency-Key": key})


def refused_count():
    with ledger.connect() as con:
        return con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0]


def operation_count():
    with ledger.connect() as con:
        return con.execute("SELECT count(*) FROM operations").fetchone()[0]


def plan(op_id, changes, digest=DIGEST):
    """Shortcut a reviewed plan onto an operation without running real Terraform."""
    ledger.finish(ledger.claim(op_id, "plan"), "planned", plan_digest=digest, changes=changes)


def deploy_to_succeeded(client, dispatcher, key, environment):
    """Full deploy lifecycle, so the resource is free for a later destroy attempt."""
    op = submit(client, key, environment=environment).json()
    assert dispatcher.run_next()
    planned = client.get(op["links"]["self"]).json()
    client.post(planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert dispatcher.run_next()
    return client.get(op["links"]["self"]).json()


@pytest.fixture
def guarded(monkeypatch, recorded_dispatcher):
    units = {
        "bu": {
            "groups": ["bu"],
            "patterns": ["demo"],
            "environments": {
                "dev": {"subscription_id": "sub-dev"},
                "unlocked": {"subscription_id": "sub-unlocked", "allow_destroy": True},
                "locked": {"subscription_id": "sub-locked", "allow_destroy": False},
                "protected": {
                    "subscription_id": "sub-protected",
                    "protected_resource_types": ["local_file"],
                },
                "protected_other": {
                    "subscription_id": "sub-protected-other",
                    "protected_resource_types": ["azurerm_key_vault"],
                },
            },
        }
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    monkeypatch.setattr(settings, "dev_groups", "bu")
    return TestClient(app)


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"]])
def test_execute_denied_for_delete_and_both_replace_orders_on_protected_type(
    guarded, recorded_dispatcher, actions, prefix
):
    op = guarded.post(
        f"{prefix}/operations",
        json={
            "pattern": "demo",
            "version": "v1.0.0",
            "environment": "protected",
            "inputs": {"filename": "agent.txt", "content": "hello"},
        },
        headers={"Idempotency-Key": f"deny-{'-'.join(actions)}-{'v1' if prefix else 'root'}"},
    ).json()
    plan(op["id"], [{"address": "local_file.this", "type": "local_file", "actions": actions}])
    pending_before = len(recorded_dispatcher.pending)
    before_refused = refused_count()

    response = guarded.post(f"{prefix}/operations/{op['id']}/execute", json={"plan_digest": DIGEST})
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["reason"] == "policy_denied"
    assert error["next_action"] == "revise_intent"
    assert "local_file.this" in error["detail"]
    assert "hello" not in response.text  # addresses only, never input/output values

    assert ledger.get(op["id"])["state"] == "planned"
    assert refused_count() == before_refused + 1
    assert len(recorded_dispatcher.pending) == pending_before  # no apply dispatched

    discarded = guarded.post(f"{prefix}/operations/{op['id']}/discard")
    assert discarded.status_code == 200, discarded.text
    assert discarded.json()["state"] == "failed"


@pytest.mark.parametrize("actions", [["create"], ["update"]])
def test_execute_allowed_for_create_and_update_on_protected_type(
    guarded, recorded_dispatcher, actions
):
    op = submit(guarded, f"allow-{actions[0]}", environment="protected").json()
    plan(op["id"], [{"address": "local_file.this", "type": "local_file", "actions": actions}])
    response = guarded.post(op["links"]["execute"], json={"plan_digest": DIGEST})
    assert response.status_code == 202, response.text


def test_execute_allowed_for_delete_of_an_unprotected_type(guarded):
    op = submit(guarded, "allow-other-type", environment="protected_other").json()
    plan(op["id"], [{"address": "local_file.this", "type": "local_file", "actions": ["delete"]}])
    response = guarded.post(op["links"]["execute"], json={"plan_digest": DIGEST})
    assert response.status_code == 202, response.text


def test_environment_without_protected_types_behaves_as_before(guarded):
    op = submit(guarded, "no-protection", environment="dev").json()
    plan(op["id"], [{"address": "local_file.this", "type": "local_file", "actions": ["delete"]}])
    response = guarded.post(op["links"]["execute"], json={"plan_digest": DIGEST})
    assert response.status_code == 202, response.text


def test_destroy_denied_at_submit_when_allow_destroy_false(guarded, recorded_dispatcher):
    created = deploy_to_succeeded(guarded, recorded_dispatcher, "lock-create", "locked")
    before_ops, before_refused = operation_count(), refused_count()

    destroy = guarded.post(
        "/operations",
        json={"pattern": "demo", "action": "destroy", "resource_id": created["resource_id"]},
        headers={"Idempotency-Key": "lock-destroy"},
    )
    assert destroy.status_code == 403, destroy.text
    error = destroy.json()["error"]
    assert error["reason"] == "policy_denied"
    assert error["next_action"] == "revise_intent"

    assert operation_count() == before_ops  # no operation created
    assert refused_count() == before_refused + 1


@pytest.mark.parametrize("environment", ["dev", "unlocked"])
def test_destroy_allowed_when_allow_destroy_true_or_absent(
    guarded, recorded_dispatcher, environment
):
    created = deploy_to_succeeded(guarded, recorded_dispatcher, f"free-{environment}", environment)
    destroy = guarded.post(
        "/operations",
        json={"pattern": "demo", "action": "destroy", "resource_id": created["resource_id"]},
        headers={"Idempotency-Key": f"free-destroy-{environment}"},
    )
    assert destroy.status_code == 202, destroy.text


def test_tenancy_disabled_unaffected_for_protected_type_plan(agent_client, recorded_dispatcher):
    op = legacy_submit(agent_client, "no-tenancy-plan").json()
    plan(op["id"], [{"address": "local_file.this", "type": "local_file", "actions": ["delete"]}])
    response = agent_client.post(op["links"]["execute"], json={"plan_digest": DIGEST})
    assert response.status_code == 202, response.text


def test_tenancy_disabled_unaffected_for_destroy(agent_client, recorded_dispatcher):
    op = legacy_submit(agent_client, "no-tenancy-destroy-seed").json()
    assert recorded_dispatcher.run_next()  # plan
    planned = agent_client.get(op["links"]["self"]).json()
    agent_client.post(planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()  # apply
    destroy = legacy_submit(agent_client, "no-tenancy-destroy-go", resource_id=op["resource_id"])
    assert destroy.status_code == 202, destroy.text


def test_plan_guardrails_capability_advertised(agent_client):
    assert agent_client.get("/agent").json()["capabilities"]["plan_guardrails"] is True
    assert agent_client.get("/v1/agent").json()["capabilities"]["plan_guardrails"] is True


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_discovery_advertises_guardrails_per_deployable_environment(guarded, prefix):
    discovery = guarded.get(f"{prefix}/agent").json()
    assert discovery["capabilities"]["guardrail_discovery"] is True
    (unit,) = discovery["business_units"]
    assert set(unit["guardrails"]) == set(unit["environments"])
    assert unit["guardrails"]["dev"] == {"allow_destroy": True, "protected_resource_types": []}
    assert unit["guardrails"]["locked"]["allow_destroy"] is False
    assert unit["guardrails"]["protected"]["protected_resource_types"] == ["local_file"]
    assert "sub-protected" not in str(discovery)
