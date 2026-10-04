"""Stored budget amounts must be trustworthy before admitting a new operation."""

import json
import sqlite3

import pytest

from app import db, ledger
from app.settings import settings
from tests.test_operation_policy import placed as _placed_fixture
from tests.test_operation_policy import request

SECRET = "budget-accounting-secret"


@pytest.fixture
def placed(tmp_path, monkeypatch, pattern_repo, recorded_dispatcher):
    return _placed_fixture.__wrapped__(tmp_path, monkeypatch, pattern_repo, recorded_dispatcher)


def rows():
    with ledger.connect() as con:
        return (
            list(con.execute("SELECT id, body FROM operations ORDER BY id")),
            list(con.execute("SELECT id, body FROM resources ORDER BY id")),
            [r[0] for r in con.execute("SELECT outcome FROM events ORDER BY seq")],
        )


def seed_resource(number, cost, *, unit="finance", environment="dev", state="failed"):
    resource = {
        "id": f"res_{number:032x}",
        "business_unit": unit,
        "environment": environment,
        "state": state,
        "estimated_monthly_cost": cost,
    }
    with ledger.connect() as con:
        con.execute("INSERT INTO resources VALUES (?, ?)", (resource["id"], json.dumps(resource)))


def assert_refused(client, recorded_dispatcher, *, resource_id=None, validation_status=200):
    body = {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": "dev",
        "inputs": {"filename": "new.txt", "content": SECRET},
    }
    if resource_id:
        body["resource_id"] = resource_id
    before = rows()
    dispatched = list(recorded_dispatcher.pending)
    validation = client.post("/v1/intents/validate", json=body)
    assert validation.status_code == validation_status, validation.text
    if validation_status == 503:
        assert validation.json()["error"]["code"] == "service_unavailable"
    else:
        assert validation.json()["valid"] is True
    assert rows() == before
    submission = client.post(
        "/v1/operations", json=body, headers={"Idempotency-Key": "corrupt-budget"}
    )
    assert submission.status_code == 503, submission.text
    assert submission.json()["error"]["code"] == "service_unavailable"
    assert SECRET not in validation.text + submission.text
    after = rows()
    assert after[:2] == before[:2]
    assert after[2] == before[2] + ["refused"]
    assert list(recorded_dispatcher.pending) == dispatched
    assert SECRET.encode() not in (settings.data_dir / "operations.sqlite").read_bytes()


@pytest.mark.parametrize(
    "costs", [[float("nan")], [-100], [-100, 100], [True], ["bad"], [1e308, 1e308]]
)
def test_corrupt_matching_resource_blocks_admission(placed, recorded_dispatcher, costs):
    for number, cost in enumerate(costs, 1):
        seed_resource(number, cost)
    assert_refused(placed, recorded_dispatcher)


def test_corrupt_previous_reservation_blocks_update_but_not_destroy(placed, recorded_dispatcher):
    original = request(placed, "original")
    assert original.status_code == 202
    operation = original.json()
    ledger.finish(ledger.claim(operation["id"], "plan"), "failed")
    with ledger.connect() as con:
        row = con.execute(
            "SELECT body FROM resources WHERE id=?", (operation["resource_id"],)
        ).fetchone()
        resource = json.loads(row[0])
        resource["estimated_monthly_cost"] = -100
        con.execute(
            "UPDATE resources SET body=? WHERE id=?",
            (json.dumps(resource), operation["resource_id"]),
        )
    before_replay = rows()
    dispatched = list(recorded_dispatcher.pending)
    replayed = request(placed, "original")
    assert replayed.status_code == 202
    assert replayed.json()["id"] == operation["id"]
    assert rows() == before_replay
    assert list(recorded_dispatcher.pending) == dispatched
    assert_refused(placed, recorded_dispatcher, resource_id=operation["resource_id"])
    destroyed = request(
        placed, "destroy-corrupt", action="destroy", resource_id=operation["resource_id"]
    )
    assert destroyed.status_code == 202, destroyed.text


@pytest.mark.parametrize("costs", [[-100], [-100, 100], [float("inf")], ["bad"], [1e308, 1e308]])
def test_corrupt_matching_legacy_rows_block_admission(placed, recorded_dispatcher, costs):
    ids = [
        db.create(
            "demo", {}, business_unit="finance", environment="dev", estimated_monthly_cost=0
        ).id
        for _ in costs
    ]
    path = settings.data_dir / "forgeapi.db"
    with sqlite3.connect(path) as con:
        for deployment_id, cost in zip(ids, costs, strict=True):
            con.execute(
                "UPDATE deployments SET estimated_monthly_cost=? WHERE id=?", (cost, deployment_id)
            )
    before = path.read_bytes()
    assert_refused(placed, recorded_dispatcher, validation_status=503)
    assert path.read_bytes() == before


def test_other_placement_and_destroyed_costs_are_ignored(placed, recorded_dispatcher):
    seed_resource(1, float("nan"), unit="hr")
    seed_resource(2, -100, state="destroyed")
    excluded = [
        db.create(
            "demo", {}, business_unit=unit, environment="dev", estimated_monthly_cost=0
        ).id
        for unit in ("hr", "finance")
    ]
    with sqlite3.connect(settings.data_dir / "forgeapi.db") as con:
        con.execute(
            "UPDATE deployments SET estimated_monthly_cost=? WHERE id=?",
            (-100, excluded[0]),
        )
        con.execute(
            "UPDATE deployments SET state='destroyed', estimated_monthly_cost=? WHERE id=?",
            (-100, excluded[1]),
        )
    accepted = request(placed, "good")
    assert accepted.status_code == 202, accepted.text
    assert len(recorded_dispatcher.pending) == 1
    assert request(placed, "good").json()["id"] == accepted.json()["id"]
    assert len(recorded_dispatcher.pending) == 1


def test_failed_reservation_and_valid_legacy_total_still_enforce_budget(
    placed, recorded_dispatcher
):
    seed_resource(1, 60, state="failed")
    db.create("demo", {}, business_unit="finance", environment="dev", estimated_monthly_cost=1)
    refused = request(placed, "over-budget")
    assert refused.status_code == 403, refused.text
    assert not recorded_dispatcher.pending
