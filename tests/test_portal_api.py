"""Portal-facing resource fields: cloud, region, cost, ownership, created_at, managed objects,
and the per-environment clouds in discovery. All additive; older stored rows return nulls."""

import json
import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.auth import require_caller
from app.main import app
from app.settings import settings
from app.tenants import Caller
from tests.conftest import git
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit

LOCAL_FILE = {
    "pattern": "local-file",
    "version": None,
    "inputs": {"filename": "portal.txt", "content": "original"},
}
THIS = [{"address": "local_file.this", "type": "local_file"}]


def run(client, dispatcher, key, **body):
    """Submit, plan, execute and apply; return (resource_id, succeeded operation)."""
    created = submit(client, key, **body).json()
    assert dispatcher.run_next()
    planned = client.get(created["links"]["self"]).json()
    assert planned["state"] == "planned", planned
    payload = {"plan_digest": planned["plan_digest"]}
    assert client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert dispatcher.run_next()
    done = client.get(created["links"]["self"]).json()
    assert done["state"] == "succeeded", done
    return done["resource_id"], done


def test_local_deploy_update_destroy_lifecycle(agent_client, recorded_dispatcher):
    rid, done = run(agent_client, recorded_dispatcher, "p-1", **LOCAL_FILE)
    first = agent_client.get(f"/resources/{rid}").json()
    assert first["managed_objects"] == THIS
    assert first["owned_by_caller"] is True
    assert first["created_at"]
    assert first["cloud"] is None and first["region"] is None
    assert first["estimated_monthly_cost"] is None
    assert "actor" not in first and "local" not in first.values()

    # A replan with changes keeps the object and the creation time.
    update = {**LOCAL_FILE, "inputs": {**LOCAL_FILE["inputs"], "content": "changed"}}
    run(agent_client, recorded_dispatcher, "p-2", **update, resource_id=rid)
    second = agent_client.get(f"/resources/{rid}").json()
    assert second["created_at"] == first["created_at"]
    assert second["managed_objects"] == THIS
    assert second["updated_at"] != first["updated_at"]
    listed = agent_client.get("/resources").json()["items"]
    assert [r["managed_objects"] for r in listed] == [THIS]

    # A replan with no changes at all: no-op objects are still listed after apply.
    created = submit(agent_client, "p-3", **update, resource_id=rid).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(created["links"]["self"]).json()
    assert planned["changes"] == []
    assert "planned_objects" not in planned
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    third = agent_client.get(f"/resources/{rid}").json()
    assert third["managed_objects"] == THIS
    assert third["created_at"] == first["created_at"]

    destroyed = submit(
        agent_client,
        "p-4",
        action="destroy",
        pattern="local-file",
        resource_id=rid,
        version=None,
        inputs={},
    ).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(destroyed["links"]["self"]).json()
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()
    final = agent_client.get(f"/resources/{rid}").json()
    assert final["state"] == "destroyed"
    assert final["managed_objects"] == []
    assert final["created_at"] == first["created_at"]


def test_old_resource_body_returns_nulls_and_computed_ownership(agent_client):
    ledger.refused("local", "warmup")  # creates the schema
    body = {
        "id": "res_" + "a" * 32,
        "operation_id": "op_" + "b" * 32,
        "actor": "local",
        "state": "ready",
        "pattern": "local-file",
        "estimated_monthly_cost": float("nan"),
    }
    with sqlite3.connect(settings.data_dir / "operations.sqlite") as con:
        con.execute("INSERT INTO resources VALUES (?, ?)", (body["id"], json.dumps(body)))
        other = {
            **body,
            "id": "res_" + "c" * 32,
            "actor": "someone-else",
            "estimated_monthly_cost": 3,
        }
        con.execute("INSERT INTO resources VALUES (?, ?)", (other["id"], json.dumps(other)))
    old = agent_client.get(f"/resources/{body['id']}")
    assert old.status_code == 200, old.text
    got = old.json()
    for name in ("cloud", "region", "estimated_monthly_cost", "created_at", "managed_objects"):
        assert got[name] is None, name
    assert got["owned_by_caller"] is True
    app.dependency_overrides[require_caller] = lambda: Caller("someone-else", frozenset())
    try:
        seen = agent_client.get(f"/resources/{other['id']}").json()
    finally:
        app.dependency_overrides.clear()
    assert seen["owned_by_caller"] is True and seen["estimated_monthly_cost"] == 3.0


@pytest.fixture
def portal(monkeypatch, pattern_repo, recorded_dispatcher):
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + '\nvariable "subscription_id" { type = string }\nvariable "region" { type = string }\n'
    )
    (pattern_repo / "config.yaml").write_text("estimated_costs: 12.5\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "portal")
    git(pattern_repo, "tag", "v1.2.0")
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"]["demo"]["cloud"] = "azure"
    settings.catalog_path.write_text(yaml.safe_dump(catalog))
    target = {"subscription_id": "hidden-portal-sub", "region": "westeurope"}
    mapping = {
        "business_units": {
            "finance": {
                "groups": ["finance"],
                "patterns": ["demo"],
                "environments": {
                    "dev": {"targets": {"azure": target}},
                    "prod": {
                        "targets": {
                            "gcp": {"project_id": "hidden-gcp", "region": "x"},
                            "aws": {"aws_account_id": "123456789012", "region": "y"},
                        }
                    },
                    "plain": {"subscription_id": "hidden-plain"},
                },
            }
        }
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    monkeypatch.setattr(settings, "dev_groups", "finance")
    return TestClient(app)


def test_cloud_region_cost_owner_and_no_placement_ids(portal, recorded_dispatcher):
    rid, _ = run(
        portal,
        recorded_dispatcher,
        "pt-1",
        pattern="demo",
        version="v1.2.0",
        environment="dev",
        inputs={"filename": "p.txt", "content": "x"},
    )
    shown = portal.get(f"/resources/{rid}")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert body["cloud"] == "azure"
    assert body["region"] == "westeurope"
    assert body["estimated_monthly_cost"] == 12.5
    assert body["owned_by_caller"] is True
    assert body["managed_objects"] == THIS
    listing = portal.get("/resources")
    for text in (shown.text, listing.text, portal.get("/agent").text):
        assert "hidden-" not in text and "123456789012" not in text

    # A different caller in the same business unit sees it, but does not own it.
    app.dependency_overrides[require_caller] = lambda: Caller("colleague", frozenset({"finance"}))
    try:
        other = portal.get(f"/resources/{rid}")
        assert other.status_code == 200, other.text
        assert other.json()["owned_by_caller"] is False
        assert other.json()["estimated_monthly_cost"] == 12.5
        assert "colleague" not in other.text and "actor" not in other.json()
    finally:
        app.dependency_overrides.clear()


def test_discovery_lists_clouds_per_environment(portal):
    unit = portal.get("/agent").json()["business_units"][0]
    assert unit["clouds"] == {"dev": ["azure"], "prod": ["aws", "gcp"], "plain": []}
    assert "hidden" not in json.dumps(unit)
