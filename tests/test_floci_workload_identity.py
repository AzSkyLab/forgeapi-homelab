"""Real API → Temporal worker → Terraform → Floci Azure, authenticated by AKS workload identity.

Opt in: uv run pytest --floci tests/test_floci_workload_identity.py -v
The AKS webhook's AZURE_* variables are set by hand; the product must map them to ARM_*.
"""

import asyncio
import os
import ssl
import uuid
from pathlib import Path

import httpx
import pytest
import yaml

from app import terraform
from app.settings import settings
from app.worker import build_worker
from tests.conftest import git
from tests.operation_support import destroy_leftovers, phase_done, temporal_api

pytestmark = pytest.mark.floci
ROOT = Path(__file__).resolve().parent.parent
SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"
CLIENT, TENANT = str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture
def workload_identity(request, monkeypatch, tmp_path):
    if not request.config.getoption("--floci"):
        pytest.skip("pass --floci with the Azure emulator running")
    for key in os.environ:
        if key.startswith(("ARM_", "AWS_", "AZURE_", "GOOGLE_", "GCLOUD_", "CLOUDSDK_")):
            monkeypatch.delenv(key)
    pem = httpx.get("http://localhost:4577/_floci/tls-cert", timeout=10)
    pem.raise_for_status()
    certificate = tmp_path / "emulator-ca.crt"
    system_ca = Path(ssl.get_default_verify_paths().cafile).read_text()
    certificate.write_text(system_ca + "\n" + pem.text)
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    repo = tmp_path / "floci-wi-azure"
    repo.mkdir()
    (repo / "main.tf").write_text((ROOT / "examples" / "floci-wi-azure" / "main.tf").read_text())
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "emulator fixture")
    git(repo, "tag", "v1.0.0")
    settings.catalog_path.write_text(
        yaml.safe_dump({"patterns": {"floci-wi-azure": {"repo": f"file://{repo}"}}})
    )
    token = tmp_path / "federated-token"
    token.write_text("header.payload.signature")
    monkeypatch.setattr(settings, "azure_use_aks_workload_identity", True)
    monkeypatch.setenv("AZURE_CLIENT_ID", CLIENT)
    monkeypatch.setenv("AZURE_TENANT_ID", TENANT)
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(token))
    try:
        yield token
    finally:
        leftovers = destroy_leftovers(SUBSCRIPTION)
        if leftovers:
            pytest.fail(f"emulator resources leaked in {leftovers}")


def test_terraform_depends_on_the_federated_token_file(workload_identity):
    env = terraform._env()
    assert "ARM_USE_MSI" not in env
    assert env["ARM_USE_AKS_WORKLOAD_IDENTITY"] == "true"
    assert (env["ARM_CLIENT_ID"], env["ARM_TENANT_ID"]) == (CLIENT, TENANT)
    asyncio.run(run(workload_identity))


async def run(token: Path):
    async with (
        temporal_api() as (api, temporal),
        build_worker(temporal),
        httpx.AsyncClient() as cloud,
    ):
        name = f"forge{uuid.uuid4().hex[:16]}"
        group = (
            f"http://localhost:4577/subscriptions/{SUBSCRIPTION}/resourceGroups/{name}"
            "?api-version=2021-04-01"
        )
        intent = {"pattern": "floci-wi-azure", "version": "v1.0.0", "inputs": {"name": name}}

        async def operate(body, key):
            accepted = await api.post("/operations", json=body, headers={"Idempotency-Key": key})
            assert accepted.status_code == 202
            op = accepted.json()
            await phase_done(temporal, op["id"], "plan")
            return op, (await api.get(op["links"]["self"])).json()

        async def execute(op, planned):
            execution = {"plan_digest": planned["plan_digest"]}
            assert (await api.post(op["links"]["execute"], json=execution)).status_code == 202
            await phase_done(temporal, op["id"], "apply")
            return (await api.get(op["links"]["self"])).json()

        assert (await cloud.get(group)).status_code == 404
        op, planned = await operate(intent, f"create-{name}")
        assert planned["state"] == "planned", planned
        assert (await cloud.get(group)).status_code == 404
        done = await execute(op, planned)
        assert done["state"] == "succeeded", done
        assert (await cloud.get(group)).status_code == 200

        destroy = {**intent, "action": "destroy", "resource_id": done["resource_id"]}
        token.unlink()
        _, refused = await operate(destroy, f"destroy-missing-{name}")
        assert refused["state"] == "failed", refused
        assert refused["diagnostic"]
        assert str(token.parent) not in refused["diagnostic"]
        assert (await cloud.get(group)).status_code == 200

        token.write_text("header.payload.signature")
        op, planned = await operate(destroy, f"destroy-{name}")
        assert planned["state"] == "planned", planned
        assert (await execute(op, planned))["state"] == "succeeded"
        assert (await cloud.get(group)).status_code == 404
