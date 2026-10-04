"""A deploy that ends `failed` changed nothing, so it gives back what acceptance reserved.

`uncertain` keeps the reservation until an operator reconciles it; a reconcile as failed is the
operator saying nothing changed, so it releases like any failed deploy."""

import json

from app import ledger
from app.settings import settings
from tests.test_budget_accounting import placed  # noqa: F401
from tests.test_budget_accounting import seed_resource as seed_resource  # noqa: F401
from tests.test_operation_policy import request

FAIL_BIN = "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n"
LOCK_BIN = (
    "import sys\n"
    "sys.stderr.write('Error: Error acquiring the state lock\\n\\n"
    "Lock Info:\\n  ID:        7f3c1c52-9a1e-4d5b-8f0a-2b6e0d9c4a11\\n')\n"
    "sys.exit(1)\n"
)


def fake_bin(tmp_path, monkeypatch, body):
    path = tmp_path / "terraform-fake"
    path.write_text(f"#!/usr/bin/env python3\n{body}")
    path.chmod(0o755)
    monkeypatch.setattr(settings, "terraform_bin", str(path))


def reserved(client):
    return client.get("/agent").json()["business_units"][0]["budgets"]["dev"]["reserved"]


def cheapen(resource_id, cost):
    """Pretend the deployed size cost `cost`, so the next update reserves a pricier amount."""
    with ledger.connect() as con:
        row = json.loads(
            con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()[0]
        )
        row["estimated_monthly_cost"] = cost
        con.execute("UPDATE resources SET body=? WHERE id=?", (json.dumps(row), resource_id))


def ready_resource(client, key="ready"):
    op = request(client, key).json()
    ledger.finish(ledger.claim(op["id"], "plan"), "succeeded")
    return op["resource_id"]


def test_new_resource_whose_plan_fails_reserves_nothing(
    placed, recorded_dispatcher, tmp_path, monkeypatch  # noqa: F811
):
    fake_bin(tmp_path, monkeypatch, FAIL_BIN)
    op = request(placed, "a").json()
    assert reserved(placed) == 60
    assert request(placed, "blocked").status_code == 403  # no headroom while it is reserved
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"
    assert reserved(placed) == 0
    assert request(placed, "after").status_code == 202  # the freed headroom is usable


def test_failed_update_to_pricier_size_returns_to_the_old_cost(
    placed, recorded_dispatcher, tmp_path, monkeypatch  # noqa: F811
):
    rid = ready_resource(placed)
    recorded_dispatcher.pending.clear()  # the first operation was finished by hand
    cheapen(rid, 20)
    update = request(placed, "up", resource_id=rid, inputs={"filename": "y.txt", "content": "t"})
    assert update.status_code == 202, update.text
    assert reserved(placed) == 60
    fake_bin(tmp_path, monkeypatch, FAIL_BIN)
    assert recorded_dispatcher.run_next()
    assert ledger.get(update.json()["id"])["state"] == "failed"
    assert reserved(placed) == 20
    assert ledger.resource(rid)["estimated_monthly_cost"] == 20


def test_apply_refused_on_a_held_lock_releases_the_reservation(
    placed, recorded_dispatcher, tmp_path, monkeypatch  # noqa: F811
):
    op = request(placed, "lock").json()
    assert recorded_dispatcher.run_next()  # real plan
    planned = placed.get(op["links"]["self"]).json()
    assert planned["state"] == "planned" and reserved(placed) == 60
    payload = {"plan_digest": planned["plan_digest"]}
    assert placed.post(planned["links"]["execute"], json=payload).status_code == 202
    fake_bin(tmp_path, monkeypatch, LOCK_BIN)
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"
    assert reserved(placed) == 0


def test_uncertain_keeps_the_reservation_until_reconciled(placed):  # noqa: F811
    rid = ready_resource(placed)
    cheapen(rid, 20)
    update = request(placed, "u", resource_id=rid, inputs={"filename": "y.txt", "content": "t"})
    ledger.interrupted(update.json()["id"], "plan")
    assert ledger.get(update.json()["id"])["state"] == "uncertain"
    assert reserved(placed) == 60


def test_reconcile_as_failed_releases_like_any_failed_deploy(placed):  # noqa: F811
    rid = ready_resource(placed)
    cheapen(rid, 20)
    update = request(placed, "u", resource_id=rid, inputs={"filename": "y.txt", "content": "t"})
    ledger.interrupted(update.json()["id"], "plan")
    ledger.reconcile(update.json()["id"], "tester", "failed", "nothing applied")
    assert reserved(placed) == 20
    # a second failed reconcile on the same resource restores again
    second = request(placed, "v", resource_id=rid, inputs={"filename": "z.txt", "content": "t"})
    ledger.interrupted(second.json()["id"], "plan")
    ledger.reconcile(second.json()["id"], "tester", "failed", "also nothing")
    assert reserved(placed) == 20


def test_operation_accepted_before_previous_amount_existed_keeps_its_reservation(
    placed,  # noqa: F811
):
    op = request(placed, "legacy").json()
    with ledger.connect() as con:
        body = json.loads(con.execute("SELECT body FROM operations").fetchone()[0])
        del body["previous_estimated_monthly_cost"]
        con.execute("UPDATE operations SET body=?", (json.dumps(body),))
    ledger.finish(ledger.claim(op["id"], "plan"), "failed")
    assert reserved(placed) == 60


def test_destroy_and_drift_check_failures_change_no_reservation(placed):  # noqa: F811
    rid = ready_resource(placed)
    check = placed.post(f"/resources/{rid}/drift-check", headers={"Idempotency-Key": "chk"})
    ledger.finish(ledger.claim(check.json()["id"], "plan"), "failed")
    assert reserved(placed) == 60
    destroy = request(placed, "d", action="destroy", resource_id=rid).json()
    ledger.finish(ledger.claim(destroy["id"], "plan"), "failed")
    assert reserved(placed) == 60
