"""A different version of the same pattern plans against the resource's existing state."""

from pathlib import Path

import pytest
import yaml

from app import ledger, terraform
from app.settings import settings
from tests.conftest import git
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit

MAIN = """
terraform {
  required_providers {
    local = { source = "hashicorp/local", version = "~> 2.5" }
  }
%s}
variable "filename" { type = string }
variable "content" { type = string }
resource "local_file" "this" {
  filename = abspath("${path.module}/../out/${var.filename}")
  content  = "${var.content}-%s"
}
output "path" { value = local_file.this.filename }
"""
BACKEND = '  backend "local" { path = "../state/terraform.tfstate" }\n'


def _repo(tmp_path, name, backend):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for version in ("v1.0.0", "v1.1.0"):
        (repo / "main.tf").write_text(MAIN % (BACKEND if backend else "", version))
        git(repo, "add", "."), git(repo, "commit", "-qm", version), git(repo, "tag", version)
    return repo


@pytest.fixture
def upgradable(tmp_path):
    for name, backend in (("kept", True), ("local-state", False)):
        _repo(tmp_path, name, backend)
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    for name in ("kept", "local-state"):
        catalog["patterns"][name] = {"repo": f"file://{tmp_path / name}"}
    settings.catalog_path.write_text(yaml.safe_dump(catalog))


def _run(client, dispatcher, key, **updates):
    op = submit(client, key, **updates).json()
    assert dispatcher.run_next()
    return client.get(op["links"]["self"]).json()


def _apply(client, dispatcher, planned):
    payload = {"plan_digest": planned["plan_digest"]}
    assert client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert dispatcher.run_next()
    return client.get(planned["links"]["self"]).json()


def test_version_upgrade_plans_against_existing_state(
    tmp_path, upgradable, agent_client, recorded_dispatcher
):
    inputs = {"filename": "app.txt", "content": "hello"}
    created = _run(
        agent_client, recorded_dispatcher, "seed", pattern="kept", version="v1.0.0", inputs=inputs
    )
    assert _apply(agent_client, recorded_dispatcher, created)["state"] == "succeeded"
    out = Path(ledger.get(created["id"])["outputs"]["path"])
    assert out.read_text() == "hello-v1.0.0"

    upgrade = _run(
        agent_client,
        recorded_dispatcher,
        "upgrade",
        pattern="kept",
        version="v1.1.0",
        inputs=inputs,
        resource_id=created["resource_id"],
    )
    assert upgrade["state"] == "planned", upgrade
    assert [c["actions"] for c in upgrade["changes"]] == [["delete", "create"]]
    assert out.read_text() == "hello-v1.0.0"  # planning changes nothing
    assert _apply(agent_client, recorded_dispatcher, upgrade)["state"] == "succeeded"
    assert out.read_text() == "hello-v1.1.0"

    resource = ledger.resource(created["resource_id"])
    assert resource["version"] == "v1.1.0"
    assert resource["commit"] == git(tmp_path / "kept", "rev-parse", "v1.1.0")
    deployment = terraform.deployment_dir(created["resource_id"])
    states = [p for p in deployment.rglob("*.tfstate") if ".terraform" not in p.parts]
    assert states == [deployment / "state" / "terraform.tfstate"]


def test_upgrade_with_state_in_workspace_fails_planning_and_keeps_state(
    upgradable, agent_client, recorded_dispatcher
):
    inputs = {"filename": "w.txt", "content": "hi"}
    created = _run(
        agent_client,
        recorded_dispatcher,
        "seed",
        pattern="local-state",
        version="v1.0.0",
        inputs=inputs,
    )
    assert _apply(agent_client, recorded_dispatcher, created)["state"] == "succeeded"
    state = terraform.deployment_dir(created["resource_id"]) / "work" / "terraform.tfstate"
    before = state.read_bytes()
    failed = _run(
        agent_client,
        recorded_dispatcher,
        "upgrade",
        pattern="local-state",
        version="v1.1.0",
        inputs=inputs,
        resource_id=created["resource_id"],
    )
    assert failed["state"] == "failed"
    assert state.read_bytes() == before


def test_pattern_name_change_still_refused(upgradable, agent_client, recorded_dispatcher):
    created = submit(agent_client, "seed").json()
    changed = submit(agent_client, "other", pattern="kept", resource_id=created["resource_id"])
    assert changed.status_code == 409
    assert changed.json()["error"]["reason"] == "pattern_change_not_supported"


def test_same_version_update_and_default_version_unchanged(agent_client, recorded_dispatcher):
    created = _run(agent_client, recorded_dispatcher, "seed")
    assert _apply(agent_client, recorded_dispatcher, created)["state"] == "succeeded"
    inputs = {"filename": "agent.txt", "content": "changed"}
    update = _run(
        agent_client,
        recorded_dispatcher,
        "update",
        version=None,
        inputs=inputs,
        resource_id=created["resource_id"],
    )
    assert update["state"] == "planned" and update["version"] == "v1.0.0"
