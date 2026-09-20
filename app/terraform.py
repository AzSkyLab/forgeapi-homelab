"""Thin subprocess wrapper around the Terraform CLI. One throwaway workspace per deployment."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from app import catalog
from app.settings import settings


class TerraformError(Exception):
    pass


def deployment_dir(deployment_id: str) -> Path:
    return settings.data_dir.resolve() / "deployments" / deployment_id


def log_path(deployment_id: str) -> Path:
    return deployment_dir(deployment_id) / "terraform.log"


def _env() -> dict[str, str]:
    cache = settings.data_dir.resolve() / "plugin-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = {**catalog.git_env(), "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    identity = {
        "ARM_TENANT_ID": settings.azure_tenant_id,
        "ARM_SUBSCRIPTION_ID": settings.azure_subscription_id,
        "ARM_CLIENT_ID": settings.azure_client_id,
    }
    env |= {k: v for k, v in identity.items() if v}
    if settings.azure_use_managed_identity:
        # Read by the azurerm and azuread providers and by the azurerm state backend.
        env["ARM_USE_MSI"] = "true"
    elif settings.azure_client_certificate_path:
        env["ARM_CLIENT_CERTIFICATE_PATH"] = str(settings.azure_client_certificate_path.resolve())
    return env


def _first_error(output: str) -> str:
    """Terraform's own error text (title, location and the pattern's validation message)."""
    start = output.find("Error:")
    text = re.sub(r"[│├─╵╷]+", " ", output[start:]) if start >= 0 else ""
    return " ".join(text.split())[:800]


def _run(deployment_id: str, *args: str) -> str:
    workdir = deployment_dir(deployment_id) / "work"
    result = subprocess.run(
        [settings.terraform_bin, *args],
        cwd=workdir, env=_env(), capture_output=True, text=True, check=False,
    )
    # `output -json` is returned to the caller, not logged: outputs live in the database.
    logged = result.stderr if args[0] == "output" else result.stdout + result.stderr
    shown = " ".join(a for a in args if not a.startswith("-backend-config"))
    with log_path(deployment_id).open("a") as log:
        log.write(f"$ terraform {shown}\n{logged}\n")
    if result.returncode != 0:
        detail = _first_error(result.stderr) or f"exit {result.returncode}"
        raise TerraformError(f"terraform {args[0]} failed: {detail}")
    return result.stdout


def _backend_args(deployment_id: str) -> list[str]:
    if not (settings.state_resource_group and settings.state_storage_account):
        raise TerraformError("pattern needs remote state but FORGEAPI_STATE_* is not configured")
    config = {
        "resource_group_name": settings.state_resource_group,
        "storage_account_name": settings.state_storage_account,
        "container_name": settings.state_container,
        "key": f"deployments/{deployment_id}.tfstate",
        "use_azuread_auth": "true",
    }
    if settings.azure_use_managed_identity:
        config["use_msi"] = "true"
    return [f"-backend-config={k}={v}" for k, v in config.items()]


def prepare(deployment_id: str, source: str, variables: dict[str, Any]) -> None:
    """Workspace with the pattern at its pinned commit, initialised against its state.

    A workspace already holding this source is reused (local-state patterns keep their state
    there). Otherwise the pattern is fetched again; remote state makes that safe on any worker,
    for retries, updates to a new version, and destroys alike."""
    workdir = deployment_dir(deployment_id) / "work"
    marker = workdir / ".forgeapi-source"
    if not marker.exists() or marker.read_text() != source:
        if (workdir / "terraform.tfstate").exists():
            raise TerraformError("cannot change the version of a deployment that keeps local state")
        shutil.rmtree(workdir, ignore_errors=True)
        workdir.mkdir(parents=True)
        _run(deployment_id, "init", "-no-color", "-backend=false", f"-from-module={source}")
        marker.write_text(source)
    (workdir / "terraform.tfvars.json").write_text(json.dumps(variables))
    backend = _backend_args(deployment_id) if catalog.uses_azurerm_backend(workdir) else []
    _run(deployment_id, "init", "-no-color", *backend)


def plan(deployment_id: str, source: str, variables: dict[str, Any]) -> None:
    prepare(deployment_id, source, variables)
    _run(deployment_id, "plan", "-no-color", "-out=tfplan")


def destroy(deployment_id: str, source: str, variables: dict[str, Any]) -> None:
    prepare(deployment_id, source, variables)
    _run(deployment_id, "destroy", "-no-color", "-auto-approve")


def apply(deployment_id: str) -> dict[str, Any]:
    _run(deployment_id, "apply", "-no-color", "tfplan")
    raw = json.loads(_run(deployment_id, "output", "-json"))
    return {name: o["value"] for name, o in raw.items() if not o.get("sensitive")}
