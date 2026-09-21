"""Real end-to-end runs: real Temporal dev server, real worker, real git, real Terraform. No mocks.

Requires the `terraform` and `git` binaries and network access (provider + dev-server download on
first run).
"""

import asyncio
import re
import uuid
from pathlib import Path

from temporalio.testing import WorkflowEnvironment

from app import catalog, db, terraform
from app.models import State
from app.settings import settings
from app.worker import build_worker
from app.workflows import DeployWorkflow, DestroyWorkflow
from tests.conftest import git


async def _run_workflow(deployment_id: str, workflow=DeployWorkflow) -> str:
    async with await WorkflowEnvironment.start_local() as env, build_worker(env.client):
        return await env.client.execute_workflow(
            workflow.run,
            deployment_id,
            id=f"{deployment_id}-{uuid.uuid4().hex[:6]}",
            task_queue=settings.task_queue,
        )


def _accept(pattern: str, inputs: dict, version: str | None = None):
    resolved = catalog.resolve(pattern, version)
    return db.create(pattern, inputs, resolved.version, resolved.commit)


def test_pattern_from_git_tag_deploys():
    deployment = _accept("demo", {"filename": "hello.txt", "content": "from git", "suffix": "!"})

    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"

    result = db.get(deployment.id)
    assert result.state == State.succeeded and result.error is None
    written = Path(result.outputs["path"])
    assert written.read_text() == "from git!"
    assert written.is_relative_to(terraform.deployment_dir(deployment.id))
    log = terraform.log_path(deployment.id).read_text()
    assert "$ terraform plan" in log and "Apply complete" in log


def test_runs_the_commit_accepted_even_if_the_tag_moves(pattern_repo):
    deployment = _accept("demo", {"filename": "a.txt", "content": "original"}, "v1.0.0")
    (pattern_repo / "main.tf").write_text("this is not terraform")
    git(pattern_repo, "commit", "-qam", "break it")
    git(pattern_repo, "tag", "-f", "v1.0.0")

    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"
    assert Path(db.get(deployment.id).outputs["path"]).read_text() == "original"


def test_local_example_pattern_deploys():
    deployment = _accept("local-file", {"filename": "x.txt", "content": "local"})
    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"
    assert Path(db.get(deployment.id).outputs["path"]).read_text() == "local"


def test_rule_the_api_cannot_lift_still_fails_in_terraform_with_its_message(pattern_repo):
    main = pattern_repo / "main.tf"
    rule = """
variable "content" {
  type = string
  validation {
    condition     = var.content != var.filename
    error_message = "content must differ from filename."
  }
}
"""
    main.write_text(re.sub(r'variable "content" \{.*?\n\}\n', rule, main.read_text(), flags=re.S))
    git(pattern_repo, "commit", "-qam", "unliftable rule"), git(pattern_repo, "tag", "v1.3.0")
    deployment = _accept("demo", {"filename": "same", "content": "same"})

    assert asyncio.run(_run_workflow(deployment.id)) == "failed"

    result = db.get(deployment.id)
    assert result.state == State.failed
    assert "terraform plan failed" in result.error
    assert "content must differ from filename." in result.error


def test_azurerm_backend_without_state_settings_fails_clearly(pattern_repo):
    main = pattern_repo / "main.tf"
    main.write_text(
        main.read_text().replace("required_version", 'backend "azurerm" {}\n  required_version', 1)
    )
    git(pattern_repo, "commit", "-qam", "remote state"), git(pattern_repo, "tag", "v2.0.0")
    deployment = _accept("demo", {"filename": "a.txt", "content": "x"})

    assert asyncio.run(_run_workflow(deployment.id)) == "failed"
    assert "FORGEAPI_STATE_* is not configured" in db.get(deployment.id).error


def test_destroy_removes_what_the_deployment_created():
    deployment = _accept("demo", {"filename": "gone.txt", "content": "x"})
    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"
    written = Path(db.get(deployment.id).outputs["path"])
    assert written.exists()

    assert asyncio.run(_run_workflow(deployment.id, DestroyWorkflow)) == "destroyed"

    assert not written.exists()
    assert db.get(deployment.id).state == State.destroyed
    assert "Destroy complete" in terraform.log_path(deployment.id).read_text()


def test_retry_after_failure_finishes_with_the_same_workspace_and_state(monkeypatch):
    deployment = _accept("demo", {"filename": "retry.txt", "content": "second time"})
    monkeypatch.setattr(settings, "terraform_bin", "terraform-not-installed")
    assert asyncio.run(_run_workflow(deployment.id)) == "failed"
    assert db.get(deployment.id).state == State.failed

    monkeypatch.setattr(settings, "terraform_bin", "terraform")
    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"

    result = db.get(deployment.id)
    assert result.state == State.succeeded and result.error is None
    assert Path(result.outputs["path"]).read_text() == "second time"


def test_identity_handed_to_terraform(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "azure_tenant_id", "tenant")
    monkeypatch.setattr(settings, "azure_subscription_id", "sub")
    monkeypatch.setattr(settings, "azure_client_id", "client")
    monkeypatch.setattr(settings, "azure_client_certificate_path", tmp_path / "client.pfx")
    monkeypatch.setattr(settings, "state_resource_group", "rg")
    monkeypatch.setattr(settings, "state_storage_account", "acct")

    local = terraform._env()
    assert local["ARM_CLIENT_CERTIFICATE_PATH"].endswith("client.pfx")
    assert "ARM_USE_MSI" not in local
    assert "-backend-config=use_msi=true" not in terraform._backend_args("dep_x")

    monkeypatch.setattr(settings, "azure_use_managed_identity", True)
    monkeypatch.setattr(settings, "azure_managed_identity_client_id", "uami-client")
    hosted = terraform._env()
    assert hosted["ARM_USE_MSI"] == "true"
    assert hosted["ARM_MSI_ENDPOINT"].startswith("http://127.0.0.1:")
    assert hosted["ARM_CLIENT_ID"] == "uami-client"
    assert "ARM_CLIENT_CERTIFICATE_PATH" not in hosted  # managed identity wins; no cert handed over
    args = terraform._backend_args("dep_x")
    assert "-backend-config=use_msi=true" in args
    assert "-backend-config=key=deployments/dep_x.tfstate" in args


def test_update_reapplies_new_inputs_in_place():
    deployment = _accept("local-file", {"filename": "same.txt", "content": "first"})
    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"
    written = Path(db.get(deployment.id).outputs["path"])

    db.respec(deployment.id, {"filename": "same.txt", "content": "second"}, None, None)
    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"

    assert written.read_text() == "second"
    log = terraform.log_path(deployment.id).read_text()
    assert "1 to add, 0 to change, 1 to destroy" in log  # a change against existing state


def test_version_change_is_refused_when_state_is_local():
    deployment = _accept("demo", {"filename": "a.txt", "content": "x"}, "v1.0.0")
    assert asyncio.run(_run_workflow(deployment.id)) == "succeeded"

    newer = catalog.resolve("demo", "v1.1.0")
    db.respec(deployment.id, deployment.inputs, newer.version, newer.commit)
    assert asyncio.run(_run_workflow(deployment.id)) == "failed"
    assert "keeps local state" in db.get(deployment.id).error


def test_identity_check_example_is_valid_terraform():
    terraform.deployment_dir("dep_check").joinpath("work").mkdir(parents=True)
    example = Path(__file__).resolve().parent.parent / "examples" / "azure-identity-check"
    terraform._run("dep_check", "init", "-no-color", "-backend=false", f"-from-module={example}")
    terraform._run("dep_check", "validate", "-no-color")


def test_federated_identity_handed_to_terraform(monkeypatch):
    from app import azure_identity

    monkeypatch.setattr(settings, "azure_tenant_id", "tenant")
    monkeypatch.setattr(settings, "azure_client_id", "ignored-when-federating")
    monkeypatch.setattr(settings, "azure_use_managed_identity", True)
    monkeypatch.setattr(settings, "azure_federated_client_id", "terraform-app")
    monkeypatch.setattr(settings, "state_resource_group", "rg")
    monkeypatch.setattr(settings, "state_storage_account", "acct")
    monkeypatch.setattr(azure_identity, "federation_token", lambda: "mi-token")

    env = terraform._env()
    assert (env["ARM_USE_OIDC"], env["ARM_CLIENT_ID"], env["ARM_OIDC_TOKEN"]) == (
        "true", "terraform-app", "mi-token",
    )  # fmt: skip
    assert "ARM_USE_MSI" not in env and "ARM_CLIENT_CERTIFICATE_PATH" not in env
    args = terraform._backend_args("dep_x")
    assert "-backend-config=use_oidc=true" in args and "-backend-config=use_msi=true" not in args
    assert not any("mi-token" in a for a in args)  # the token never goes on a command line


def test_msi_shim_speaks_the_vm_metadata_protocol(monkeypatch):
    import json
    import time
    import urllib.error
    import urllib.request
    from types import SimpleNamespace

    from app import azure_identity, msi_shim

    asked = []

    class FakeCredential:
        def get_token(self, scope):
            asked.append(scope)
            return SimpleNamespace(token="tok-123", expires_on=time.time() + 3600)

    monkeypatch.setattr(azure_identity, "credential", lambda: FakeCredential())
    url = (
        msi_shim.endpoint()
        + "?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F"
    )

    request = urllib.request.Request(url, headers={"Metadata": "true"})
    body = json.load(urllib.request.urlopen(request))
    assert body["access_token"] == "tok-123" and body["token_type"] == "Bearer"
    assert body["resource"] == "https://management.azure.com/"
    assert 3500 < int(body["expires_in"]) <= 3600
    assert asked == ["https://management.azure.com/.default"]

    for bad in (
        urllib.request.Request(url),
        urllib.request.Request(url.split("?")[0], headers={"Metadata": "true"}),
    ):
        try:
            urllib.request.urlopen(bad)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as err:
            assert err.code == 400
    assert msi_shim.endpoint() + "" == url.split("?")[0]  # started once, stable
