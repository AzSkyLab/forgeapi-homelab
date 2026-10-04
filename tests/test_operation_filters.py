"""`resource_id`/`state` filters on `GET /operations` compose with pagination and never widen
visibility: they are additional AND clauses inside the same actor/unit condition as today."""

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app, caller_context
from app.settings import settings
from app.tenants import Caller
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import INTENT


@pytest.fixture
def agent_client(recorded_dispatcher):
    return TestClient(app)


def submit(client, key, **updates):
    response = client.post(
        "/operations", json={**INTENT, **updates}, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return response.json()


def finish_as_succeeded(operation_id, digest="a" * 64):
    """Drive an operation straight to `succeeded` through the same ledger API the Temporal
    worker uses, without running Terraform; frees the resource for a second operation."""
    ledger.finish(ledger.claim(operation_id, "plan"), "planned", plan_digest=digest)
    ledger.execute(operation_id, digest, "local")
    ledger.finish(ledger.claim(operation_id, "apply"), "succeeded", outputs={}, withheld_outputs=[])


@pytest.mark.parametrize("prefix", ("", "/v1"))
def test_resource_id_filter_returns_only_that_resource(agent_client, prefix):
    first = submit(agent_client, "first")
    second = submit(agent_client, "second")
    finish_as_succeeded(first["id"])
    again = submit(agent_client, "again", resource_id=first["resource_id"])

    page = agent_client.get(
        f"{prefix}/operations", params={"resource_id": first["resource_id"]}
    ).json()
    assert {item["id"] for item in page["items"]} == {first["id"], again["id"]}
    assert all(item["resource_id"] == first["resource_id"] for item in page["items"])
    assert second["id"] not in {item["id"] for item in page["items"]}


def test_state_filter_returns_only_matching_state(agent_client):
    queued = submit(agent_client, "queued")
    planned_op = submit(agent_client, "planned")
    ledger.finish(ledger.claim(planned_op["id"], "plan"), "planned", plan_digest="b" * 64)

    only_planned = agent_client.get("/operations", params={"state": "planned"}).json()
    assert [item["id"] for item in only_planned["items"]] == [planned_op["id"]]
    assert only_planned["items"][0]["state"] == "planned"

    only_queued = agent_client.get("/operations", params={"state": "queued"}).json()
    assert [item["id"] for item in only_queued["items"]] == [queued["id"]]


def test_both_filters_compose(agent_client):
    op = submit(agent_client, "both")
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="c" * 64)
    other = submit(agent_client, "other-resource")

    matching = agent_client.get(
        "/operations", params={"resource_id": op["resource_id"], "state": "planned"}
    ).json()
    assert [item["id"] for item in matching["items"]] == [op["id"]]

    mismatched = agent_client.get(
        "/operations", params={"resource_id": other["resource_id"], "state": "planned"}
    ).json()
    assert mismatched["items"] == []


def test_cursor_traversal_stays_stable_under_a_filter(agent_client):
    queued_ids = [submit(agent_client, f"queued-{i}")["id"] for i in range(4)]
    noise = submit(agent_client, "noise")
    ledger.finish(ledger.claim(noise["id"], "plan"), "planned", plan_digest="d" * 64)

    first = agent_client.get("/operations", params={"state": "queued", "limit": 2}).json()
    assert [item["id"] for item in first["items"]] == queued_ids[-1:-3:-1]
    assert first["next_before"] == queued_ids[-2]

    seen = [item["id"] for item in first["items"]]
    before = first["next_before"]
    while before:
        page = agent_client.get(
            "/operations", params={"state": "queued", "limit": 2, "before": before}
        ).json()
        seen.extend(item["id"] for item in page["items"])
        before = page["next_before"]
    assert seen == queued_ids[::-1]
    assert noise["id"] not in seen
    assert len(seen) == len(set(seen))


def test_filter_anchor_not_matching_filter_is_404_like_an_invisible_anchor(agent_client):
    planned_op = submit(agent_client, "mismatched-anchor")
    ledger.finish(ledger.claim(planned_op["id"], "plan"), "planned", plan_digest="e" * 64)
    # The anchor exists and is visible, but does not satisfy the active filter.
    response = agent_client.get(
        "/operations", params={"state": "queued", "before": planned_op["id"]}
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_filters_do_not_widen_actor_isolation(agent_client):
    mine = submit(agent_client, "mine")
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        page = agent_client.get(
            "/operations", params={"resource_id": mine["resource_id"]}
        ).json()
        assert page["items"] == []
        anchored = agent_client.get(
            "/operations", params={"state": "queued", "before": mine["id"]}
        )
        assert anchored.status_code == 404
    finally:
        app.dependency_overrides.pop(caller_context, None)


def test_filters_do_not_widen_business_unit_isolation(placed, monkeypatch):
    finance = placed_request(placed, "finance").json()
    monkeypatch.setattr(settings, "dev_groups", "hr")
    hr = placed_request(placed, "hr").json()

    by_resource = placed.get(
        "/operations", params={"resource_id": finance["resource_id"]}
    ).json()
    assert by_resource["items"] == []  # hr caller cannot see finance's resource

    by_state = placed.get("/operations", params={"state": "queued"}).json()
    assert [item["id"] for item in by_state["items"]] == [hr["id"]]

    anchored = placed.get("/operations", params={"before": finance["id"]})
    assert anchored.status_code == 404
    assert anchored.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("prefix", ("", "/v1"))
def test_invalid_filter_values_are_422(agent_client, prefix):
    bad_resource = agent_client.get(
        f"{prefix}/operations", params={"resource_id": "not-a-resource"}
    )
    assert bad_resource.status_code == 422
    assert bad_resource.json()["error"]["code"] == "invalid_request"

    bad_state = agent_client.get(f"{prefix}/operations", params={"state": "bogus-state"})
    assert bad_state.status_code == 422
    assert bad_state.json()["error"]["code"] == "invalid_request"


def test_capability_is_advertised():
    assert TestClient(app).get("/v1/agent").json()["capabilities"]["operation_filters"] is True
