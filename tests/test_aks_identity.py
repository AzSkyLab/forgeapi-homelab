"""AKS workload identity: _env sets ARM_USE_AKS_WORKLOAD_IDENTITY=true and falls back to the
pod-webhook-injected AZURE_CLIENT_ID/AZURE_TENANT_ID when no explicit setting wins; _backend_args
adds use_aks_workload_identity=true instead of use_oidc/use_msi. Real subprocess boundary, same
pattern as tests/test_child_env.py: a fake `terraform` dumps its own received environment.

Verified: the env/backend-arg wiring in this repo's code, and that explicit settings still beat
the webhook-injected values. NOT verified: whether the azurerm provider/backend actually honor
ARM_USE_AKS_WORKLOAD_IDENTITY / use_aks_workload_identity against a real AKS cluster -- taken
from the task description's claim about provider support, not confirmed against the provider's
own source or docs from this sandbox.
"""

import json
from pathlib import Path

import pytest

from app import terraform
from app.settings import Settings, settings


def _fake_bin(tmp_path):
    path = tmp_path / "terraform"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "open(sys.argv[-1], 'w').write(json.dumps(dict(os.environ)))\n"
    )
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def workload_identity(monkeypatch):
    monkeypatch.setattr(settings, "azure_use_aks_workload_identity", True)
    # isolated_settings (conftest.py) already clears these, but spell it out: AKS workload
    # identity is exercised alone here, not layered on another identity mode.
    monkeypatch.setattr(settings, "azure_federated_client_id", None)
    monkeypatch.setattr(settings, "azure_use_managed_identity", False)
    monkeypatch.setattr(settings, "azure_client_certificate_path", None)


def _child_env(tmp_path, monkeypatch, command="plan"):
    monkeypatch.setattr(settings, "terraform_bin", str(_fake_bin(tmp_path)))
    (terraform.deployment_dir("dep-aks") / "work").mkdir(parents=True, exist_ok=True)
    out = tmp_path / f"{command}.json"
    terraform._run("dep-aks", command, str(out))
    return json.loads(out.read_text())


def test_env_sets_use_aks_workload_identity(tmp_path, monkeypatch):
    env = _child_env(tmp_path, monkeypatch)
    assert env["ARM_USE_AKS_WORKLOAD_IDENTITY"] == "true"


def test_env_falls_back_to_webhook_injected_client_and_tenant_id(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.setattr(settings, "azure_tenant_id", None)
    monkeypatch.setenv("AZURE_CLIENT_ID", "webhook-client-id")
    monkeypatch.setenv("AZURE_TENANT_ID", "webhook-tenant-id")
    env = _child_env(tmp_path, monkeypatch)
    assert env["ARM_CLIENT_ID"] == "webhook-client-id"
    assert env["ARM_TENANT_ID"] == "webhook-tenant-id"


def test_explicit_settings_beat_the_webhook_injected_values(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "azure_client_id", "explicit-client-id")
    monkeypatch.setenv("AZURE_CLIENT_ID", "webhook-client-id")
    env = _child_env(tmp_path, monkeypatch)
    assert env["ARM_CLIENT_ID"] == "explicit-client-id"


def test_no_arm_client_id_set_when_neither_setting_nor_webhook_value_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "azure_client_id", None)
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    env = _child_env(tmp_path, monkeypatch)
    assert "ARM_CLIENT_ID" not in env


def test_no_forgeapi_leak(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGEAPI_GITHUB_TOKEN", "env-secret")
    env = _child_env(tmp_path, monkeypatch)
    assert not [k for k in env if k.startswith("FORGEAPI_")]


def test_backend_args_use_aks_workload_identity(monkeypatch):
    monkeypatch.setattr(settings, "state_resource_group", "rg")
    monkeypatch.setattr(settings, "state_storage_account", "sa")
    args = terraform._backend_args("dep-aks-backend")
    assert "-backend-config=use_aks_workload_identity=true" in args
    assert not any("use_oidc" in a or "use_msi" in a for a in args)


@pytest.mark.parametrize(
    "conflict",
    [
        {"azure_federated_client_id": "fed-client-id"},
        {"azure_use_managed_identity": True},
        {"azure_client_certificate_path": Path("/tmp/does-not-matter.pfx")},
    ],
)
def test_mutually_exclusive_with_other_identity_modes(conflict):
    kwargs = {
        "azure_use_aks_workload_identity": True,
        "azure_federated_client_id": None,
        "azure_use_managed_identity": False,
        "azure_client_certificate_path": None,
    }
    kwargs.update(conflict)
    with pytest.raises(ValueError, match="mutually exclusive|cannot be combined"):
        Settings(**kwargs)


def test_aks_workload_identity_alone_is_accepted():
    Settings(
        azure_use_aks_workload_identity=True,
        azure_federated_client_id=None,
        azure_use_managed_identity=False,
        azure_client_certificate_path=None,
    )
