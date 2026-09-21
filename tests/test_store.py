"""One contract, run against both stores. The Table store runs against Azurite, Microsoft's
storage emulator, in Docker; it is skipped with a visible reason when Docker is unavailable."""

import shutil
import subprocess
import time
import uuid

import pytest

from app import db, db_table
from app.models import State
from app.settings import settings

# Azurite's published, fixed development credentials. Not a secret; only valid for the emulator.
_AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)


@pytest.fixture(scope="session")
def azurite() -> str:
    if not shutil.which("docker"):
        pytest.skip("Table Storage store NOT tested: docker is required to run Azurite")
    name = f"forgeapi-azurite-{uuid.uuid4().hex[:8]}"
    image = "mcr.microsoft.com/azure-storage/azurite"
    command = ["azurite-table", "--tableHost", "0.0.0.0", "--skipApiVersionCheck"]
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::10002", image, *command],
        check=True, capture_output=True,
    )  # fmt: skip
    try:
        port = subprocess.run(
            ["docker", "port", name, "10002"], check=True, capture_output=True, text=True
        ).stdout.strip().rsplit(":", 1)[1]  # fmt: skip
        endpoint = f"http://127.0.0.1:{port}/devstoreaccount1"
        yield (
            f"DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
            f"AccountKey={_AZURITE_KEY};TableEndpoint={endpoint};"
        )
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


@pytest.fixture(params=["sqlite", "table"])
def store(request, monkeypatch):
    if request.param == "table":
        monkeypatch.setattr(settings, "db_backend", "table")
        monkeypatch.setattr(settings, "table_connection_string", request.getfixturevalue("azurite"))
        monkeypatch.setattr(settings, "table_name", f"t{uuid.uuid4().hex[:12]}")
        for _ in range(40):  # wait for the emulator to accept connections
            try:
                db_table.get("dep_probe")
                break
            except Exception:
                time.sleep(0.25)
    return request.param


def test_create_then_get_round_trips_every_field(store):
    created = db.create("demo", {"name": "x", "tags": {"a": "b"}, "n": 3}, "v1.2.3", "abc123")
    loaded = db.get(created.id)
    assert loaded == created
    assert loaded.state == State.accepted
    assert loaded.version == "v1.2.3" and loaded.commit == "abc123"
    assert loaded.outputs is None and loaded.error is None


def test_unversioned_pattern_has_no_version_or_commit(store):
    loaded = db.get(db.create("local-file", {}).id)
    assert loaded.version is None and loaded.commit is None


def test_missing_record(store):
    assert db.get("dep_missing") is None
    db.update("dep_missing", State.failed, error="x")  # no error, nothing created
    assert db.get("dep_missing") is None


def test_update_keeps_outputs_and_replaces_error(store):
    deployment_id = db.create("demo", {}).id

    db.update(deployment_id, State.failed, error="boom")
    assert db.get(deployment_id).error == "boom"

    db.update(
        deployment_id, State.succeeded, outputs={"uri": "https://x", "list": [1, 2]},
        withheld=["admin_password"],
    )  # fmt: skip
    done = db.get(deployment_id)
    assert done.withheld_outputs == ["admin_password"]
    assert done.state == State.succeeded and done.error is None
    assert done.outputs == {"uri": "https://x", "list": [1, 2]}

    db.update(deployment_id, State.destroying)  # a later state change must not drop outputs
    assert db.get(deployment_id).withheld_outputs == ["admin_password"]
    assert db.get(deployment_id).outputs == {"uri": "https://x", "list": [1, 2]}
    assert db.get(deployment_id).updated_at > done.created_at


def test_respec_changes_what_the_deployment_should_be(store):
    deployment_id = db.create("demo", {"a": 1}, "v1.0.0", "aaa").id
    db.update(deployment_id, State.succeeded, outputs={"kept": True})

    db.respec(deployment_id, {"a": 2, "b": "new"}, "v1.1.0", "bbb")

    changed = db.get(deployment_id)
    assert changed.inputs == {"a": 2, "b": "new"}
    assert (changed.version, changed.commit) == ("v1.1.0", "bbb")
    assert changed.state == State.succeeded and changed.outputs == {"kept": True}


def test_placement_fields_round_trip_and_listing_is_scoped(store):
    injected = {"cost_center": "CC-1", "tags": {"a": "b"}}
    fin = db.create(
        "demo", {"n": 1}, "v1", "abc", business_unit="finance", environment="dev",
        subscription_id="sub-1", size="small", injected=injected, requested_by="user-1",
        estimated_monthly_cost=12.5,
    )  # fmt: skip
    free = db.create("demo", {}, business_unit="finance", estimated_monthly_cost=0)
    assert db.get(free.id).estimated_monthly_cost == 0  # zero is a real estimate, not "unknown"
    assert db.get(fin.id).estimated_monthly_cost == 12.5
    hr = db.create("demo", {}, business_unit="hr's", environment="dev", subscription_id="sub-2")
    legacy = db.create("demo", {})

    loaded = db.get(fin.id)
    assert loaded == fin
    assert (loaded.business_unit, loaded.environment, loaded.subscription_id) == (
        "finance", "dev", "sub-1",
    )  # fmt: skip
    assert loaded.size == "small" and loaded.injected == injected
    assert loaded.requested_by == "user-1"
    assert db.get(legacy.id).business_unit is None and db.get(legacy.id).injected is None

    assert {d.id for d in db.list_for(["finance"])} == {fin.id, free.id}
    assert {d.id for d in db.list_for(["finance", "hr's"])} == {fin.id, free.id, hr.id}  # quotes
    assert db.list_for([]) == []
    assert {fin.id, hr.id, legacy.id} <= {d.id for d in db.list_for(None)}

    db.respec(fin.id, {"n": 2}, "v2", "def", "large", {"cost_center": "CC-2"}, 99)
    changed = db.get(fin.id)
    assert (changed.size, changed.injected, changed.version) == (
        "large",
        {"cost_center": "CC-2"},
        "v2",
    )
    assert changed.business_unit == "finance" and changed.subscription_id == "sub-1"
    assert changed.estimated_monthly_cost == 99


def test_logs_are_readable_from_the_shared_store(store, monkeypatch, tmp_path):
    from app import logs, terraform

    logs.append("dep_logs", "$ terraform plan\nfirst\n")
    logs.append("dep_logs", "x" * 70_000 + "\nlast\n")  # larger than one table property
    logs.append("dep_other", "someone else's\n")

    if store == "table":  # a different container: no local file, only the shared store
        monkeypatch.setattr(terraform, "deployment_dir", lambda d: tmp_path / "another-disk" / d)
    text = logs.read("dep_logs")
    assert text.startswith("$ terraform plan\nfirst\n") and text.endswith("\nlast\n")
    assert len(text) == len("$ terraform plan\nfirst\n") + 70_000 + len("\nlast\n")
    assert "someone else's" not in text
    assert logs.read("dep_none") == ""


def test_audit_events_round_trip_and_are_scoped(store):
    from datetime import UTC, datetime, timedelta

    from app import audit

    first = audit.record("deployment.create", "accepted", "user-1", deployment_id="dep_a",
                         pattern="demo", version="v1", business_unit="finance", environment="dev",
                         detail={"inputs": {"n": 1}, "injected": {"cc": "X"}})  # fmt: skip
    audit.record("deployment.create", "refused", "user-2", status=403, business_unit="hr's",
                 detail={"reason": {"message": "over budget", "available": 0.5}})  # fmt: skip
    audit.record("deployment.state", "succeeded", "worker", deployment_id="dep_a",
                 business_unit="finance")  # fmt: skip
    audit.record("deployment.create", "accepted", "local")  # single-tenant: no business unit

    finance = audit.query(["finance"], None, 10)
    assert [(e.action, e.outcome) for e in finance] == [
        ("deployment.state", "succeeded"), ("deployment.create", "accepted"),
    ]  # fmt: skip
    assert finance[1] == first and finance[1].detail["injected"] == {"cc": "X"}
    [refusal] = audit.query(["hr's"], None, 10)
    assert refusal.status == 403 and refusal.detail["reason"]["available"] == 0.5
    assert len(audit.query(None, None, 10)) == 4 and audit.query([], None, 10) == []
    assert len(audit.query(None, None, 2)) == 2
    assert [e.outcome for e in audit.for_deployment("dep_a", "finance")] == [
        "succeeded",
        "accepted",
    ]
    assert audit.for_deployment("dep_a", "hr's") == []
    assert audit.query(None, datetime.now(UTC) + timedelta(minutes=1), 10) == []


def test_touch_moves_only_the_heartbeat(store):
    deployment = db.create("demo", {"n": 1}, "v1", "abc")
    db.update(deployment.id, State.applying)
    before = db.get(deployment.id)
    db.touch(deployment.id)
    after = db.get(deployment.id)
    assert after.updated_at > before.updated_at
    assert after.model_dump(exclude={"updated_at"}) == before.model_dump(exclude={"updated_at"})
    db.touch("dep_missing")  # no error, nothing created
    assert db.get("dep_missing") is None
