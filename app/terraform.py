"""Thin subprocess wrapper around the Terraform CLI. One throwaway workspace per deployment."""

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from app import catalog, logs
from app.settings import settings

GRACE = 60  # seconds given to a deadline-killed terraform to react to SIGINT before SIGKILL

# A process started in the background by a shell (`worker &`) inherits SIGINT as ignored, and exec
# keeps an ignored signal ignored: every Terraform child would then ignore the deadline's graceful
# SIGINT and be SIGKILLed after GRACE without writing state. The worker's own disposition is left
# alone (importing this module must not change its Ctrl-C behaviour); only children are reset, and
# only in that case. Ponytail: preexec_fn runs between fork and exec, which is not fully
# fork-safe in a threaded process, so it is used only when the parent ignores SIGINT and does
# nothing but one signal call.
_PARENT_IGNORES_SIGINT = signal.getsignal(signal.SIGINT) == signal.SIG_IGN


def _default_sigint() -> None:
    signal.signal(signal.SIGINT, signal.SIG_DFL)


class TerraformError(Exception):
    pass


class _DeadlineExceeded(Exception):
    pass


# The shared provider cache is not safe while a provider is being installed into it: a second
# `init` corrupts the download, and a `plan`/`apply` elsewhere can read a half-written package.
# `init` is the only writer, so only `init` takes this lock; plan/apply only read it.
_INIT_LOCK = threading.Lock()


def deployment_dir(deployment_id: str) -> Path:
    return settings.data_dir.resolve() / "deployments" / deployment_id


def log_path(deployment_id: str) -> Path:
    return deployment_dir(deployment_id) / "terraform.log"


def _env(subscription_id: str | None = None, token: bool = False) -> dict[str, str]:
    cache = settings.data_dir.resolve() / "plugin-cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = {**catalog.git_env(token=token), "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}
    env["TF_PLUGIN_CACHE_DIR"] = str(cache)
    # Without this, every fresh workspace (no lock file yet) re-extracts providers into the shared
    # cache, rewriting binaries in place that other deployments' plan/apply may be executing:
    # "text file busy". With it, init links the cached copy. Workspaces are throwaway and
    # single-platform, so lock files holding only this platform's checksums are fine.
    env["TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE"] = "true"
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
    elif settings.azure_use_aks_workload_identity:
        # The AKS pod webhook already put AZURE_CLIENT_ID, AZURE_TENANT_ID and
        # AZURE_FEDERATED_TOKEN_FILE in this process's environment (passed through above via
        # catalog.git_env); azurerm reads them itself once this flag is set. Fill ARM_CLIENT_ID/
        # ARM_TENANT_ID from them only when no explicit setting already did.
        env["ARM_USE_AKS_WORKLOAD_IDENTITY"] = "true"
        if "ARM_CLIENT_ID" not in env and env.get("AZURE_CLIENT_ID"):
            env["ARM_CLIENT_ID"] = env["AZURE_CLIENT_ID"]
        if "ARM_TENANT_ID" not in env and env.get("AZURE_TENANT_ID"):
            env["ARM_TENANT_ID"] = env["AZURE_TENANT_ID"]
    return env


def _first_error(output: str) -> str:
    """Terraform's own error text (title, location and the pattern's validation message)."""
    start = output.find("Error:")
    text = re.sub(r"[│├─╵╷]+", " ", output[start:]) if start >= 0 else ""
    return " ".join(text.split())[:800]


def _killpg(pid: int, sig: int) -> None:
    with contextlib.suppress(ProcessLookupError):  # already gone; nothing to signal
        os.killpg(pid, sig)


def _invoke(
    command: list[str], workdir: Path, env: dict[str, str], deadline: float | None
) -> subprocess.CompletedProcess:
    proc = subprocess.Popen(
        command,
        cwd=workdir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        preexec_fn=_default_sigint if _PARENT_IGNORES_SIGINT else None,
    )
    try:
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        stdout, stderr = proc.communicate(timeout=remaining)
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        _killpg(proc.pid, signal.SIGINT)  # terraform stops gracefully and writes state
        try:
            proc.communicate(timeout=GRACE)
        except subprocess.TimeoutExpired:
            _killpg(proc.pid, signal.SIGKILL)
            proc.communicate()  # reap
        raise _DeadlineExceeded from None


def _run(
    deployment_id: str,
    *args: str,
    subscription_id: str | None = None,
    _unlock_once: bool = True,
    safe_logs: bool = False,
    deadline: float | None = None,
) -> str:
    workdir = deployment_dir(deployment_id) / "work"
    shown = " ".join(a for a in args if not a.startswith("-backend-config"))
    command = [settings.terraform_bin, *args]
    try:
        if args[0] == "init":
            # Only `init` fetches pattern modules from git, so only it needs the token.
            # The wait for the lock counts against the deadline, and nothing has started yet if
            # it runs out, so the phase fails cleanly rather than becoming uncertain.
            wait = -1 if deadline is None else max(0.0, deadline - time.monotonic())
            if not _INIT_LOCK.acquire(timeout=wait):
                raise TerraformError(
                    "terraform init exceeded its deadline waiting for the provider cache"
                )
            try:
                result = _invoke(command, workdir, _env(subscription_id, token=True), deadline)
            finally:
                _INIT_LOCK.release()
        else:
            result = _invoke(command, workdir, _env(subscription_id), deadline)
    except _DeadlineExceeded:
        logs.append(deployment_id, f"$ terraform {shown}\nexit_code=timeout\n")
        raise TerraformError(f"terraform {args[0]} exceeded its deadline") from None
    # `output -json` is returned to the caller, not logged: outputs live in the database.
    logged = result.stderr if args[0] in {"output", "show"} else result.stdout + result.stderr
    if safe_logs:
        # The operation activity exposes structured outcomes. Raw provider diagnostics may
        # contain auth errors or values, so its persistent log contains command receipts only.
        logged = f"exit_code={result.returncode}"
    logs.append(deployment_id, f"$ terraform {shown}\n{logged}\n")
    if result.returncode != 0:
        lock = lock_id(result.stderr)
        if lock and _unlock_once:
            # forgeapi runs one job per deployment at a time, so a held lock can only belong to
            # a run whose worker was stopped mid-way (scale-in, restart). Release it and go on.
            logs.append(deployment_id, f"# state lock {lock} belongs to an interrupted run\n")
            _run(deployment_id, "force-unlock", "-force", lock, subscription_id=subscription_id)
            return _run(deployment_id, *args, subscription_id=subscription_id, _unlock_once=False)
        detail = _first_error(result.stderr) or f"exit {result.returncode}"
        raise TerraformError(f"terraform {args[0]} failed: {detail}")
    return result.stdout


def lock_id(stderr: str) -> str | None:
    """The lock ID from Terraform's "Error acquiring the state lock" message, if that is it."""
    if "Error acquiring the state lock" not in stderr:
        return None
    match = re.search(r"^\s*ID:\s+([0-9a-fA-F-]{8,})\s*$", stderr, re.M)
    return match.group(1) if match else None


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
    elif settings.azure_use_aks_workload_identity:
        config["use_aks_workload_identity"] = "true"
    return [f"-backend-config={k}={v}" for k, v in config.items()]


def prepare(
    deployment_id: str,
    source: str,
    variables: dict[str, Any],
    subscription_id: str | None = None,
    *,
    safe_logs: bool = False,
    deadline: float | None = None,
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
        _run(
            deployment_id,
            *fetch,
            subscription_id=subscription_id,
            safe_logs=safe_logs,
            _unlock_once=not safe_logs,
            deadline=deadline,
        )
        marker.write_text(source)
    (workdir / "terraform.tfvars.json").write_text(json.dumps(variables))
    backend = _backend_args(deployment_id) if catalog.uses_azurerm_backend(workdir) else []
    _run(
        deployment_id,
        "init",
        "-no-color",
        *backend,
        subscription_id=subscription_id,
        safe_logs=safe_logs,
        _unlock_once=not safe_logs,
        deadline=deadline,
    )


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
