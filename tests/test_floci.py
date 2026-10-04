"""Real HTTP API → Temporal worker → Terraform → AWS/Azure/GCP Floci.

Opt in: uv run pytest --floci tests/test_floci.py -v
Emulators must already be running from compose.floci.yaml. No cloud credentials are used.
"""

import asyncio
import os
import ssl
import uuid
from pathlib import Path
from threading import Thread

import httpx
import pytest
import yaml

from app import ledger
from app.settings import settings
from app.worker import build_worker
from deploy.emulator.storage_proxy import server
from tests.conftest import git
from tests.operation_support import destroy_leftovers, phase_done, temporal_api

pytestmark = pytest.mark.floci
ROOT = Path(__file__).resolve().parent.parent
AZURE_SUBSCRIPTION = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def floci_environment(request, monkeypatch, tmp_path, placed):
    if not request.config.getoption("--floci"):
        pytest.skip("pass --floci with AWS, Azure and GCP emulators running")
    for key in os.environ:
        if key.startswith(("ARM_", "AWS_", "AZURE_", "GOOGLE_", "GCLOUD_", "CLOUDSDK_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    pem = httpx.get("http://localhost:4577/_floci/tls-cert", timeout=10)
    pem.raise_for_status()
    assert "BEGIN CERTIFICATE" in pem.text
    certificate = tmp_path / "emulator-ca.crt"
    system_ca = Path(ssl.get_default_verify_paths().cafile).read_text()
    certificate.write_text(system_ca + "\n" + pem.text)
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    specs = {}
    for provider in ("aws", "azure", "gcp"):
        repo = tmp_path / f"floci-{provider}"
        repo.mkdir()
        (repo / "main.tf").write_text(
            (
                ROOT / "examples" / f"floci-{'placed-' if placed else ''}{provider}" / "main.tf"
            ).read_text()
        )
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "emulator fixture")
        git(repo, "tag", "v1.0.0")
        specs[f"floci-{provider}"] = {"repo": f"file://{repo}"}
        if placed:
            specs[f"floci-{provider}"]["cloud"] = provider
    settings.catalog_path.write_text(yaml.safe_dump({"patterns": specs}))
    if placed:
        monkeypatch.setattr(settings, "dev_groups", "emulator")
        monkeypatch.setattr(
            settings,
            "tenants_yaml",
            yaml.safe_dump(
                {
                    "business_units": {
                        "work": {
                            "groups": ["emulator"],
                            "patterns": list(specs),
                            "environments": {
                                "dev": {
                                    "targets": {
                                        "azure": {
                                            "subscription_id": AZURE_SUBSCRIPTION,
                                            "region": "eastus",
                                        },
                                        "aws": {
                                            "aws_account_id": "000000000000",
                                            "region": "us-east-1",
                                        },
                                        "gcp": {
                                            "project_id": "forgeapi-emulator",
                                            "region": "us-central1",
                                        },
                                    }
                                }
                            },
                        }
                    }
                }
            ),
        )
    proxy = server(port=0)
    thread = Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", f"http://127.0.0.1:{proxy.server_port}")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")
    try:
        yield
    finally:
        leftovers = destroy_leftovers(AZURE_SUBSCRIPTION)
        if leftovers:
            pytest.fail(f"emulator resources leaked in {leftovers}")
        proxy.shutdown()
        proxy.server_close()
        thread.join()


@pytest.mark.parametrize("provider", ["aws", "azure", "gcp"])
@pytest.mark.parametrize("placed", [False, True], ids=["unplaced", "placed"])
def test_api_plan_apply_readback_destroy(provider, placed, floci_environment):
    asyncio.run(run_lifecycle(provider, placed))


@pytest.mark.parametrize("placed", [True])
def test_aws_wrong_executor_account_fails_before_creating_bucket(
    placed, floci_environment, monkeypatch
):
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["business_units"]["work"]["environments"]["dev"]["targets"]["aws"][
        "aws_account_id"
    ] = "111111111111"
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))

    async def check():
        async with temporal_api() as (api, temporal), build_worker(temporal):
            name = f"forgeguard{uuid.uuid4().hex[:12]}"
            response = await api.post(
                "/operations",
                json={"pattern": "floci-aws", "environment": "dev", "inputs": {"name": name}},
                headers={"Idempotency-Key": name},
            )
            assert response.status_code == 202
            op = response.json()
            await phase_done(temporal, op["id"], "plan")
            failed = (await api.get(op["links"]["self"])).json()
            assert failed["state"] == "failed"
            assert failed["plan_digest"] is None
            async with httpx.AsyncClient() as cloud:
                assert (await cloud.get(f"http://localhost:4566/{name}")).status_code == 404

    asyncio.run(check())


async def run_lifecycle(provider, placed):
    async with (
        temporal_api() as (api, temporal),
        build_worker(temporal),
        httpx.AsyncClient() as cloud,
    ):
        assert f"floci-{provider}" in (await api.get("/agent")).json()["patterns"]
        query = "?environment=dev" if placed else ""
        described = (await api.get(f"/patterns/floci-{provider}{query}")).json()
        assert "name" in described["input_schema"]["required"]
        if placed:
            assert set(described["input_schema"]["properties"]) == {"name"}
        name = f"forge{uuid.uuid4().hex[:16]}"
        intent = {"pattern": f"floci-{provider}", "version": "v1.0.0", "inputs": {"name": name}}
        if placed:
            intent["environment"] = "dev"
        checked = await api.post("/intents/validate", json=intent)
        assert checked.status_code == 200
        intent["expected_commit"] = checked.json()["commit"]
        headers = {"Idempotency-Key": f"create-{name}"}
        accepted = await api.post("/operations", json=intent, headers=headers)
        assert accepted.status_code == 202
        op = accepted.json()
        assert (await api.post("/operations", json=intent, headers=headers)).json()["id"] == op[
            "id"
        ]
        await phase_done(temporal, op["id"], "plan")
        planned = (await api.get(op["links"]["self"])).json()
        assert planned["state"] == "planned", planned
        assert planned["changes"][0]["actions"] == ["create"]
        if provider == "aws":
            target = f"http://localhost:4566/{name}"
        elif provider == "gcp":
            target = f"http://localhost:4588/storage/v1/b/{name}"
        else:
            target = (
                "http://localhost:4577/subscriptions/00000000-0000-0000-0000-000000000001"
                f"/resourcegroups/{name}?api-version=2024-03-01"
            )
        if placed and provider == "azure":
            target = target.split("?")[0] + (
                f"/providers/Microsoft.Storage/storageAccounts/{name}"
                "?api-version=2023-05-01"
            )
        # Independent provider readback proves a plan alone creates nothing.
        assert (await cloud.get(target)).status_code == 404
        blob = f"http://localhost:4577/{name}/data?restype=container"
        if placed and provider == "azure":
            assert (await cloud.get(blob)).status_code == 404
        execution = {"plan_digest": planned["plan_digest"]}
        assert (await api.post(op["links"]["execute"], json=execution)).status_code == 202
        assert (await api.post(op["links"]["execute"], json=execution)).status_code == 202
        await phase_done(temporal, op["id"], "apply")
        done = (await api.get(op["links"]["self"])).json()
        assert done["state"] == "succeeded", done
        assert done["outputs"]["name"] == name
        if placed:
            assert done["withheld_outputs"] == ["placement_id"]
            assert "placement_id" not in done["outputs"]
        assert (await cloud.get(target)).status_code == 200
        if placed and provider == "azure":
            assert (await cloud.get(blob)).status_code == 200
        events = (await api.get(op["links"]["events"])).json()
        assert [e["outcome"] for e in events["items"]] == [
            "accepted",
            "planning",
            "planned",
            "accepted",
            "applying",
            "succeeded",
        ]
        cleanup_response = await api.post(
            "/operations",
            json={
                **intent,
                "action": "destroy",
                "resource_id": done["resource_id"],
            },
            headers={"Idempotency-Key": f"destroy-{name}"},
        )
        assert cleanup_response.status_code == 202
        cleanup = cleanup_response.json()
        await phase_done(temporal, cleanup["id"], "plan")
        cleanup = (await api.get(cleanup["links"]["self"])).json()
        assert cleanup["state"] == "planned", cleanup
        assert cleanup["changes"][0]["actions"] == ["delete"]
        assert (
            await api.post(
                cleanup["links"]["execute"], json={"plan_digest": cleanup["plan_digest"]}
            )
        ).status_code == 202
        await phase_done(temporal, cleanup["id"], "apply")
        assert ledger.get(cleanup["id"])["state"] == "succeeded"
        assert (await cloud.get(target)).status_code == 404
        if placed and provider == "azure":
            assert (await cloud.get(blob)).status_code == 404
