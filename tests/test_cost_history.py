"""Cost history is rebuilt from the ledger and ends exactly where budget discovery does."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from app import db, ledger
from app.settings import settings
from tests.test_budget_accounting import placed, seed_resource  # noqa: F401
from tests.test_operation_policy import request

START = datetime(2026, 9, 1, 12, tzinfo=UTC)


@pytest.fixture
def clock(monkeypatch):
    state = {"at": START}
    monkeypatch.setattr(ledger, "now", lambda: state["at"].isoformat())

    def move(days):
        state["at"] = START + timedelta(days=days, hours=1)

    return move


def history(client, prefix="", **params):
    params = {"business_unit": "finance", "environment": "dev", **params}
    return client.get(f"{prefix}/budgets/history", params=params)


def values(client, **params):
    return [d["reserved"] for d in history(client, **params).json()["days"]]


def reserved_now(client):
    return client.get("/agent").json()["business_units"][0]["budgets"]["dev"]["reserved"]


def run(op, state):
    ledger.finish(ledger.claim(op["id"], "plan"), state)


def test_accept_discard_apply_destroy_series(placed, clock):  # noqa: F811
    clock(0)
    first = request(placed, "a").json()  # day 0: accepted, reserves 60
    clock(1)
    ledger.finish(ledger.claim(first["id"], "plan"), "planned")
    ledger.discard(first["id"], "tester")  # day 1: released
    clock(2)
    second = request(placed, "b").json()
    run(second, "succeeded")  # day 2: 60 kept
    clock(5)
    resource_id = second["resource_id"]
    destroy = request(placed, "c", action="destroy", resource_id=resource_id).json()
    clock(6)
    run(destroy, "succeeded")  # day 6: released
    clock(8)
    body = history(placed, days=9).json()
    assert [d["date"] for d in body["days"]][0] == "2026-09-01"
    assert [d["reserved"] for d in body["days"]] == [60, 0, 60, 60, 60, 60, 0, 0, 0]
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 0
    assert body["currency_note"] == "pattern estimates in the budget's unit, not billing"
    assert body["monthly_budget"] == 100 and body["truncated"] is False
    assert body["by_pattern"] == [{"pattern": "demo", "reserved_now": 0, "change_over_period": 0}]
    assert body["movers"] == []  # both resources net to zero over the period


def test_movers_and_by_pattern_over_a_shorter_window(placed, clock):  # noqa: F811
    clock(0)
    op = request(placed, "a").json()
    run(op, "succeeded")
    clock(5)
    seed_resource(1, 10, state="ready")
    body = history(placed, days=3).json()
    assert [d["reserved"] for d in body["days"]] == [70, 70, 70]
    assert body["days"][-1]["reserved"] == reserved_now(placed)
    # the 60 was reserved before the window: it is baseline, not a mover; the seeded row is flat
    assert body["movers"] == []
    by = {p["pattern"]: p for p in body["by_pattern"]}
    assert by["demo"] == {"pattern": "demo", "reserved_now": 60, "change_over_period": 0}
    assert by["unknown"]["reserved_now"] == 10
    wide = history(placed, days=10).json()
    (mover,) = wide["movers"]
    assert mover["resource_id"] == op["resource_id"] and mover["delta"] == 60
    assert mover["pattern"] == "demo" and mover["at"].startswith("2026-09-01")
    assert wide["by_pattern"][0]["change_over_period"] == 60


def test_failed_uncertain_and_drift_checks_change_nothing(placed, clock):  # noqa: F811
    clock(0)
    ready = request(placed, "a").json()
    run(ready, "succeeded")
    rid = ready["resource_id"]
    clock(1)
    check = placed.post(f"/resources/{rid}/drift-check", headers={"Idempotency-Key": "chk"})
    assert check.status_code == 202, check.text
    ledger.interrupted(check.json()["id"], "plan")
    update = request(placed, "b", resource_id=rid, inputs={"filename": "y.txt", "content": "t"})
    assert update.status_code == 202, update.text
    run(update.json(), "failed")  # failed: restores the previous 60 (unchanged here)
    clock(2)
    again = request(placed, "c", resource_id=rid, inputs={"filename": "z.txt", "content": "t"})
    assert again.status_code == 202, again.text
    ledger.interrupted(again.json()["id"], "plan")  # uncertain keeps its reservation
    body = history(placed, days=4).json()
    assert [d["reserved"] for d in body["days"]] == [0, 60, 60, 60]
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 60


def test_failed_deploys_release_at_their_finish_time(placed, clock):  # noqa: F811
    clock(0)
    ready = request(placed, "a").json()
    run(ready, "succeeded")  # day 0: 60
    rid = ready["resource_id"]
    with ledger.connect() as con:  # pretend the deployed size cost 20
        row = json.loads(con.execute("SELECT body FROM resources").fetchone()[0])
        row["estimated_monthly_cost"] = 20
        con.execute("UPDATE resources SET body=?", (json.dumps(row),))
    clock(1)
    update = request(placed, "b", resource_id=rid, inputs={"filename": "y.txt", "content": "t"})
    assert update.status_code == 202, update.text  # day 1: reserves max(20, 60) = 60
    clock(2)
    run(update.json(), "failed")  # day 2: back to 20
    clock(3)
    again = request(placed, "c", resource_id=rid, inputs={"filename": "z.txt", "content": "t"})
    ledger.interrupted(again.json()["id"], "plan")  # day 3: 60 and stays (uncertain)
    body = history(placed, days=4).json()
    assert [d["reserved"] for d in body["days"]] == [60, 60, 20, 60]
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 60


def test_failed_new_resource_series_returns_to_zero(placed, clock):  # noqa: F811
    clock(0)
    op = request(placed, "a").json()
    clock(1)
    run(op, "failed")
    clock(2)
    body = history(placed, days=3).json()
    assert [d["reserved"] for d in body["days"]] == [60, 0, 0]
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 0


def test_final_day_matches_discovery_with_legacy_and_other_rows(placed, clock):  # noqa: F811
    seed_resource(1, 30, state="destroyed")
    seed_resource(2, 10, unit="hr")
    db.create("demo", {}, business_unit="finance", environment="dev", estimated_monthly_cost=25)
    clock(0)
    request(placed, "a")
    clock(3)
    body = history(placed, days=5).json()
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 85
    assert [d["reserved"] for d in body["days"]] == [
        25,
        85,
        85,
        85,
        85,
    ]  # legacy is flat; the accept lands on 09-01
    assert {"pattern": "legacy", "reserved_now": 25, "change_over_period": 0} in body["by_pattern"]


def test_row_written_outside_operations_still_ends_on_the_invariant(placed, clock):  # noqa: F811
    clock(0)
    op = request(placed, "a").json()
    run(op, "succeeded")
    with ledger.connect() as con:
        row = json.loads(con.execute("SELECT body FROM resources").fetchone()[0])
        row["estimated_monthly_cost"] = 75
        con.execute("UPDATE resources SET body=?", (json.dumps(row),))
    clock(1)
    body = history(placed, days=2).json()
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 75


def test_isolation_and_bounds(placed, clock):  # noqa: F811
    clock(0)
    request(placed, "a")
    assert history(placed, "/v1").status_code == 200
    assert history(placed, business_unit="hr").status_code == 403
    assert history(placed, environment="prod").status_code == 422
    for days in (0, 367, "x"):
        assert history(placed, days=days).status_code == 422
    assert history(placed, days=1).json()["days"][0]["reserved"] == 60
    assert history(placed, days=366).status_code == 200
    # defaults: the caller's only unit and only environment
    assert placed.get("/v1/budgets/history").json()["business_unit"] == "finance"


def test_read_only_and_capability(placed, clock):  # noqa: F811
    clock(0)
    request(placed, "a")
    path = settings.data_dir / "operations.sqlite"
    with sqlite3.connect(path) as con:
        before = [con.execute(f"SELECT * FROM {t}").fetchall() for t in ("operations", "events")]
    history(placed)
    with sqlite3.connect(path) as con:
        after = [con.execute(f"SELECT * FROM {t}").fetchall() for t in ("operations", "events")]
    assert before == after
    assert placed.get("/agent").json()["capabilities"]["cost_history"] is True


def test_corrupt_data_is_a_sanitized_503(placed, clock):  # noqa: F811
    seed_resource(1, -5)
    response = history(placed)
    assert response.status_code == 503 and "-5" not in response.text


def test_truncation(placed, clock, monkeypatch):  # noqa: F811
    from app import cost_history

    monkeypatch.setattr(cost_history, "MAX_RESOURCES", 1)
    seed_resource(1, 10, state="ready")
    seed_resource(2, 10, state="ready")
    body = history(placed, days=2).json()
    assert body["truncated"] is True
    assert body["days"][-1]["reserved"] == reserved_now(placed) == 20
