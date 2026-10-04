"""Multi-cloud HA app recipe on Floci: two replicas (AWS, Azure) behind a Route 53 failover router.

HTTP API (ASGI) -> real Temporal dev server -> production worker -> Terraform -> Floci.
Opt in: uv run pytest --floci tests/test_floci_ha_app.py -v
Emulators must already be running from compose.floci.yaml. No cloud credentials are used.
"""

import asyncio
import os
import ssl
import xml.etree.ElementTree as ET
from pathlib import Path
from threading import Thread

import httpx
import pytest
import yaml

from app.settings import settings
from app.worker import build_worker
from deploy.emulator.storage_proxy import server
from tests.operation_support import destroy_leftovers, temporal_api
from tests.test_floci import AZURE_SUBSCRIPTION, ROOT
from tests.test_floci_placed_features import (
    COST,
    S3,
    TARGETS,
    assert_no_placement,
    create,
    destroy,
    execute,
    json_text,
    make_repo,
    new_name,
    plan,
    status,
    submit,
)

pytestmark = pytest.mark.floci
ROUTE53 = f"{S3}/2013-04-01"
PATTERNS = {
    "ha-replica-aws": ("floci-ha-replica-aws", "aws"),
    "ha-replica-azure": ("floci-ha-replica-azure", "azure"),
    "ha-router-aws": ("floci-ha-router-aws", "aws"),
}
ZONE_TYPE = "aws_route53_zone"
OBJECTS = {
    "ha-replica-aws": [
        ("aws_s3_bucket.app", "aws_s3_bucket"),
        ("aws_s3_object.health", "aws_s3_object"),
        ("aws_s3_object.index", "aws_s3_object"),
    ],
    "ha-replica-azure": [
        ("azurerm_resource_group.app", "azurerm_resource_group"),
        ("azurerm_storage_account.app", "azurerm_storage_account"),
        ("azurerm_storage_container.app", "azurerm_storage_container"),
    ],
    "ha-router-aws": [
        ("aws_route53_health_check.primary", "aws_route53_health_check"),
        ("aws_route53_health_check.secondary", "aws_route53_health_check"),
        ("aws_route53_record.primary", "aws_route53_record"),
        ("aws_route53_record.secondary", "aws_route53_record"),
        ("aws_route53_zone.app", "aws_route53_zone"),
    ],
}


def mapping() -> str:
    targets = {cloud: dict(TARGETS[cloud]) for cloud in ("aws", "azure")}
    return yaml.safe_dump(
        {
            "business_units": {
                "work": {
                    "groups": ["emulator"],
                    "patterns": list(PATTERNS),
                    "environments": {"dev": {"budget_monthly": 300, "targets": targets}},
                }
            }
        }
    )


@pytest.fixture
def ha_floci(request, monkeypatch, tmp_path):
    if not request.config.getoption("--floci"):
        pytest.skip("pass --floci with the AWS and Azure emulators running")
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
    for name, (directory, cloud) in PATTERNS.items():
        main_tf = (ROOT / "examples" / directory / "main.tf").read_text()
        specs[name] = {**make_repo(tmp_path, name, main_tf), "cloud": cloud}
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


def deploy(pattern: str, app: str, role: str, **fields) -> dict:
    return {
        "pattern": pattern,
        "version": "v1.0.0",
        "environment": "dev",
        "labels": {"app": app, "app_role": role},
        **fields,
    }


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse(text: str) -> ET.Element:
    return ET.fromstring(text)


def children(node: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in node.iter() if local(c.tag) == name]


def text_of(node: ET.Element, name: str) -> str | None:
    found = children(node, name)
    return found[0].text if found else None


async def zones(cloud: httpx.AsyncClient, zone_name: str) -> list[str]:
    listed = await cloud.get(f"{ROUTE53}/hostedzone")
    assert listed.status_code == 200, listed.text
    return [
        text_of(z, "Id").rsplit("/", 1)[-1]
        for z in children(parse(listed.text), "HostedZone")
        if text_of(z, "Name") == zone_name
    ]


async def failover_records(cloud: httpx.AsyncClient, zone_id: str) -> dict:
    """role (PRIMARY/SECONDARY) -> (CNAME target, health check id), read from Route 53."""
    listed = await cloud.get(f"{ROUTE53}/hostedzone/{zone_id}/rrset")
    assert listed.status_code == 200, listed.text
    records = {}
    for rrset in children(parse(listed.text), "ResourceRecordSet"):
        role = text_of(rrset, "Failover")
        if role:
            assert text_of(rrset, "Type") == "CNAME"
            records[role] = (text_of(rrset, "Value"), text_of(rrset, "HealthCheckId"))
    return records


async def health_checks(cloud: httpx.AsyncClient) -> dict:
    """health check id -> (fqdn, path)."""
    listed = await cloud.get(f"{ROUTE53}/healthcheck")
    assert listed.status_code == 200, listed.text
    return {
        text_of(h, "Id"): (text_of(h, "FullyQualifiedDomainName"), text_of(h, "ResourcePath"))
        for h in children(parse(listed.text), "HealthCheck")
    }


def test_multi_cloud_ha_app_failover_through_the_platform(ha_floci):
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            app = new_name()
            aws_name, azure_name = f"{app}a", f"{app}z"
            zone_name = f"{app}.ha.example."
            fqdn = f"app.{zone_name}"

            # 1. Two replicas, one per cloud, each readable from its emulator.
            aws = await create(
                api,
                temporal,
                deploy("ha-replica-aws", app, "replica", inputs={"name": aws_name}),
            )
            azure = await create(
                api,
                temporal,
                deploy(
                    "ha-replica-azure",
                    app,
                    "replica",
                    inputs={"name": azure_name, "app_version": "2"},
                ),
            )
            index = await cloud.get(f"{S3}/{aws_name}/index.html")
            assert index.status_code == 200 and aws_name in index.text, index.text
            health = await cloud.get(f"{S3}/{aws_name}/health")
            assert (health.status_code, health.text) == (200, "ok")
            assert await status(cloud, "azure", azure_name) == 200
            container = await cloud.get(f"http://localhost:4577/{azure_name}/app?restype=container")
            assert container.status_code == 200, container.text
            endpoints = {"aws": aws["outputs"]["endpoint"], "azure": azure["outputs"]["endpoint"]}
            assert aws_name in endpoints["aws"] and azure_name in endpoints["azure"], endpoints
            assert aws["outputs"]["health_path"] == "/health"
            assert azure["outputs"]["health_path"] == "/app?restype=container"

            print("HA replica outputs:", aws["outputs"], azure["outputs"])

            # 2. The router fronts them: AWS primary, Azure secondary.
            def router_intent(primary, secondary, **fields):
                return deploy(
                    "ha-router-aws",
                    app,
                    "router",
                    inputs={"zone_name": zone_name},
                    input_refs={
                        "primary_endpoint": {"resource_id": primary, "output": "endpoint"},
                        "secondary_endpoint": {"resource_id": secondary, "output": "endpoint"},
                        "primary_health_path": {"resource_id": primary, "output": "health_path"},
                        "secondary_health_path": {
                            "resource_id": secondary,
                            "output": "health_path",
                        },
                    },
                    **fields,
                )

            router = await create(
                api, temporal, router_intent(aws["resource_id"], azure["resource_id"])
            )
            rid = router["resource_id"]
            assert router["outputs"]["app_fqdn"] == fqdn.rstrip(".")
            print("HA router outputs:", router["outputs"])
            zone_ids = await zones(cloud, zone_name)
            assert zone_ids == [router["outputs"]["zone_id"]], zone_ids
            zone_id = zone_ids[0]
            records = await failover_records(cloud, zone_id)
            assert set(records) == {"PRIMARY", "SECONDARY"}
            assert records["PRIMARY"][0] == endpoints["aws"]
            assert records["SECONDARY"][0] == endpoints["azure"]
            checks = await health_checks(cloud)
            assert checks[records["PRIMARY"][1]][0] == endpoints["aws"]
            assert checks[records["SECONDARY"][1]][0] == endpoints["azure"]
            assert checks[records["PRIMARY"][1]][1] == "/health"
            assert checks[records["SECONDARY"][1]][1] == "/app?restype=container"

            # 3. A replica the router references cannot be destroyed.
            for pattern, source, name, provider in (
                ("ha-replica-aws", aws, aws_name, "aws"),
                ("ha-replica-azure", azure, azure_name, "azure"),
            ):
                blocked = await submit(
                    api,
                    {"action": "destroy", "pattern": pattern, "resource_id": source["resource_id"]},
                )
                assert blocked.status_code == 409, blocked.text
                assert blocked.json()["error"]["reason"] == "resource_referenced"
                assert await status(cloud, provider, name) == 200
            assert (await cloud.get(f"{S3}/{aws_name}/health")).text == "ok"

            # 4. Failover drill: swap the roles and apply the exact reviewed plan.
            drill = await plan(
                api,
                temporal,
                {**router_intent(azure["resource_id"], aws["resource_id"]), "resource_id": rid},
            )
            changed = [c for c in drill["changes"] if c["actions"] != ["no-op"]]
            assert changed and all(c["actions"] == ["update"] for c in changed), changed
            assert not [
                c for c in drill["changes"] if c["type"] == ZONE_TYPE and c["actions"] != ["no-op"]
            ]
            assert {c["address"] for c in changed} >= {
                "aws_route53_record.primary",
                "aws_route53_record.secondary",
            }
            await execute(api, temporal, drill)
            assert await zones(cloud, zone_name) == [zone_id]  # same zone, not replaced
            swapped = await failover_records(cloud, zone_id)
            assert swapped["PRIMARY"][0] == endpoints["azure"]
            assert swapped["SECONDARY"][0] == endpoints["aws"]
            checks = await health_checks(cloud)
            assert checks[swapped["PRIMARY"][1]][0] == endpoints["azure"]
            assert checks[swapped["SECONDARY"][1]][0] == endpoints["aws"]
            # Each health check follows its replica's own path after the swap.
            assert checks[swapped["PRIMARY"][1]][1] == "/app?restype=container"
            assert checks[swapped["SECONDARY"][1]][1] == "/health"

            # 5. Resource API: each member's cloud, region, objects and labels.
            members = {
                "ha-replica-aws": (aws["resource_id"], "aws", "replica"),
                "ha-replica-azure": (azure["resource_id"], "azure", "replica"),
                "ha-router-aws": (rid, "aws", "router"),
            }
            found = await api.get(f"/resources?label=app={app}")
            assert found.status_code == 200, found.text
            assert sorted(r["id"] for r in found.json()["items"]) == sorted(
                v[0] for v in members.values()
            )
            assert_no_placement(found.text)
            for pattern, (resource_id, provider, role) in members.items():
                resource = (await api.get(f"/resources/{resource_id}")).json()
                assert resource["cloud"] == provider
                assert resource["region"] == TARGETS[provider]["region"]
                assert resource["labels"] == {"app": app, "app_role": role}
                assert resource["state"] == "ready"
                expected = [{"address": a, "type": t} for a, t in OBJECTS[pattern]]
                assert sorted(resource["managed_objects"], key=lambda o: o["address"]) == expected
                assert resource["estimated_monthly_cost"] == COST
                assert_no_placement(json_text(resource))
            only_router = (await api.get("/resources?label=app_role=router&state=ready")).json()
            assert [r["id"] for r in only_router["items"]] == [rid]

            # 6. Ordered teardown: router first, then both replicas.
            await destroy(api, temporal, "ha-router-aws", rid)
            assert await zones(cloud, zone_name) == []
            assert (await cloud.get(f"{ROUTE53}/hostedzone/{zone_id}")).status_code == 404
            remaining = await health_checks(cloud)
            assert not {c[0] for c in remaining.values()} & set(endpoints.values())
            await destroy(api, temporal, "ha-replica-aws", aws["resource_id"])
            await destroy(api, temporal, "ha-replica-azure", azure["resource_id"])
            assert (await cloud.get(f"{S3}/{aws_name}/health")).status_code == 404
            assert await status(cloud, "aws", aws_name) == 404
            assert await status(cloud, "azure", azure_name) == 404

    asyncio.run(scenario())
