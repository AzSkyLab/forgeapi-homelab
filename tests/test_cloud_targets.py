"""Cloud placement belongs to the platform and cannot move an existing resource."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.conftest import git


@pytest.fixture(
    params=[("azure", "subscription_id"), ("aws", "aws_account_id"), ("gcp", "project_id")]
)
def cloud_setup(request, monkeypatch, pattern_repo, recorded_dispatcher):
    cloud, identifier = request.param
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + f'''
variable "{identifier}" {{ type = string }}
variable "region" {{ type = string }}
output "placement_id" {{ value = var.{identifier} }}
'''
    )
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "cloud target contract")
    git(pattern_repo, "tag", "v1.2.0")
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"]["demo"]["cloud"] = cloud
    settings.catalog_path.write_text(yaml.safe_dump(catalog))
    target = {identifier: f"hidden-{cloud}-target", "region": "chosen-region"}
    mapping = {
        "business_units": {
            name: {
                "groups": [name],
                "patterns": ["demo"],
                "environments": {"dev": {"targets": {cloud: target}}},
            }
            for name in ("finance", "hr")
        }
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    monkeypatch.setattr(settings, "dev_groups", "finance")
    return TestClient(app), cloud, identifier, target, mapping, recorded_dispatcher


def submit(client, key="one", **updates):
    return client.post(
        "/operations",
        json={
            "pattern": "demo",
            "version": "v1.2.0",
            "environment": "dev",
            "inputs": {"filename": "proof.txt", "content": "hello"},
            **updates,
        },
        headers={"Idempotency-Key": key},
    )


def test_target_hidden_in_discovery_request_and_outputs(cloud_setup, monkeypatch):
    client, cloud, identifier, target, _, dispatcher = cloud_setup
    described = client.get("/patterns/demo?environment=dev")
    assert described.status_code == 200
    assert described.json()["cloud"] == cloud
    properties = described.json()["input_schema"]["properties"]
    assert identifier not in properties and "region" not in properties
    for variable in (identifier, "region"):
        denied = submit(
            client, variable, inputs={"filename": "x", "content": "y", variable: "caller-override"}
        )
        assert denied.status_code == 422
    op = submit(client).json()
    assert ledger.get(op["id"])["cloud_target"] == target
    assert ledger.get(op["id"])["injected"] == target
    dispatcher.run_next()  # real Terraform, using local provider for fast boundary proof
    planned = client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    assert (
        client.post(
            op["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
        ).status_code
        == 202
    )
    dispatcher.run_next()
    done = client.get(op["links"]["self"])
    assert done.json()["state"] == "succeeded"
    assert done.json()["withheld_outputs"] == ["placement_id"]
    exposed = [
        described.text,
        done.text,
        client.get("/agent").text,
        client.get(op["links"]["events"]).text,
        client.get("/operations").text,
    ]
    assert all(target[identifier] not in body for body in exposed)
    assert target[identifier] not in json.dumps(ledger.get(op["id"])["outputs"])
    monkeypatch.setattr(settings, "dev_groups", "hr")
    assert client.get(op["links"]["self"]).status_code == 404
    assert client.get("/operations").json()["items"] == []


def test_target_in_plan_address_fails_before_public_summary(cloud_setup, pattern_repo):
    from app import operation_activities, terraform

    client, _, identifier, target, _, dispatcher = cloud_setup
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + f'''
resource "terraform_data" "address_probe" {{
  for_each = toset([var.{identifier}])
  input = each.key
}}
'''
    )
    git(pattern_repo, "add", "main.tf")
    git(pattern_repo, "commit", "-qm", "target in plan address")
    git(pattern_repo, "tag", "v1.3.0")
    accepted = submit(client, "address-probe", version="v1.3.0")
    assert accepted.status_code == 202, accepted.text
    op = accepted.json()
    assert dispatcher.run_next()  # a real local Terraform plan; no apply
    status = client.get(op["links"]["self"])
    assert status.json()["state"] == "failed"
    assert status.json()["plan_digest"] is None
    assert status.json()["changes"] is None
    stored = ledger.get(op["id"])
    assert stored["plan_digest"] is None and stored["changes"] is None
    assert stored["cloud_target"] == target
    assert not operation_activities.plan_path(stored).exists()  # failed plans are deleted
    saved_digest = "0" * 64
    exposed = [
        status.text,
        client.get("/operations").text,
        client.get(op["links"]["events"]).text,
    ]
    assert all(target[identifier] not in body for body in exposed)
    assert target[identifier] not in terraform.log_path(op["resource_id"]).read_text()
    assert (
        client.post(op["links"]["execute"], json={"plan_digest": saved_digest}).status_code
        == 409
    )
    assert not dispatcher.pending


def test_mapping_changes_cannot_move_existing_state_or_execute_old_plan(cloud_setup, monkeypatch):
    client, cloud, identifier, _, mapping, _ = cloud_setup
    op = submit(client).json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="b" * 64)
    mapping["business_units"]["finance"]["environments"]["dev"]["targets"][cloud][identifier] = (
        "moved"
    )
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    assert client.post(op["links"]["execute"], json={"plan_digest": "b" * 64}).status_code == 409
    assert submit(client, "update", resource_id=op["resource_id"]).status_code == 409
    assert ledger.get(op["id"])["state"] == "planned"


def test_missing_cloud_target_fails_closed(cloud_setup, monkeypatch):
    client, _, _, _, mapping, dispatcher = cloud_setup
    mapping["business_units"]["finance"]["environments"]["dev"]["targets"] = {}
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    refused = submit(client)
    assert refused.status_code == 422
    assert refused.json()["error"]["reason"] == "cloud_not_available"
    assert "environment dev has no" in refused.json()["error"]["detail"]
    assert not dispatcher.pending
    assert client.get("/operations").json()["items"] == []
