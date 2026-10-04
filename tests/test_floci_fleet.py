"""Drift and upgrade detection on all three Floci emulators.

HTTP API (ASGI) -> real Temporal dev server -> production worker -> Terraform -> Floci, with
independent readbacks and an out-of-band delete made directly against the emulator.
Opt in: uv run pytest --floci tests/test_floci_fleet.py -v
Emulators must already be running from compose.floci.yaml. No cloud credentials are used.
"""

import asyncio
import uuid

import httpx
import pytest
import yaml

from app.settings import settings
from app.worker import build_worker
from tests.conftest import git
from tests.operation_support import phase_done, temporal_api
from tests.test_floci import ROOT
from tests.test_floci_placed_features import (
    PROVIDERS,
    assert_no_placement,
    create,
    destroy,
    execute,
    intent,
    json_text,
    make_repo,
    new_name,
    placed_floci,  # noqa: F401
    plan,
    status,
    target_url,
)

pytestmark = pytest.mark.floci

# The primary object each pattern manages, deleted directly in the emulator.
DELETED = {
    "aws": "aws_s3_bucket.test",
    # Deleting the account leaves its container behind in Floci Azure (a recreate then fails with
    # "already exists"), so the container is the object removed there.
    "azure": "azurerm_storage_container.test",
    "gcp": "google_storage_bucket.test",
}


# Floci's Azure and GCP providers answer a refresh differently from the apply that wrote the
# state, so every refresh-only plan reports benign `update` drift even on an untouched resource
# (seen 2026-10-03: azurerm_storage_account, google_storage_bucket). Floci AWS settles after one
# apply. Where that noise exists, "in sync" means "only `update` drift, no create/delete".
NOISY = {"azure", "gcp"}
STATE_S3 = "http://localhost:4566"


def backend(bucket: str, key: str) -> str:
    """Remote state: the API refuses to change the version of a local-state deployment."""
    return f"""
  backend "s3" {{
    bucket                      = "{bucket}"
    key                         = "{key}"
    region                      = "us-east-1"
    access_key                  = "test"
    secret_key                  = "test"
    use_path_style              = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    endpoints = {{
      s3 = "{STATE_S3}"
    }}
  }}
"""


def with_remote_state(tmp_path, provider: str, bucket: str, key: str) -> None:
    """Re-register floci-<provider> from a repo whose root module keeps state in Floci S3."""
    text = (ROOT / "examples" / f"floci-placed-{provider}" / "main.tf").read_text()
    text = text.replace("terraform {\n", "terraform {" + backend(bucket, key) + "\n", 1)
    assert "backend" in text
    spec = {
        **make_repo(tmp_path, f"fleet-{provider}", text),
        "cloud": provider,
    }
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"][f"floci-{provider}"] = spec
    settings.catalog_path.write_text(yaml.safe_dump(catalog))


def assert_in_sync(provider: str, drift: list, status_value: str):
    if provider in NOISY:
        assert {d["actions"][0] for d in drift} <= {"update"}, drift
    else:
        assert drift == [] and status_value == "in_sync", (drift, status_value)


def primary_url(provider: str, name: str) -> str:
    if provider == "azure":  # Blob service Get/Delete Container on the pattern's `data` container
        return f"http://localhost:4577/{name}/data?restype=container"
    return target_url(provider, name)


async def primary_status(cloud, provider: str, name: str) -> int:
    return (await cloud.get(primary_url(provider, name))).status_code


async def out_of_band_delete(cloud, provider: str, name: str):
    # S3 DeleteBucket, GCS buckets.delete, Blob Delete Container.
    response = await cloud.delete(primary_url(provider, name))
    assert response.status_code in (200, 202, 204), response.text


async def check(api, temporal, resource_id: str) -> dict:
    accepted = await api.post(
        f"/resources/{resource_id}/drift-check", headers={"Idempotency-Key": new_name()}
    )
    assert accepted.status_code == 202, accepted.text
    op = accepted.json()
    assert op["action"] == "drift_check"
    await phase_done(temporal, op["id"], "plan")
    done = (await api.get(op["links"]["self"])).json()
    assert done["state"] == "succeeded", json_text(done)
    assert_no_placement(json_text(done))
    return done


async def resource(api, rid: str) -> dict:
    got = await api.get(f"/resources/{rid}")
    assert got.status_code == 200, got.text
    assert_no_placement(got.text)
    return got.json()


def ids(listing: httpx.Response) -> list[str]:
    assert listing.status_code == 200, listing.text
    return [r["id"] for r in listing.json()["items"]]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_drift_and_upgrade_detection(provider, placed_floci, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(settings, "version_cache_seconds", 0)
    pattern = f"floci-{provider}"
    state_bucket = f"state{uuid.uuid4().hex[:16]}"
    state_key = f"{uuid.uuid4().hex}.tfstate"

    async def run(api, temporal, cloud):
        name = new_name()
        body = intent(provider, name)

        # 1. Created: in sync, no newer tag.
        rid = (await create(api, temporal, body))["resource_id"]
        assert await status(cloud, provider, name) == 200
        view = await resource(api, rid)
        assert view["drift_status"] == "in_sync"
        assert view["latest_version"] == "v1.0.0"
        assert view["upgrade_available"] is False

        if provider not in NOISY:
            # Floci AWS reports empty tags only on refresh; one no-op apply writes them into
            # state so that drift is measured against a settled baseline.
            first = await check(api, temporal, rid)
            assert {d["actions"][0] for d in first["drift"]} <= {"update"}, first["drift"]
            await execute(api, temporal, await plan(api, temporal, {**body, "resource_id": rid}))
        before = await resource(api, rid)

        # 2. A check of an untouched resource changes only the drift fields.
        checked = await check(api, temporal, rid)
        after = await resource(api, rid)
        assert_in_sync(provider, checked["drift"], after["drift_status"])
        assert after["drift_checked_at"]
        for key in ("managed_objects", "version", "state"):
            assert after[key] == before[key], key
        assert checked["id"] not in ids(await api.get("/operations?limit=100"))
        assert checked["id"] in ids(await api.get("/operations?action=drift_check&limit=100"))
        if provider not in NOISY:
            assert rid in ids(await api.get("/resources?drift_status=in_sync"))

        # A check while another operation is in flight is refused.
        pending = await plan(api, temporal, {**body, "resource_id": rid})
        busy = await api.post(
            f"/resources/{rid}/drift-check", headers={"Idempotency-Key": new_name()}
        )
        assert busy.status_code == 409, busy.text
        assert busy.json()["error"]["reason"] == "resource_busy"
        assert_no_placement(busy.text)
        assert (await api.post(pending["links"]["discard"])).status_code == 200

        # 3. Delete the primary object behind the API's back.
        await out_of_band_delete(cloud, provider, name)
        assert await primary_status(cloud, provider, name) == 404
        drifted = await check(api, temporal, rid)
        gone = [d for d in drifted["drift"] if d["address"] == DELETED[provider]]
        assert gone and gone[0]["actions"] == ["delete"], drifted["drift"]
        seen = await resource(api, rid)
        assert seen["drift_status"] == "drifted"
        assert DELETED[provider] in [d["address"] for d in seen["drift"]]
        assert rid in ids(await api.get("/resources?drift_status=drifted"))
        assert rid not in ids(await api.get("/resources?drift_status=in_sync"))
        assert await primary_status(cloud, provider, name) == 404  # a check recreates nothing
        for key in ("managed_objects", "version", "state"):
            assert seen[key] == before[key], key

        # 4. Repair: the same intent plans the missing object and recreates it.
        planned = await plan(api, temporal, {**body, "resource_id": rid})
        created = [c["address"] for c in planned["changes"] if "create" in c["actions"]]
        assert DELETED[provider] in created, planned["changes"]
        await execute(api, temporal, planned)
        assert await primary_status(cloud, provider, name) == 200
        assert await status(cloud, provider, name) == 200
        # A recreated object shows the same refresh noise as a new one, never delete/create.
        repaired = await check(api, temporal, rid)
        assert {d["actions"][0] for d in repaired["drift"]} <= {"update"}, repaired["drift"]

        # 5. A new tag is an available upgrade; upgrading clears it.
        repo = tmp_path / f"fleet-{provider}"
        (repo / "main.tf").write_text(
            (repo / "main.tf").read_text() + "\n# v1.1.0: no resource change\n"
        )
        git(repo, "add", ".")
        git(repo, "commit", "-qm", "v1.1.0")
        git(repo, "tag", "v1.1.0")
        seen = await resource(api, rid)
        assert seen["version"] == "v1.0.0"
        assert seen["latest_version"] == "v1.1.0" and seen["upgrade_available"] is True
        assert rid in ids(await api.get("/resources?upgrade_available=true"))
        assert rid not in ids(await api.get("/resources?upgrade_available=false"))
        await create(api, temporal, {**body, "version": "v1.1.0", "resource_id": rid})
        seen = await resource(api, rid)
        assert seen["version"] == "v1.1.0"
        assert seen["latest_version"] == "v1.1.0" and seen["upgrade_available"] is False
        assert rid not in ids(await api.get("/resources?upgrade_available=true"))
        assert await status(cloud, provider, name) == 200

        # 6. Destroyed: gone, and no drift verdict.
        await destroy(api, temporal, pattern, rid)
        assert await status(cloud, provider, name) == 404
        ended = await resource(api, rid)
        assert ended["drift_status"] is None
        assert ended["managed_objects"] == []

    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            made = await cloud.put(f"{STATE_S3}/{state_bucket}")
            assert made.status_code == 200, made.text
            try:
                await run(api, temporal, cloud)
            finally:
                await cloud.delete(f"{STATE_S3}/{state_bucket}/{state_key}")
                await cloud.delete(f"{STATE_S3}/{state_bucket}")
            assert (await cloud.get(f"{STATE_S3}/{state_bucket}")).status_code == 404

    with_remote_state(tmp_path, provider, state_bucket, state_key)
    asyncio.run(scenario())
