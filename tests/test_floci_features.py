"""End-to-end feature tests: HTTP API -> real Temporal -> production worker -> Terraform -> Floci.

Opt in: uv run pytest --floci tests/test_floci_features.py -v
The Floci AWS emulator (S3 on http://localhost:4566) must already be running. No cloud
credentials are used.
"""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml

from app import ledger, terraform
from app.settings import settings
from app.worker import build_worker
from tests.conftest import git
from tests.operation_support import destroy_leftovers, phase_done, temporal_api

pytestmark = pytest.mark.floci
S3 = "http://localhost:4566"

PROVIDER = """
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.14.1"
    }
  }
%BACKEND%}

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
  s3_use_path_style           = true
  endpoints {
    s3 = "http://localhost:4566"
  }
}
"""


def provider(backend: str = "") -> str:
    return PROVIDER.replace("%BACKEND%", backend)


@pytest.fixture
def floci(request, monkeypatch, tmp_path):
    if not request.config.getoption("--floci"):
        pytest.skip("pass --floci with the Floci AWS emulator running")
    for key in list(os.environ):
        if key.startswith(("ARM_", "AWS_", "AZURE_", "GOOGLE_", "GCLOUD_", "CLOUDSDK_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,::1")
    try:
        yield tmp_path
    finally:
        leftovers = destroy_leftovers()
        if leftovers:
            pytest.fail(f"emulator resources leaked in {leftovers}")


def make_pattern(root: Path, name: str, versions: dict[str, str]) -> dict:
    """A real git repo with one tag per version; returns the catalog spec."""
    repo = root / f"pattern-{name}"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for tag, body in versions.items():
        (repo / "main.tf").write_text(body)
        git(repo, "add", ".")
        git(repo, "commit", "-qm", tag)
        git(repo, "tag", tag)
    return {"repo": f"file://{repo}"}


def register(specs: dict):
    settings.catalog_path.write_text(yaml.safe_dump({"patterns": specs}))


def bucket_name(prefix: str = "e2e") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


BUCKET = (
    provider()
    + """
variable "name" {
  type = string
}

resource "aws_s3_bucket" "test" {
  bucket = var.name
}

output "name" {
  value = aws_s3_bucket.test.id
}
"""
)

OBJECT = (
    provider()
    + """
variable "bucket" {
  type = string
}

resource "aws_s3_object" "hello" {
  bucket  = var.bucket
  key     = "hello.txt"
  content = "hello from forgeapi"
}

output "key" {
  value = aws_s3_object.hello.key
}
"""
)


async def submit(api, body: dict, key: str | None = None):
    return await api.post(
        "/operations", json=body, headers={"Idempotency-Key": key or uuid.uuid4().hex}
    )


async def plan(api, temporal, body: dict) -> dict:
    accepted = await submit(api, body)
    assert accepted.status_code == 202, accepted.text
    op = accepted.json()
    await phase_done(temporal, op["id"], "plan")
    planned = (await api.get(op["links"]["self"])).json()
    assert planned["state"] == "planned", planned
    return planned


async def execute(api, temporal, planned: dict) -> dict:
    response = await api.post(
        planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
    )
    assert response.status_code == 202, response.text
    await phase_done(temporal, planned["id"], "apply")
    done = (await api.get(planned["links"]["self"])).json()
    assert done["state"] == "succeeded", done
    return done


async def create(api, temporal, body: dict) -> dict:
    return await execute(api, temporal, await plan(api, temporal, body))


async def destroy(api, temporal, pattern: str, resource_id: str) -> dict:
    planned = await plan(
        api,
        temporal,
        {"action": "destroy", "pattern": pattern, "resource_id": resource_id},
    )
    assert [c["actions"] for c in planned["changes"]] and all(
        c["actions"] == ["delete"] for c in planned["changes"]
    )
    return await execute(api, temporal, planned)


async def status(cloud, path: str) -> int:
    return (await cloud.get(f"{S3}/{path}")).status_code


def test_composition_refs_and_destroy_protection(floci):
    specs = {
        "bucket": make_pattern(floci, "bucket", {"v1.0.0": BUCKET}),
        "object": make_pattern(floci, "object", {"v1.0.0": OBJECT}),
    }
    register(specs)

    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            name = bucket_name()
            listed = {p["name"] for p in (await api.get("/patterns")).json()["items"]}
            assert {"bucket", "object"} <= listed

            a = await create(
                api,
                temporal,
                {
                    "pattern": "bucket",
                    "version": "v1.0.0",
                    "inputs": {"name": name},
                    "labels": {"team": "e2e"},
                },
            )
            a_id = a["resource_id"]
            assert a["outputs"]["name"] == name
            assert await status(cloud, name) == 200

            found = (await api.get("/resources?label=team=e2e&state=ready")).json()["items"]
            assert [r["id"] for r in found] == [a_id]
            assert found[0]["labels"] == {"team": "e2e"}
            by_pattern = (await api.get("/resources?pattern=object")).json()["items"]
            assert by_pattern == []

            refs = {"bucket": {"resource_id": a_id, "output": "name"}}
            b = await create(
                api, temporal, {"pattern": "object", "version": "v1.0.0", "input_refs": refs}
            )
            b_id = b["resource_id"]
            assert b["outputs"]["key"] == "hello.txt"
            got = await cloud.get(f"{S3}/{name}/hello.txt")
            assert got.status_code == 200 and got.text == "hello from forgeapi"
            resource_b = (await api.get(f"/resources/{b_id}")).json()
            assert resource_b["input_refs"] == refs
            assert resource_b["state"] == "ready"
            by_pattern = (await api.get("/resources?pattern=object")).json()["items"]
            assert [r["id"] for r in by_pattern] == [b_id]

            # A is referenced by B: destroy is refused until B is gone.
            blocked = await submit(
                api, {"action": "destroy", "pattern": "bucket", "resource_id": a_id}
            )
            assert blocked.status_code == 409
            assert blocked.json()["error"]["reason"] == "resource_referenced"
            assert await status(cloud, name) == 200

            await destroy(api, temporal, "object", b_id)
            assert await status(cloud, f"{name}/hello.txt") == 404
            assert (await api.get(f"/resources/{b_id}")).json()["state"] == "destroyed"
            assert await status(cloud, name) == 200

            await destroy(api, temporal, "bucket", a_id)
            assert await status(cloud, name) == 404
            assert (await api.get(f"/resources/{a_id}")).json()["state"] == "destroyed"

    asyncio.run(scenario())


def test_version_upgrade_with_remote_s3_state(floci):
    state_bucket = bucket_name("state")
    state_key = f"{uuid.uuid4().hex}.tfstate"
    backend = f"""
  backend "s3" {{
    bucket                      = "{state_bucket}"
    key                         = "{state_key}"
    region                      = "us-east-1"
    access_key                  = "test"
    secret_key                  = "test"
    use_path_style              = true
    skip_credentials_validation = true
    skip_requesting_account_id  = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    endpoints = {{
      s3 = "http://localhost:4566"
    }}
  }}
"""

    def body(tag: str) -> str:
        return (
            provider(backend)
            + f"""
variable "name" {{
  type = string
}}

resource "aws_s3_bucket" "test" {{
  bucket = var.name
  tags = {{
    version = "{tag}"
  }}
}}

output "name" {{
  value = aws_s3_bucket.test.id
}}
"""
        )

    register(
        {
            "upgradable": make_pattern(
                floci, "upgradable", {"v1.0.0": body("v1"), "v1.1.0": body("v2")}
            )
        }
    )

    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            created = await cloud.put(f"{S3}/{state_bucket}")
            assert created.status_code == 200, created.text
            try:
                name = bucket_name()
                done = await create(
                    api,
                    temporal,
                    {"pattern": "upgradable", "version": "v1.0.0", "inputs": {"name": name}},
                )
                rid = done["resource_id"]
                assert await status(cloud, name) == 200
                tagging = await cloud.get(f"{S3}/{name}?tagging")
                assert "<Value>v1</Value>" in tagging.text, tagging.text
                assert await status(cloud, f"{state_bucket}/{state_key}") == 200
                workdir = terraform.deployment_dir(rid) / "work"
                assert not (workdir / "terraform.tfstate").exists()

                planned = await plan(
                    api,
                    temporal,
                    {
                        "action": "deploy",
                        "pattern": "upgradable",
                        "resource_id": rid,
                        "version": "v1.1.0",
                        "inputs": {"name": name},
                    },
                )
                assert planned["resource_id"] == rid
                assert [c["actions"] for c in planned["changes"]] == [["update"]]
                await execute(api, temporal, planned)
                tagging = await cloud.get(f"{S3}/{name}?tagging")
                assert "<Value>v2</Value>" in tagging.text, tagging.text
                resource = (await api.get(f"/resources/{rid}")).json()
                assert resource["version"] == "v1.1.0"
                assert resource["state"] == "ready"
                assert await status(cloud, f"{state_bucket}/{state_key}") == 200
                assert not (workdir / "terraform.tfstate").exists()

                await destroy(api, temporal, "upgradable", rid)
                assert await status(cloud, name) == 404
                assert not (workdir / ".terraform").exists()
            finally:
                await cloud.delete(f"{S3}/{state_bucket}/{state_key}")
                await cloud.delete(f"{S3}/{state_bucket}")
            assert await status(cloud, state_bucket) == 404

    asyncio.run(scenario())


def test_discard_and_plan_expiry_cleanup(floci, monkeypatch):
    register({"bucket": make_pattern(floci, "bucket", {"v1.0.0": BUCKET})})

    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            intent = {"pattern": "bucket", "version": "v1.0.0"}

            # Discard: plan file removed, nothing created.
            first = bucket_name()
            planned = await plan(api, temporal, {**intent, "inputs": {"name": first}})
            tfplan = terraform.deployment_dir(planned["resource_id"]) / "work" / "tfplan"
            assert tfplan.exists()
            assert await status(cloud, first) == 404
            discarded = await api.post(planned["links"]["discard"])
            assert discarded.status_code == 200, discarded.text
            assert discarded.json()["state"] == "failed"
            assert not tfplan.exists()
            assert await status(cloud, first) == 404

            # Expiry: a plan older than plan_max_age_hours cannot execute.
            monkeypatch.setattr(settings, "plan_max_age_hours", 1)
            second = bucket_name()
            planned = await plan(api, temporal, {**intent, "inputs": {"name": second}})
            rid = planned["resource_id"]
            tfplan = terraform.deployment_dir(rid) / "work" / "tfplan"
            assert tfplan.exists()
            assert planned["plan_expires_at"] is not None
            real_now = ledger.now
            later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
            monkeypatch.setattr(ledger, "now", lambda: later)
            refused = await api.post(
                planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
            )
            monkeypatch.setattr(ledger, "now", real_now)
            assert refused.status_code == 409, refused.text
            assert refused.json()["error"]["reason"] == "plan_expired"
            expired = (await api.get(planned["links"]["self"])).json()
            assert expired["state"] == "failed"
            assert not tfplan.exists()
            assert await status(cloud, second) == 404

            # A new intent on the same resource is accepted, planned and applied.
            replanned = await plan(
                api,
                temporal,
                {**intent, "resource_id": rid, "inputs": {"name": second}},
            )
            assert replanned["id"] != planned["id"]
            await execute(api, temporal, replanned)
            assert await status(cloud, second) == 200
            await destroy(api, temporal, "bucket", rid)
            assert await status(cloud, second) == 404

    asyncio.run(scenario())


def test_destroy_leftovers_cleans_up_an_unfinished_test(floci):
    register({"bucket": make_pattern(floci, "bucket", {"v1.0.0": BUCKET})})

    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            name = bucket_name()
            await create(
                api,
                temporal,
                {"pattern": "bucket", "version": "v1.0.0", "inputs": {"name": name}},
            )
            assert await status(cloud, name) == 200
            assert destroy_leftovers() == []
            assert await status(cloud, name) == 404
            assert destroy_leftovers() == []

    asyncio.run(scenario())


def test_failed_plan_leaves_no_plan_file(floci):
    failing = (
        provider()
        + """
variable "name" {
  type = string
}

resource "aws_s3_bucket" "test" {
  bucket = var.name
  lifecycle {
    precondition {
      condition     = var.name == "never"  # 1.15 rejects a constant
      error_message = "refused at plan time"
    }
  }
}
"""
    )
    register({"failing": make_pattern(floci, "failing", {"v1.0.0": failing})})

    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            name = bucket_name()
            accepted = await submit(
                api, {"pattern": "failing", "version": "v1.0.0", "inputs": {"name": name}}
            )
            assert accepted.status_code == 202, accepted.text
            op = accepted.json()
            await phase_done(temporal, op["id"], "plan")
            failed = (await api.get(op["links"]["self"])).json()
            assert failed["state"] == "failed", failed
            assert failed["plan_digest"] is None
            assert "refused at plan time" in failed["diagnostic"]
            workdir = terraform.deployment_dir(failed["resource_id"]) / "work"
            # Init ran (the workspace exists) but no saved plan was left behind.
            assert (workdir / ".terraform").exists()
            assert not (workdir / "tfplan").exists()
            assert await status(cloud, name) == 404

    asyncio.run(scenario())
