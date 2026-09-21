"""Thin subprocess wrapper around the Terraform CLI. One throwaway workspace per deployment."""

import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

from app import catalog, logs
from app.settings import settings


class TerraformError(Exception):
    pass


class _CacheGate:
    """Reader/writer gate for Terraform's shared provider cache.

    The cache is not safe while a provider is being installed into it: a second `init` corrupts
    the download, and a `plan`/`apply` in another workspace can read a half-written package
    ("cached package does not match any of the checksums"). So `init` (the only writer) runs
    alone, and every other command runs in parallel with its peers. Waiting writers go first so
    a steady stream of plans cannot starve an init."""

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._readers = 0
        self._writing = False
        self._writers_waiting = 0

    @contextlib.contextmanager
    def held(self, *, exclusive: bool):
        with self._changed:
            if exclusive:
                self._writers_waiting += 1
                self._changed.wait_for(lambda: not self._writing and self._readers == 0)
                self._writers_waiting -= 1
                self._writing = True
            else:
                self._changed.wait_for(lambda: not self._writing and not self._writers_waiting)
                self._readers += 1
        try:
            yield
        finally:
            with self._changed:
                if exclusive:
                    self._writing = False
                else:
                    self._readers -= 1
                self._changed.notify_all()


_CACHE = _CacheGate()


def deployment_dir(deployment_id: str) -> Path:
    return settings.data_dir.resolve() / "deployments" / deployment_id


def log_path(deployment_id: str) -> Path:
    return deployment_dir(deployment_id) / "terraform.log"


def _env(subscription_id: str | None = None) -> dict[str, str]:
    cache = settings.data_dir.resolve() / "plugin-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = {**catalog.git_env(), "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    identity = {
        "ARM_TENANT_ID": settings.azure_tenant_id,
        # The business unit's subscription when placement applies, else the platform default.
        "ARM_SUBSCRIPTION_ID": subscription_id or settings.azure_subscription_id,
        "ARM_CLIENT_ID": settings.azure_client_id,
    }
    env |= {k: v for k, v in identity.items() if v}
    if settings.azure_federated_client_id:
        # Read by the azurerm and azuread providers and by the azurerm state backend. The token
        # is short-lived and lives only in this subprocess's environment.
        from app.azure_identity import federation_token

        env["ARM_USE_OIDC"] = "true"
        env["ARM_CLIENT_ID"] = settings.azure_federated_client_id
        env["ARM_OIDC_TOKEN"] = federation_token()
    elif settings.azure_use_managed_identity:
        # Terraform only speaks the VM metadata protocol; serve it locally (see app/msi_shim.py).
        from app import msi_shim

        env["ARM_USE_MSI"] = "true"
        env["ARM_MSI_ENDPOINT"] = msi_shim.endpoint()
        if settings.azure_managed_identity_client_id:
            env["ARM_CLIENT_ID"] = settings.azure_managed_identity_client_id
    elif settings.azure_client_certificate_path:
        env["ARM_CLIENT_CERTIFICATE_PATH"] = str(settings.azure_client_certificate_path.resolve())
    return env


def _first_error(output: str) -> str:
    """Terraform's own error text (title, location and the pattern's validation message)."""
    start = output.find("Error:")
    text = re.sub(r"[│├─╵╷]+", " ", output[start:]) if start >= 0 else ""
    return " ".join(text.split())[:800]


def _run(deployment_id: str, *args: str, subscription_id: str | None = None) -> str:
    workdir = deployment_dir(deployment_id) / "work"
    with _CACHE.held(exclusive=args[0] == "init"):
        result = subprocess.run(
            [settings.terraform_bin, *args],
            cwd=workdir,
            env=_env(subscription_id),
            capture_output=True,
            text=True,
            check=False,
        )
    # `output -json` is returned to the caller, not logged: outputs live in the database.
    logged = result.stderr if args[0] == "output" else result.stdout + result.stderr
    shown = " ".join(a for a in args if not a.startswith("-backend-config"))
    logs.append(deployment_id, f"$ terraform {shown}\n{logged}\n")
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
    if settings.azure_subscription_id:
        # State lives in the platform subscription even when the deployment targets another.
        config["subscription_id"] = settings.azure_subscription_id
    if settings.azure_federated_client_id:
        config["use_oidc"] = "true"
    elif settings.azure_use_managed_identity:
        config["use_msi"] = "true"
    return [f"-backend-config={k}={v}" for k, v in config.items()]


def prepare(
    deployment_id: str, source: str, variables: dict[str, Any], subscription_id: str | None = None
) -> None:
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
        fetch = ("init", "-no-color", "-backend=false", f"-from-module={source}")
        _run(deployment_id, *fetch, subscription_id=subscription_id)
        marker.write_text(source)
    (workdir / "terraform.tfvars.json").write_text(json.dumps(variables))
    backend = _backend_args(deployment_id) if catalog.uses_azurerm_backend(workdir) else []
    _run(deployment_id, "init", "-no-color", *backend, subscription_id=subscription_id)


def _fingerprint(source: str, variables: dict[str, Any], subscription_id: str | None) -> str:
    """Identifies exactly what a saved plan was made from."""
    material = json.dumps([source, variables, subscription_id], sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def plan(
    deployment_id: str, source: str, variables: dict[str, Any], subscription_id: str | None = None
) -> None:
    prepare(deployment_id, source, variables, subscription_id)
    _run(deployment_id, "plan", "-no-color", "-out=tfplan", subscription_id=subscription_id)
    stamp = deployment_dir(deployment_id) / "work" / "tfplan.fingerprint"
    stamp.write_text(_fingerprint(source, variables, subscription_id))


def destroy(
    deployment_id: str, source: str, variables: dict[str, Any], subscription_id: str | None = None
) -> None:
    prepare(deployment_id, source, variables, subscription_id)
    _run(deployment_id, "destroy", "-no-color", "-auto-approve", subscription_id=subscription_id)


def apply(
    deployment_id: str, source: str, variables: dict[str, Any], subscription_id: str | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Apply the saved plan for exactly this request. Returns (outputs, withheld output names).

    Plan and apply are separate steps and may run on different worker replicas, each with its
    own disk. If this replica does not hold a plan made from this source, these variables and
    this subscription (another replica planned it, or a plan from an earlier request is lying
    around), it rebuilds the workspace and plans again. Remote state makes that safe."""
    workdir = deployment_dir(deployment_id) / "work"
    saved, stamp = workdir / "tfplan", workdir / "tfplan.fingerprint"
    expected = _fingerprint(source, variables, subscription_id)
    if not saved.exists() or not stamp.exists() or stamp.read_text() != expected:
        note = "# no saved plan for this request on this worker; planning here\n"
        logs.append(deployment_id, note)
        plan(deployment_id, source, variables, subscription_id)
    _run(deployment_id, "apply", "-no-color", "tfplan", subscription_id=subscription_id)
    saved.unlink(missing_ok=True)  # a plan is applied once
    stamp.unlink(missing_ok=True)
    raw = json.loads(_run(deployment_id, "output", "-json", subscription_id=subscription_id))
    return split_outputs(raw)


def split_outputs(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(values safe to store and return, names of sensitive outputs that were withheld).

    A sensitive value is never stored or returned by the API. Its *name* is, so that a pattern
    which marks something sensitive without putting it in a Key Vault is visible rather than
    silently lossy."""
    shown = {name: o["value"] for name, o in raw.items() if not o.get("sensitive")}
    return shown, sorted(name for name, o in raw.items() if o.get("sensitive"))
