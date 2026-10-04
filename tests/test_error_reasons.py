"""Additive `reason` sub-codes on error envelopes. `code` and status stay exactly as today."""

import json

import pytest

from app import cloud_targets, ledger, policy
from app.contracts import OperationError
from tests.conftest import git
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import INTENT
from tests.test_operations import agent_client as agent_client

DIGEST = "9" * 64


def tag(prefix):
    return "v1" if prefix else "root"


def fake_succeed(op):
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest=DIGEST, changes=[])
    ledger.execute(op["id"], DIGEST, "local")
    ledger.finish(ledger.claim(op["id"], "apply"), "succeeded", outputs={}, withheld_outputs=[])


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_resource_busy_reason_carries_blocking_operation(agent_client, prefix):
    first = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-busy-1-{tag(prefix)}"},
    ).json()
    busy = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "resource_id": first["resource_id"]},
        headers={"Idempotency-Key": f"err-busy-2-{tag(prefix)}"},
    )
    assert busy.status_code == 409, busy.text
    error = busy.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "resource_busy"
    assert error["next_action"] == "inspect_blocking_operation"
    assert error["operation_id"] == first["id"]
    assert error["status_url"] == f"{prefix}/operations/{first['id']}"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_resource_changed_reason(agent_client, prefix, monkeypatch):
    created = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-chg-seed-{tag(prefix)}"},
    ).json()
    fake_succeed(created)  # not busy, so the race below surfaces as resource_changed, not busy
    original_validate = policy.validate

    def drifted(intent, caller):
        resolved, budget = original_validate(intent, caller)
        with ledger.connect() as con:
            row = con.execute(
                "SELECT body FROM resources WHERE id=?", (created["resource_id"],)
            ).fetchone()
            resource = json.loads(row[0])
            resource["operation_id"] = "op_" + "9" * 32
            con.execute(
                "UPDATE resources SET body=? WHERE id=?",
                (json.dumps(resource), created["resource_id"]),
            )
        return resolved, budget

    monkeypatch.setattr(policy, "validate", drifted)
    response = agent_client.post(
        f"{prefix}/operations",
        json={
            **INTENT,
            "resource_id": created["resource_id"],
            "inputs": {"filename": "agent.txt", "content": "drift"},
        },
        headers={"Idempotency-Key": f"err-chg-{tag(prefix)}"},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "resource_changed"
    assert error["next_action"] == "validate_and_resubmit"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_idempotency_key_reused_reason(agent_client, prefix):
    key = f"err-idem-{tag(prefix)}"
    first = agent_client.post(f"{prefix}/operations", json=INTENT, headers={"Idempotency-Key": key})
    assert first.status_code == 202, first.text
    conflict = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "inputs": {"filename": "other.txt", "content": "other"}},
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409, conflict.text
    error = conflict.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "idempotency_key_reused"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_plan_digest_mismatch_reason(agent_client, recorded_dispatcher, prefix):
    op = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-digest-{tag(prefix)}"},
    ).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(f"{prefix}/operations/{op['id']}").json()
    assert planned["state"] == "planned"
    response = agent_client.post(
        f"{prefix}/operations/{op['id']}/execute", json={"plan_digest": "0" * 64}
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "plan_digest_mismatch"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_operation_not_planned_reason(agent_client, prefix):
    op = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-notplanned-{tag(prefix)}"},
    ).json()
    response = agent_client.post(f"{prefix}/operations/{op['id']}/discard")
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "operation_not_planned"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_operation_not_uncertain_reason(agent_client, prefix):
    op = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-notuncertain-{tag(prefix)}"},
    ).json()
    response = agent_client.post(
        f"{prefix}/operations/{op['id']}/reconcile",
        json={"outcome": "failed", "reason": "checking the mechanism"},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "operation_not_uncertain"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_budget_exceeded_reason(placed, prefix):
    first = placed_request(placed, f"err-budget-1-{tag(prefix)}", prefix)
    assert first.status_code == 202, first.text
    second = placed_request(placed, f"err-budget-2-{tag(prefix)}", prefix)
    assert second.status_code == 403, second.text
    error = second.json()["error"]
    assert error["code"] == "permission_denied"
    assert error["reason"] == "budget_exceeded"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_pattern_change_not_supported_reason(agent_client, prefix):
    created = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-pattern-seed-{tag(prefix)}"},
    ).json()
    changed = agent_client.post(
        f"{prefix}/operations",
        json={"pattern": "local-file", "resource_id": created["resource_id"]},
        headers={"Idempotency-Key": f"err-pattern-{tag(prefix)}"},
    )
    assert changed.status_code == 409, changed.text
    error = changed.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "pattern_change_not_supported"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_revision_moved_reason(agent_client, pattern_repo, prefix):
    commit = git(pattern_repo, "rev-parse", "v1.0.0")
    accepted = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "expected_commit": commit},
        headers={"Idempotency-Key": f"err-moved-seed-{tag(prefix)}"},
    )
    assert accepted.status_code == 202, accepted.text
    git(pattern_repo, "tag", "-f", "v1.0.0", "v1.1.0^{commit}")
    moved = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "expected_commit": commit},
        headers={"Idempotency-Key": f"err-moved-{tag(prefix)}"},
    )
    assert moved.status_code == 409, moved.text
    error = moved.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "revision_moved"
    assert error["next_action"] == "describe_and_validate_again"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_resource_destroyed_reason(agent_client, prefix):
    created = agent_client.post(
        f"{prefix}/operations",
        json=INTENT,
        headers={"Idempotency-Key": f"err-destroy-seed-{tag(prefix)}"},
    ).json()
    fake_succeed(created)
    destroy = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "action": "destroy", "resource_id": created["resource_id"]},
        headers={"Idempotency-Key": f"err-destroy-{tag(prefix)}"},
    ).json()
    fake_succeed(destroy)
    again = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "resource_id": created["resource_id"]},
        headers={"Idempotency-Key": f"err-destroy-again-{tag(prefix)}"},
    )
    assert again.status_code == 409, again.text
    error = again.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "resource_destroyed"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_placement_changed_reason(placed, prefix):
    created = placed_request(placed, f"err-place-seed-{tag(prefix)}", prefix).json()
    changed = placed_request(
        placed,
        f"err-place-{tag(prefix)}",
        prefix,
        resource_id=created["resource_id"],
        business_unit="hr",
    )
    assert changed.status_code == 409, changed.text
    error = changed.json()["error"]
    assert error["code"] == "conflict"
    assert error["reason"] == "placement_changed"


def test_cloud_target_changed_reason():
    """`cloud_targets.unchanged` (cloud/target drift, as opposed to a business-unit change)
    carries the same `placement_changed` reason, via `OperationError` rather than the bare
    `TenancyError` it used to raise."""
    with pytest.raises(OperationError) as excinfo:
        cloud_targets.unchanged(
            {"cloud": "aws", "cloud_target": {"aws_account_id": "1", "region": "us-east-1"}},
            "aws",
            {"aws_account_id": "2", "region": "us-east-1"},
        )
    error = excinfo.value
    assert error.status_code == 409
    assert error.detail == "resource cloud placement changed; operator reconciliation required"
    assert error.reason == "placement_changed"
