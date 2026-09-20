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
