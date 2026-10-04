"""`diagnostic`: a sanitized Terraform failure reason on a failed or uncertain operation."""

import pytest
import yaml

from app import terraform
from app.contracts import Operation, public_operation
from app.operation_activities import diagnostic
from app.settings import settings
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit

GUID = "0b7c6e5a-1111-4222-8333-444455556666"
OP = {"cloud_target": {"subscription_id": "my-hidden-sub", "region": "eastus"}}


def tf(message):
    return terraform.TerraformError(message)


def test_precondition_message_kept_with_command_prefix():
    text = diagnostic(OP, tf("terraform plan failed: Error: bucket names must start with team-"))
    assert text.startswith("terraform plan failed:")
    assert "bucket names must start with team-" in text


def test_identifiers_are_redacted():
    text = diagnostic(
        OP,
        tf(
            f"terraform apply failed: Error: sub {GUID} acct 123456789012 at "
            "https://example.com/x?y=1 id /subscriptions/abc/resourceGroups/rg "
            "placed in My-Hidden-Sub ok 9876543210987"
        ),
    )
    for leaked in (GUID, "123456789012", "https://", "example.com", "/subscriptions/", "hidden"):
        assert leaked.lower() not in text.lower()
    assert text.count("[redacted]") >= 5
    assert "9876543210987" in text  # not a standalone 12-digit number


@pytest.mark.parametrize(
    "detail",
    [
        "Error: AADSTS7000215 invalid secret",
        "Error: InvalidClientTokenId bad",
        "Error: 403 Forbidden",
        "Error: AccessDenied for user",
        "Error: The security token included is expired",
    ],
)
def test_auth_errors_are_replaced(detail):
    text = diagnostic(OP, tf(f"terraform plan failed: {detail}"))
    assert text == (
        "terraform plan failed: the cloud provider rejected the credentials or permissions"
    )


def test_truncated_and_deadline_passes_through():
    assert len(diagnostic(OP, tf("terraform plan failed: " + "x" * 2000))) == 400
    assert diagnostic(OP, tf("terraform apply exceeded its deadline")) == (
        "terraform apply exceeded its deadline"
    )


def test_non_terraform_exceptions_get_none():
    assert diagnostic(OP, ValueError("unsafe plan summary")) is None
    assert diagnostic(OP, KeyError("x")) is None


def register_failing(tmp_path):
    repo = tmp_path / "failing-pattern"
    repo.mkdir()
    (repo / "main.tf").write_text(
        """
terraform {
  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

variable "filename" {
  type = string
}

resource "local_file" "this" {
  filename = "${path.module}/out/${var.filename}"
  content  = "x"
  lifecycle {
    precondition {
      condition     = var.filename == "never"
      error_message = "bucket names must start with team-"
    }
  }
}
"""
    )
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"]["failing"] = {"local": str(repo)}
    settings.catalog_path.write_text(yaml.safe_dump(catalog))


def test_failed_plan_carries_diagnostic(agent_client, recorded_dispatcher, tmp_path):
    register_failing(tmp_path)
    created = submit(
        agent_client, "diag-fail", pattern="failing", version=None, inputs={"filename": "a.txt"}
    ).json()
    assert recorded_dispatcher.run_next()
    failed = agent_client.get(created["links"]["self"]).json()
    assert failed["state"] == "failed", failed
    assert "terraform plan failed" in failed["diagnostic"]
    assert "bucket names must start with team-" in failed["diagnostic"]
    via_v1 = agent_client.get(f"/v1/operations/{failed['id']}").json()
    assert via_v1["diagnostic"] == failed["diagnostic"]
    assert agent_client.get("/agent").json()["capabilities"]["failure_diagnostics"] is True
    assert Operation.model_validate(via_v1).diagnostic == failed["diagnostic"]


def test_successful_operation_has_null_diagnostic(agent_client, recorded_dispatcher):
    created = submit(
        agent_client,
        "diag-ok",
        pattern="local-file",
        version=None,
        inputs={"filename": "ok.txt", "content": "c"},
    ).json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(created["links"]["self"]).json()
    assert planned["state"] == "planned"
    assert planned["diagnostic"] is None


def test_old_operation_without_the_field_is_null():
    op = {
        "id": "o",
        "resource_id": "r",
        "state": "failed",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    assert public_operation(op)["diagnostic"] is None


def test_unrecognised_terraform_error_text_is_not_shown():
    assert diagnostic(OP, tf("raw-auth-error-secret")) is None


@pytest.mark.parametrize("command", ["plan", "apply", "show"])
def test_state_lock_text_is_never_shown(command):
    lock = (
        f"terraform {command} failed: Error: Error acquiring the state lock Lock Info: "
        "ID: 1234abcd-1234 Who: alice@corp.example"
    )
    assert diagnostic(OP, tf(lock)) is None


def test_injected_platform_values_are_redacted():
    op = {
        **OP,
        "injected": {
            "resource_group_name": "rg-finance-dev",
            "subnet_id": "snet-abc123",
            "location": "westeurope",
            "tags": {"owner": "platform-team"},
        },
    }
    text = diagnostic(
        op,
        tf(
            "terraform plan failed: Error: group RG-Finance-Dev has no subnet snet-abc123 "
            "owned by platform-team in westeurope"
        ),
    )
    for leaked in ("rg-finance-dev", "snet-abc123", "platform-team"):
        assert leaked not in text.lower()
    assert "westeurope" in text  # a location is not placement identity
