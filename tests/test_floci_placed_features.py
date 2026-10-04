"""End-to-end placed-mode (tenant mapping) features on all three Floci emulators.

HTTP API (ASGI) -> real Temporal dev server -> production worker -> Terraform -> Floci.
Opt in: uv run pytest --floci tests/test_floci_placed_features.py -v
Emulators must already be running from compose.floci.yaml. No cloud credentials are used.
"""

import asyncio
import hashlib
import os
import ssl
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Thread

import httpx
import pytest
import yaml

from app import ledger, terraform
from app.settings import settings
from app.worker import build_worker
from deploy.emulator.storage_proxy import server
from tests.conftest import git
from tests.operation_support import destroy_leftovers, phase_done, temporal_api
from tests.test_floci import AZURE_SUBSCRIPTION, ROOT

pytestmark = pytest.mark.floci
PROVIDERS = ["aws", "azure", "gcp"]
COST = 30
BUDGET = 100
PROTECTED = {
    "aws": "aws_s3_bucket",
    "azure": "azurerm_storage_account",
    "gcp": "google_storage_bucket",
}
TARGETS = {
    "azure": {"subscription_id": AZURE_SUBSCRIPTION, "region": "eastus"},
    "aws": {"aws_account_id": "000000000000", "region": "us-east-1"},
    "gcp": {"project_id": "forgeapi-emulator", "region": "us-central1"},
}
OBJECTS = {
    "aws": [("aws_s3_bucket.test", "aws_s3_bucket")],
    "azure": [
        ("azurerm_resource_group.test", "azurerm_resource_group"),
        ("azurerm_storage_account.test", "azurerm_storage_account"),
        ("azurerm_storage_container.test", "azurerm_storage_container"),
    ],
    "gcp": [("google_storage_bucket.test", "google_storage_bucket")],
}
PLACEMENT_IDS = [AZURE_SUBSCRIPTION, "000000000000", "forgeapi-emulator"]
S3 = "http://localhost:4566"

def consumer_module(provider: str, body: str) -> str:
    text = (ROOT / "examples" / f"floci-placed-{provider}" / "main.tf").read_text()
    return text[: text.index('variable "name"')] + body


OBJECT = consumer_module(
    "aws",
    """
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

variable "aws_account_id" {
  type = string
}

variable "region" {
  type = string
}
""",
)
AZURE_OBJECT = consumer_module(
    "azure",
    """
variable "account" {
  type = string
}

# A container, not a blob: Floci Azure answers 501 to the Set Blob Properties call that
# azurerm_storage_blob always makes after uploading (seen 2026-10-02, azurerm 4.65.0).
resource "azurerm_storage_container" "hello" {
  name                  = "consumer"
  storage_account_name  = var.account
  container_access_type = "private"
}

output "key" {
  value = azurerm_storage_container.hello.name
}

variable "subscription_id" {
  type = string
}

variable "region" {
  type = string
}
""",
)
GCP_OBJECT = consumer_module(
    "gcp",
    """
variable "bucket" {
  type = string
}

resource "google_storage_bucket_object" "hello" {
  name    = "hello.txt"
  bucket  = var.bucket
  content = "hello from forgeapi"
}

output "key" {
  value = google_storage_bucket_object.hello.name
}

variable "project_id" {
  type = string
}

variable "region" {
  type = string
}
""",
)
# provider -> (consumer pattern, module text, reference input name, readback URL template,
# expected `key` output, expected readback body or None when only existence is checked)
HELLO = "hello from forgeapi"
CONSUMERS = {
    "aws": ("floci-aws-object", OBJECT, "bucket", S3 + "/{name}/hello.txt", "hello.txt", HELLO),
    "azure": (
        "floci-azure-object",
        AZURE_OBJECT,
        "account",
        "http://localhost:4577/{name}/consumer?restype=container",
        "consumer",
        None,
    ),
    "gcp": (
        "floci-gcp-object",
        GCP_OBJECT,
        "bucket",
        "http://localhost:4588/storage/v1/b/{name}/o/hello.txt?alt=media",
        "hello.txt",
        HELLO,
    ),
}


# A placed pattern whose precondition message interpolates the injected account id.
REFUSING = (
    "floci-aws-refusing",
    (ROOT / "examples" / "floci-placed-aws" / "main.tf")
    .read_text()
    .replace(
        "  bucket = var.name\n}",
        "  bucket = var.name\n  lifecycle {\n    precondition {\n"
        '      condition     = var.name == "never"\n'
        '      error_message = "account ${var.aws_account_id} may not create this bucket"\n'
        "    }\n  }\n}",
    ),
)


def mapping(locked_open: bool = False) -> str:
    def environment(**extra):
        return {
            "budget_monthly": BUDGET,
            "targets": {cloud: dict(target) for cloud, target in TARGETS.items()},
            **extra,
        }

    locked = (
        {}
        if locked_open
        else {
            "allow_destroy": False,
            "protected_resource_types": sorted(PROTECTED.values(), reverse=True),
        }
    )
    patterns = [f"floci-{p}" for p in PROVIDERS] + [c[0] for c in CONSUMERS.values()]
    patterns.append(REFUSING[0])
    return yaml.safe_dump(
        {
            "business_units": {
                "work": {
                    "groups": ["emulator"],
                    "patterns": patterns,
                    "environments": {"dev": environment(), "locked": environment(**locked)},
                },
                "other": {
                    "groups": ["other"],
                    "patterns": ["floci-aws"],
                    "environments": {"dev": environment()},
                },
            }
        }
    )


def make_repo(root: Path, name: str, main_tf: str) -> dict:
    repo = root / name
    repo.mkdir()
    (repo / "main.tf").write_text(main_tf)
    (repo / "config.yaml").write_text(yaml.safe_dump({"estimated_costs": COST}))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "emulator fixture")
    git(repo, "tag", "v1.0.0")
    return {"repo": f"file://{repo}"}


@pytest.fixture
def placed_floci(request, monkeypatch, tmp_path):
    if not request.config.getoption("--floci"):
        pytest.skip("pass --floci with AWS, Azure and GCP emulators running")
    for key in list(os.environ):
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
    for provider in PROVIDERS:
        main_tf = (ROOT / "examples" / f"floci-placed-{provider}" / "main.tf").read_text()
        specs[f"floci-{provider}"] = {**make_repo(tmp_path, f"floci-{provider}", main_tf)}
        specs[f"floci-{provider}"]["cloud"] = provider
    for provider, (pattern, module, *_rest) in CONSUMERS.items():
        specs[pattern] = {**make_repo(tmp_path, pattern, module), "cloud": provider}
    specs[REFUSING[0]] = {**make_repo(tmp_path, REFUSING[0], REFUSING[1]), "cloud": "aws"}
    settings.catalog_path.write_text(yaml.safe_dump({"patterns": specs}))
    monkeypatch.setattr(settings, "dev_groups", "emulator")
    monkeypatch.setattr(settings, "tenants_yaml", mapping())
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


def target_url(provider: str, name: str) -> str:
    if provider == "aws":
        return f"{S3}/{name}"
    if provider == "gcp":
        return f"http://localhost:4588/storage/v1/b/{name}"
    return (
        f"http://localhost:4577/subscriptions/{AZURE_SUBSCRIPTION}/resourcegroups/{name}"
        f"/providers/Microsoft.Storage/storageAccounts/{name}?api-version=2023-05-01"
    )


def new_name() -> str:
    return f"forge{uuid.uuid4().hex[:16]}"


async def status(cloud, provider: str, name: str) -> int:
    return (await cloud.get(target_url(provider, name))).status_code


async def submit(api, body: dict):
    return await api.post("/operations", json=body, headers={"Idempotency-Key": uuid.uuid4().hex})


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
    assert done["state"] == "succeeded", json_text(done)
    return done


def intent(provider: str, name: str, environment: str = "dev", **extra) -> dict:
    return {
        "pattern": f"floci-{provider}",
        "version": "v1.0.0",
        "environment": environment,
        "inputs": {"name": name},
        **extra,
    }


async def create(api, temporal, body: dict) -> dict:
    return await execute(api, temporal, await plan(api, temporal, body))


async def destroy(api, temporal, pattern: str, resource_id: str) -> dict:
    planned = await plan(
        api, temporal, {"action": "destroy", "pattern": pattern, "resource_id": resource_id}
    )
    assert planned["changes"] and all(c["actions"] == ["delete"] for c in planned["changes"])
    return await execute(api, temporal, planned)


async def budget(api, environment: str = "dev") -> dict:
    units = {u["name"]: u for u in (await api.get("/agent")).json()["business_units"]}
    return units["work"]["budgets"][environment]


def assert_no_placement(body: str):
    for value in PLACEMENT_IDS:
        assert value not in body, f"placement identifier {value} leaked"


def test_placed_discovery_shows_guardrails_and_budget_headroom(placed_floci):
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            agent = await api.get("/agent")
            assert agent.status_code == 200
            units = {u["name"]: u for u in agent.json()["business_units"]}
            # The caller is only in `emulator`: the `other` unit is invisible.
            assert set(units) == {"work"}
            work = units["work"]
            assert set(work["environments"]) == {"dev", "locked"}
            assert work["guardrails"] == {
                "dev": {"allow_destroy": True, "protected_resource_types": []},
                "locked": {
                    "allow_destroy": False,
                    "protected_resource_types": sorted(PROTECTED.values()),
                },
            }
            assert work["clouds"]["dev"] == sorted(PROVIDERS)
            assert_no_placement(agent.text)
            before = work["budgets"]["dev"]
            assert before["monthly_budget"] == BUDGET
            reserved = before["reserved"]
            assert before["available"] == BUDGET - reserved
            assert work["budgets"]["locked"]["monthly_budget"] == BUDGET

            listed = await api.get("/patterns")
            clouds = {p["name"]: p["cloud"] for p in listed.json()["items"]}
            assert {f"floci-{p}": p for p in PROVIDERS}.items() <= clouds.items()
            assert {c[0]: p for p, c in CONSUMERS.items()}.items() <= clouds.items()
            assert set(clouds) == {f"floci-{p}" for p in PROVIDERS} | {
                c[0] for c in CONSUMERS.values()
            } | {REFUSING[0]}

            # Another unit's placement is not reachable or revealed.
            foreign = await api.post(
                "/intents/validate", json={**intent("aws", new_name()), "business_unit": "other"}
            )
            assert foreign.status_code == 403, foreign.text

            name = new_name()
            done = await create(api, temporal, intent("aws", name))
            assert await status(cloud, "aws", name) == 200
            after = await budget(api)
            assert after["reserved"] == reserved + COST
            assert after["available"] == BUDGET - reserved - COST
            assert after["monthly_budget"] == BUDGET

            for body in (
                (await api.get("/agent")).text,
                (await api.get("/patterns")).text,
                (await api.get("/patterns/floci-aws?environment=dev")).text,
            ):
                assert_no_placement(body)
            assert_no_placement((await api.get(f"/resources/{done['resource_id']}")).text)

            await destroy(api, temporal, "floci-aws", done["resource_id"])
            assert await status(cloud, "aws", name) == 404
            assert (await budget(api))["reserved"] == reserved

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", PROVIDERS)
def test_placed_labels_filters_and_budget_enforcement(provider, placed_floci):
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            name = new_name()
            done = await create(api, temporal, intent(provider, name, labels={"cloud": provider}))
            rid = done["resource_id"]
            assert await status(cloud, provider, name) == 200

            found = await api.get(f"/resources?environment=dev&label=cloud={provider}&state=ready")
            assert found.status_code == 200
            items = found.json()["items"]
            assert [r["id"] for r in items] == [rid]
            assert items[0]["labels"] == {"cloud": provider}
            assert items[0]["environment"] == "dev"
            assert items[0]["cloud"] == provider
            assert items[0]["region"] == TARGETS[provider]["region"]
            assert items[0]["estimated_monthly_cost"] == COST
            assert items[0]["owned_by_caller"] is True
            assert items[0]["created_at"]
            expected = sorted(
                ({"address": a, "type": t} for a, t in OBJECTS[provider]),
                key=lambda o: o["address"],
            )
            assert sorted(items[0]["managed_objects"], key=lambda o: o["address"]) == expected
            assert_no_placement(found.text)
            other = (await api.get("/resources?environment=dev&label=cloud=nothing")).json()
            assert other["items"] == []
            assert (await api.get("/resources?environment=locked")).json()["items"] == []

            # Two more fit (90 of 100); a fourth does not. Admission agrees with discovery.
            names = [new_name() for _ in range(4)]
            second = await plan(api, temporal, intent(provider, names[0]))
            third = await plan(api, temporal, intent(provider, names[1]))
            assert await budget(api) == {
                "monthly_budget": BUDGET,
                "reserved": 3 * COST,
                "available": BUDGET - 3 * COST,
            }
            assert (await budget(api))["available"] < COST
            refused = await submit(api, intent(provider, names[2]))
            assert refused.status_code == 403, refused.text
            assert refused.json()["error"]["reason"] == "budget_exceeded"
            assert await budget(api) == {
                "monthly_budget": BUDGET,
                "reserved": 3 * COST,
                "available": BUDGET - 3 * COST,
            }

            # Discarding a plan frees its reservation; now exactly one more fits.
            discarded = await api.post(third["links"]["discard"])
            assert discarded.status_code == 200, discarded.text
            freed = await budget(api)
            assert freed["reserved"] == 2 * COST and freed["available"] >= COST
            fourth = await plan(api, temporal, intent(provider, names[3]))
            assert (await budget(api))["available"] < COST
            for pending in (second, fourth):
                assert (await api.post(pending["links"]["discard"])).status_code == 200
            assert (await budget(api))["reserved"] == COST
            for unused in names:
                assert await status(cloud, provider, unused) == 404

            await destroy(api, temporal, f"floci-{provider}", rid)
            assert await status(cloud, provider, name) == 404
            assert (await budget(api))["reserved"] == 0
            gone = await api.get(f"/resources/{rid}")
            assert gone.json()["managed_objects"] == []
            assert gone.json()["estimated_monthly_cost"] is None  # destroyed costs nothing
            assert_no_placement(gone.text)

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", PROVIDERS)
def test_placed_input_refs_same_env_and_cross_env_refused(provider, placed_floci):
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            name = new_name()
            source = await create(api, temporal, intent(provider, name))
            sid = source["resource_id"]
            assert await status(cloud, provider, name) == 200
            ref = {"name": {"resource_id": sid, "output": "name"}}

            # Same environment validates (and resolves the reference); nothing is reserved.
            same = {**intent(provider, ""), "inputs": {}, "input_refs": ref}
            checked = await api.post("/intents/validate", json=same)
            assert checked.status_code == 200, checked.text
            assert checked.json()["budget_reserved"] is False
            # Another environment of the same unit may not reference it.
            across = {**same, "environment": "locked"}
            crossed = await api.post("/intents/validate", json=across)
            assert crossed.status_code == 422, crossed.text
            assert "one business unit and environment" in crossed.text
            submitted = await submit(api, across)
            assert submitted.status_code == 422, submitted.text
            assert (await api.get("/resources?environment=locked")).json()["items"] == []

            pattern, _module, field, readback, key, body = CONSUMERS[provider]
            url = readback.format(name=name)
            consumer = {
                "pattern": pattern,
                "version": "v1.0.0",
                "environment": "dev",
                "input_refs": {field: ref["name"]},
            }
            crossed = await submit(api, {**consumer, "environment": "locked"})
            assert crossed.status_code == 422, crossed.text
            made = await create(api, temporal, consumer)
            cid = made["resource_id"]
            assert made["outputs"]["key"] == key
            got = await cloud.get(url)
            assert got.status_code == 200, got.text
            assert body is None or got.text == body
            resource = (await api.get(f"/resources/{cid}")).json()
            assert resource["input_refs"] == {field: ref["name"]}
            assert resource["state"] == "ready"
            assert_no_placement(json_text(resource))

            blocked = await submit(
                api, {"action": "destroy", "pattern": f"floci-{provider}", "resource_id": sid}
            )
            assert blocked.status_code == 409, blocked.text
            assert blocked.json()["error"]["reason"] == "resource_referenced"
            assert await status(cloud, provider, name) == 200
            await destroy(api, temporal, pattern, cid)
            assert (await cloud.get(url)).status_code == 404

            await destroy(api, temporal, f"floci-{provider}", sid)
            assert await status(cloud, provider, name) == 404

    asyncio.run(scenario())


def json_text(value) -> str:
    import json

    return json.dumps(value)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_locked_environment_guardrails(provider, placed_floci, monkeypatch):
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            pattern = f"floci-{provider}"
            name = new_name()
            # Creating is allowed in a locked environment.
            done = await create(api, temporal, intent(provider, name, "locked"))
            rid = done["resource_id"]
            assert (await api.get(f"/resources/{rid}")).json()["environment"] == "locked"
            assert await status(cloud, provider, name) == 200

            # A destroy intent is refused outright and creates no operation.
            operations = (await api.get("/operations?limit=100")).json()["items"]
            denied = await submit(
                api, {"action": "destroy", "pattern": pattern, "resource_id": rid}
            )
            assert denied.status_code == 403, denied.text
            assert denied.json()["error"]["reason"] == "policy_denied"
            after = (await api.get("/operations?limit=100")).json()["items"]
            assert [o["id"] for o in after] == [o["id"] for o in operations]
            assert await status(cloud, provider, name) == 200

            # A replacing update plans, but executing a plan that replaces a protected type
            # is refused.
            renamed = new_name()
            planned = await plan(
                api,
                temporal,
                {"pattern": pattern, "resource_id": rid, "inputs": {"name": renamed}},
            )
            protected = [c for c in planned["changes"] if c["type"] == PROTECTED[provider]]
            assert protected and all("delete" in c["actions"] for c in protected)
            refused = await api.post(
                planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]}
            )
            assert refused.status_code == 403, refused.text
            assert refused.json()["error"]["reason"] == "policy_denied"
            assert (await api.get(planned["links"]["self"])).json()["state"] == "planned"
            discarded = await api.post(planned["links"]["discard"])
            assert discarded.status_code == 200, discarded.text
            assert discarded.json()["state"] == "failed"
            assert await status(cloud, provider, name) == 200
            assert await status(cloud, provider, renamed) == 404
            assert (await api.get(f"/resources/{rid}")).json()["state"] == "ready"

            # Cleanup is impossible through the locked mapping; open it temporarily.
            monkeypatch.setattr(settings, "tenants_yaml", mapping(locked_open=True))
            await destroy(api, temporal, pattern, rid)
            assert await status(cloud, provider, name) == 404

    asyncio.run(scenario())


def test_accept_path_plan_expiry_placed(placed_floci, monkeypatch):
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            monkeypatch.setattr(settings, "plan_max_age_hours", 1)
            name = new_name()
            body = intent("aws", name)
            first = await plan(api, temporal, body)
            rid = first["resource_id"]
            tfplan = terraform.deployment_dir(rid) / "work" / "tfplan"
            assert tfplan.exists() and first["plan_expires_at"] is not None
            assert (await budget(api))["reserved"] == COST

            real_now = ledger.now
            later = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
            monkeypatch.setattr(ledger, "now", lambda: later)
            try:
                accepted = await submit(api, {**body, "resource_id": rid})
            finally:
                monkeypatch.setattr(ledger, "now", real_now)
            assert accepted.status_code == 202, accepted.text
            new = accepted.json()
            assert new["id"] != first["id"]
            expired = (await api.get(first["links"]["self"])).json()
            assert expired["state"] == "failed"
            # The new plan may already be writing the same path; the expired plan must be gone.
            assert not tfplan.exists() or (
                hashlib.sha256(tfplan.read_bytes()).hexdigest() != first["plan_digest"]
            )
            assert await status(cloud, "aws", name) == 404

            await phase_done(temporal, new["id"], "plan")
            replanned = (await api.get(new["links"]["self"])).json()
            assert replanned["state"] == "planned", replanned
            # Reserved once for the resource, not once per operation.
            assert (await budget(api))["reserved"] == COST
            await execute(api, temporal, replanned)
            assert await status(cloud, "aws", name) == 200
            assert (await budget(api))["reserved"] == COST
            await destroy(api, temporal, "floci-aws", rid)
            assert await status(cloud, "aws", name) == 404
            assert (await budget(api))["reserved"] == 0

    asyncio.run(scenario())


def test_failed_plan_diagnostic_redacts_placement_id(placed_floci):
    assert "lifecycle" in REFUSING[1]

    async def scenario():
        async with temporal_api() as (api, temporal), build_worker(temporal):
            accepted = await submit(
                api,
                {
                    "pattern": REFUSING[0],
                    "version": "v1.0.0",
                    "environment": "dev",
                    "inputs": {"name": new_name()},
                },
            )
            assert accepted.status_code == 202, accepted.text
            op = accepted.json()
            await phase_done(temporal, op["id"], "plan")
            failed = (await api.get(op["links"]["self"])).json()
            assert failed["state"] == "failed", json_text(failed)
            text = failed["diagnostic"]
            assert "may not create this bucket" in text
            assert "[redacted]" in text
            assert "000000000000" not in text
            assert_no_placement(json_text(failed))

    asyncio.run(scenario())
