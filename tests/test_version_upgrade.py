"""Compatibility with operation rows captured before the /v1 routes were added.

The fixture is from the pre-versioning Intent and SQLite ledger. Terraform plan
bytes are intentionally generated in a temporary workspace: they embed state
and are tied to the installed Terraform/provider toolchain.
"""

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app import ledger, operation_activities, terraform
from app.contracts import Intent, canonical_intent
from app.main import app
from app.settings import settings
from tests.conftest import EXAMPLE

CAPTURE = json.loads((Path(__file__).parent / "fixtures/upgrade/ledger.json").read_text())


def restore_old_rows(digest="f" * 64):
    """Load frozen rows without accepting a fresh intent through the new code."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(settings.data_dir / "operations.sqlite") as old_db:
        old_db.executescript((Path(__file__).parent / "fixtures/upgrade/schema.sql").read_text())
        for old in CAPTURE["operations"]:
            row = old.copy()
            body = json.loads(row["body"])
            body["source"] = str(EXAMPLE)
            body["plan_digest"] = digest
            row["body"] = json.dumps(body)
            old_db.execute(
                "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(row.values())
            )
        for old in CAPTURE["resources"]:
            row = old.copy()
            body = json.loads(row["body"])
            body["source"] = str(EXAMPLE)
            row["body"] = json.dumps(body)
            old_db.execute("INSERT INTO resources VALUES (?, ?)", tuple(row.values()))
        for row in CAPTURE["events"]:
            old_db.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)", tuple(row.values()))
    return CAPTURE["operations"][0]["id"]


def test_old_intent_replays_on_root_and_v1_without_new_acceptance(recorded_dispatcher):
    request = Intent.model_validate(CAPTURE["request"])
    assert canonical_intent(request) == CAPTURE["request_material"]
    assert ledger.fingerprint(CAPTURE["request_material"]) == CAPTURE["request_fingerprint"]
    op_id = restore_old_rows()
    headers = {"Idempotency-Key": CAPTURE["idempotency_key"]}
    api = TestClient(app)
    for route in ("/operations", "/v1/operations"):
        replay = api.post(route, json=CAPTURE["request"], headers=headers)
        assert replay.status_code == 202, replay.text
        assert replay.json()["id"] == op_id
        assert replay.json()["resource_id"] == CAPTURE["operations"][0]["resource_id"]
        conflict = api.post(
            route,
            json={**CAPTURE["request"], "inputs": {"filename": "changed.txt", "content": "new"}},
            headers=headers,
        )
        assert conflict.status_code == 409
    assert {x["id"] for x in api.get("/v1/operations").json()["items"]} == {op_id}
    assert not recorded_dispatcher.pending  # a planned operation is not replanned on retry
    assert [e["outcome"] for e in ledger.events(op_id, 0, 100)].count("accepted") == 1


def test_old_planned_row_executes_saved_local_plan_once(recorded_dispatcher, monkeypatch):
    old = json.loads(CAPTURE["operations"][0]["body"])
    resource_id = old["resource_id"]
    terraform.prepare(resource_id, str(EXAMPLE), old["inputs"], safe_logs=True)
    terraform._run(
        resource_id, "plan", "-no-color", "-out=tfplan", _unlock_once=False, safe_logs=True
    )
    path = terraform.deployment_dir(resource_id) / "work/tfplan"
    digest = operation_activities.hashlib.sha256(path.read_bytes()).hexdigest()
    op_id = restore_old_rows(digest)
    calls = []
    run = terraform._run

    def counted(*args, **kwargs):
        calls.append(args[1])
        return run(*args, **kwargs)

    monkeypatch.setattr(terraform, "_run", counted)
    api = TestClient(app)
    execute = f"/v1/operations/{op_id}/execute"
    payload = {"plan_digest": digest}
    assert api.get(f"/v1/operations/{op_id}").json()["state"] == "planned"
    assert api.post(execute, json=payload).status_code == 202
    assert api.post(execute, json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    done = api.get(f"/v1/operations/{op_id}").json()
    assert done["state"] == "succeeded", done
    assert Path(done["outputs"]["path"]).read_text() == old["inputs"]["content"]
    assert api.post(execute, json=payload).json()["state"] == "succeeded"
    assert calls.count("apply") == 1
    assert "plan" not in calls
    assert not path.exists()
    assert not recorded_dispatcher.run_next()
