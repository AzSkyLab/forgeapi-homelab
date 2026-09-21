"""Business units: placement, injection, sizes, ownership. See docs/tenancy.md."""

import asyncio
import base64
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app import db, terraform
from app.main import app
from app.models import State
from app.settings import settings
from tests.conftest import git
from tests.test_pipeline import _run_workflow

FIN_DEVS, FIN_RELEASE, HR_DEVS = "grp-fin-devs", "grp-fin-release", "grp-hr-devs"
SUB_FIN_DEV, SUB_FIN_PRD, SUB_HR_DEV = "sub-fin-dev", "sub-fin-prd", "sub-hr-dev"

PATTERN = """
terraform {
  required_providers {
    local = { source = "hashicorp/local", version = "~> 2.5" }
  }
}

variable "name" { type = string }

variable "environment" {
  type = string
  validation {
    condition     = contains(["dev", "prd"], var.environment)
    error_message = "environment must be dev or prd."
  }
}

variable "business_unit" { type = string }
variable "cost_center" { type = string }
variable "private_endpoint_subnet_id" { type = string }

variable "location" {
  type    = string
  default = "eastus"
}

variable "tier" {
  type    = string
  default = "none"
}

resource "local_file" "this" {
  filename = "${path.module}/out/result.json"
  content = jsonencode({
    name = var.name, environment = var.environment, business_unit = var.business_unit,
    cost_center = var.cost_center, subnet = var.private_endpoint_subnet_id,
    location = var.location, tier = var.tier,
  })
}

output "path" { value = abspath(local_file.this.filename) }
"""

SIZING = {
    "description": "Sized demo",
    "sizing": {
        "small": {"dev": {"tier": "s-dev"}, "prd": {"tier": "s-prd"}},
        "large": {"prd": {"tier": "l-prd", "not_a_variable": True}},
    },
}


@pytest.fixture(autouse=True)
def tenancy(tmp_path, monkeypatch):
    repo = tmp_path / "terraform-pattern-sized"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "main.tf").write_text(PATTERN)
    (repo / "config.yaml").write_text(yaml.safe_dump(SIZING))
    git(repo, "add", "."), git(repo, "commit", "-qm", "v1.0.0"), git(repo, "tag", "v1.0.0")

    catalog_spec = yaml.safe_load(settings.catalog_path.read_text())
    catalog_spec["patterns"]["sized"] = {"repo": f"file://{repo}"}
    settings.catalog_path.write_text(yaml.safe_dump(catalog_spec))

    tenants_file = tmp_path / "tenants.yaml"
    tenants_file.write_text(
        yaml.safe_dump(
            {
                "business_units": {
                    "finance": {
                        "groups": [FIN_DEVS],
                        "inject": {"business_unit": "finance", "cost_center": "CC-1042"},
                        "patterns": ["sized", "demo"],
                        "regions": {"allowed": ["eastus2", "centralus"], "default": "eastus2"},
                        "environments": {
                            "dev": {
                                "subscription_id": SUB_FIN_DEV,
                                "network": {"private_endpoint_subnet_id": "/fin/dev/snet-pe"},
                            },
                            "prd": {
                                "subscription_id": SUB_FIN_PRD,
                                "groups": [FIN_RELEASE],
                                "network": {"private_endpoint_subnet_id": "/fin/prd/snet-pe"},
                            },
                        },
                    },
                    "hr": {
                        "groups": [HR_DEVS],
                        "inject": {"business_unit": "hr", "cost_center": "CC-2000"},
                        "patterns": ["sized"],
                        "environments": {
                            "dev": {
                                "subscription_id": SUB_HR_DEV,
                                "network": {"private_endpoint_subnet_id": "/hr/dev/snet-pe"},
                            }
                        },
                    },
                }
            }
        )
    )
    monkeypatch.setattr(settings, "tenants_path", tenants_file)
    monkeypatch.setattr(settings, "dev_groups", FIN_DEVS)


def as_groups(monkeypatch, *groups):
    monkeypatch.setattr(settings, "dev_groups", ",".join(groups))


def deploy(client, **body):
    request = {"pattern": "sized", "environment": "dev", "size": "small", "inputs": {"name": "x"}}
    return client.post("/deployments", json={**request, **body})


def test_me_lists_only_what_the_caller_can_do(client, monkeypatch):
    finance = client.get("/me").json()["business_units"]
    assert [u["name"] for u in finance] == ["finance"]
    assert finance[0]["environments"] == ["dev"]  # prd needs the release group
    assert finance[0]["patterns"] == ["demo", "sized"]

    as_groups(monkeypatch, FIN_DEVS, FIN_RELEASE)
    assert client.get("/me").json()["business_units"][0]["environments"] == ["dev", "prd"]

    as_groups(monkeypatch, "grp-nobody")
    assert client.get("/me").json()["business_units"] == []
    assert client.get("/patterns").json() == []
    assert deploy(client).status_code == 403


def test_patterns_are_filtered_per_business_unit(client, monkeypatch):
    assert [p["name"] for p in client.get("/patterns").json()] == ["demo", "sized"]
    as_groups(monkeypatch, HR_DEVS)
    assert [p["name"] for p in client.get("/patterns").json()] == ["sized"]
    response = client.post(
        "/deployments",
        json={"pattern": "demo", "environment": "dev", "inputs": {"filename": "a", "content": "b"}},
    )
    assert response.status_code == 403


def test_pattern_page_hides_platform_inputs_and_limits_location(client):
    page = client.get("/patterns/sized").json()
    inputs = {v["name"]: v for v in page["inputs"]}
    assert set(inputs) == {"name", "location"}  # env, BU, cost centre, subnet and tier are ours
    assert inputs["location"]["allowed_values"] == ["eastus2", "centralus"]
    assert inputs["location"]["default"] == "eastus2"
    assert page["placement"] == {
        "business_unit": "finance",
        "environment": "dev",
        "environments": ["dev"],
        "sizes": {"dev": ["small"]},
    }
    assert page["example"]["environment"] == "dev" and page["example"]["size"] == "small"
    schema = client.get("/patterns/sized/schema").json()
    assert set(schema["properties"]) == {"name", "location"}


def test_deployment_is_placed_and_injected_but_subscription_is_never_shown(client, dispatched):
    body = deploy(client).json()
    assert body["business_unit"] == "finance" and body["environment"] == "dev"
    assert body["injected"] == {
        "business_unit": "finance",
        "cost_center": "CC-1042",
        "private_endpoint_subnet_id": "/fin/dev/snet-pe",
        "environment": "dev",
        "tier": "s-dev",
        "location": "eastus2",  # the BU default, not the pattern's "eastus"
    }
    assert "subscription_id" not in body and SUB_FIN_DEV not in json.dumps(body)
    assert db.get(body["id"]).subscription_id == SUB_FIN_DEV
    assert db.get(body["id"]).requested_by == "local"
    assert dispatched == [body["id"]]


def test_callers_cannot_set_platform_inputs_or_bad_regions(client, dispatched):
    for inputs in (
        {"name": "x", "cost_center": "CC-MINE"},
        {"name": "x", "private_endpoint_subnet_id": "/evil"},
        {"name": "x", "tier": "l-prd"},  # locked by the size
        {"name": "x", "environment": "prd"},
    ):
        assert deploy(client, inputs=inputs).status_code == 422, inputs
    bad_region = deploy(client, inputs={"name": "x", "location": "westeurope"})
    assert bad_region.status_code == 422
    assert "location must be one of" in bad_region.text
    assert deploy(client, inputs={"name": "x", "location": "centralus"}).status_code == 202
    assert len(dispatched) == 1


def test_environment_and_size_rules(client, monkeypatch, dispatched):
    assert deploy(client, environment=None).status_code == 422
    assert deploy(client, environment="tst").status_code == 422
    assert deploy(client, environment="prd").status_code == 403  # not in the release group
    assert deploy(client, size=None).status_code == 422
    large_in_dev = deploy(client, size="large")
    assert large_in_dev.status_code == 422 and "['small']" in large_in_dev.text
    assert dispatched == []

    as_groups(monkeypatch, FIN_DEVS, FIN_RELEASE)
    prd = deploy(client, environment="prd", size="large").json()
    assert prd["injected"]["tier"] == "l-prd"
    assert "not_a_variable" not in prd["injected"]  # only declared variables are injected
    assert db.get(prd["id"]).subscription_id == SUB_FIN_PRD


def test_caller_in_two_business_units_must_say_which(client, monkeypatch):
    as_groups(monkeypatch, FIN_DEVS, HR_DEVS)
    ambiguous = deploy(client)
    assert ambiguous.status_code == 422 and "finance" in ambiguous.text and "hr" in ambiguous.text
    hr = deploy(client, business_unit="hr").json()
    assert hr["business_unit"] == "hr" and hr["injected"]["cost_center"] == "CC-2000"
    assert db.get(hr["id"]).subscription_id == SUB_HR_DEV

    as_groups(monkeypatch, FIN_DEVS)
    assert deploy(client, business_unit="hr").status_code == 403


def test_other_business_units_cannot_see_or_touch_a_deployment(client, monkeypatch, dispatched):
    deployment_id = deploy(client).json()["id"]
    db.update(deployment_id, State.succeeded, outputs={})

    as_groups(monkeypatch, HR_DEVS)
    assert client.get("/deployments").json() == []
    for call in (
        lambda: client.get(f"/deployments/{deployment_id}"),
        lambda: client.get(f"/deployments/{deployment_id}/logs"),
        lambda: client.put(f"/deployments/{deployment_id}", json={}),
        lambda: client.delete(f"/deployments/{deployment_id}"),
    ):
        assert call().status_code == 404  # indistinguishable from "does not exist"

    as_groups(monkeypatch, FIN_DEVS)
    assert [d["id"] for d in client.get("/deployments").json()] == [deployment_id]
    assert client.delete(f"/deployments/{deployment_id}").status_code == 202


def test_reading_prd_is_allowed_but_changing_it_needs_the_release_group(client, monkeypatch):
    as_groups(monkeypatch, FIN_DEVS, FIN_RELEASE)
    deployment_id = deploy(client, environment="prd").json()["id"]
    db.update(deployment_id, State.succeeded, outputs={})

    as_groups(monkeypatch, FIN_DEVS)
    assert client.get(f"/deployments/{deployment_id}").status_code == 200
    assert client.delete(f"/deployments/{deployment_id}").status_code == 403
    assert client.put(f"/deployments/{deployment_id}", json={}).status_code == 403


def test_update_re_reads_the_mapping_but_a_deployment_never_moves_subscription(client):
    deployment_id = deploy(client).json()["id"]
    db.update(deployment_id, State.succeeded, outputs={})

    spec = yaml.safe_load(settings.tenants_path.read_text())
    dev = spec["business_units"]["finance"]["environments"]["dev"]
    dev["network"]["private_endpoint_subnet_id"] = "/fin/dev/snet-pe-v2"
    settings.tenants_path.write_text(yaml.safe_dump(spec))
    updated = client.put(f"/deployments/{deployment_id}", json={}).json()
    assert updated["injected"]["private_endpoint_subnet_id"] == "/fin/dev/snet-pe-v2"

    db.update(deployment_id, State.succeeded, outputs={})
    dev["subscription_id"] = "sub-somewhere-else"
    settings.tenants_path.write_text(yaml.safe_dump(spec))
    assert client.put(f"/deployments/{deployment_id}", json={}).status_code == 409


def test_placement_fields_are_refused_when_business_units_are_off(client, monkeypatch):
    monkeypatch.setattr(settings, "tenants_path", None)
    response = client.post(
        "/deployments",
        json={"pattern": "demo", "environment": "dev", "inputs": {"filename": "a", "content": "b"}},
    )
    assert response.status_code == 422


def test_easy_auth_header_supplies_identity_and_groups(monkeypatch, dispatched):
    monkeypatch.setattr(settings, "auth_mode", "easyauth")
    claims = [
        {"typ": "http://schemas.microsoft.com/identity/claims/objectidentifier", "val": "user-42"},
        {"typ": "groups", "val": HR_DEVS},
        {"typ": "groups", "val": "grp-unrelated"},
    ]
    header = base64.b64encode(json.dumps({"claims": claims}).encode()).decode()
    client = TestClient(app)

    assert client.get("/me").status_code == 401
    mine = client.get("/me", headers={"X-MS-CLIENT-PRINCIPAL": header}).json()
    assert mine["id"] == "user-42" and [u["name"] for u in mine["business_units"]] == ["hr"]
    created = deploy(client).status_code  # no header
    assert created == 401


def test_injected_values_and_target_subscription_reach_terraform(monkeypatch):
    seen = []
    real_env = terraform._env
    monkeypatch.setattr(terraform, "_env", lambda sub=None: seen.append(sub) or real_env(sub))
    deployment = db.create(
        "sized", {"name": "real"}, "v1.0.0", git_commit(), business_unit="finance",
        environment="dev", subscription_id=SUB_FIN_DEV, size="small",
        injected={"business_unit": "finance", "cost_center": "CC-1042", "environment": "dev",
                  "private_endpoint_subnet_id": "/fin/dev/snet-pe", "tier": "s-dev",
                  "location": "eastus2"},
    )  # fmt: skip

    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"

    written = json.loads(Path(db.get(deployment.id).outputs["path"]).read_text())
    assert written == {
        "name": "real", "environment": "dev", "business_unit": "finance",
        "cost_center": "CC-1042", "subnet": "/fin/dev/snet-pe", "location": "eastus2",
        "tier": "s-dev",
    }  # fmt: skip
    assert seen and set(seen) == {SUB_FIN_DEV}
    assert terraform._env(SUB_FIN_DEV)["ARM_SUBSCRIPTION_ID"] == SUB_FIN_DEV


def git_commit() -> str:
    from app import catalog

    return catalog.resolve("sized", "v1.0.0").commit
