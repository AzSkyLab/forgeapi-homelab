"""One-request multi-cloud HA app (`POST /apps`) on Floci: two replicas (AWS, Azure) and a Route 53
failover router, with two exact-plan approvals.

HTTP API (ASGI) -> real Temporal dev server -> production worker -> Terraform -> Floci, checked by
independent emulator readbacks.
Opt in: uv run pytest --floci tests/test_floci_app_rollout.py -v
Emulators must already be running from compose.floci.yaml. No cloud credentials are used.
"""

import asyncio
import uuid

import httpx
import pytest

from app.worker import build_worker
from tests.operation_support import temporal_api
from tests.test_floci_ha_app import (  # noqa: F401 (ha_floci is a pytest fixture)
    OBJECTS,
    ROUTE53,
    failover_records,
    ha_floci,
    health_checks,
    zones,
)
from tests.test_floci_placed_features import (
    COST,
    S3,
    assert_no_placement,
    json_text,
    new_name,
    status,
)

pytestmark = pytest.mark.floci


async def wait_for(api, app_id: str, state: str, timeout: int = 180) -> dict:
    async def poll():
        while True:
            body = (await api.get(f"/apps/{app_id}")).json()
            if body["state"] == state:
                return body
            assert body["state"] not in ("failed", "uncertain"), json_text(body)
            await asyncio.sleep(0.5)

    return await asyncio.wait_for(poll(), timeout)


def test_one_request_multi_cloud_ha_app_on_floci(ha_floci):  # noqa: F811
    async def scenario():
        async with (
            temporal_api() as (api, temporal),
            build_worker(temporal),
            httpx.AsyncClient() as cloud,
        ):
            app = new_name()
            aws_name, azure_name = f"{app}a", f"{app}z"
            zone_name = f"{app}.ha.example."
            body = {
                "name": app,
                "environment": "dev",
                "replicas": [
                    {
                        "pattern": "ha-replica-aws",
                        "version": "v1.0.0",
                        "inputs": {"name": aws_name},
                    },
                    {
                        "pattern": "ha-replica-azure",
                        "version": "v1.0.0",
                        "inputs": {"name": azure_name, "app_version": "2"},
                    },
                ],
                "router": {
                    "pattern": "ha-router-aws",
                    "version": "v1.0.0",
                    "inputs": {"zone_name": zone_name},
                    "replica_refs": {
                        "primary_endpoint": {"replica": 0, "output": "endpoint"},
                        "secondary_endpoint": {"replica": 1, "output": "endpoint"},
                        "primary_health_path": {"replica": 0, "output": "health_path"},
                        "secondary_health_path": {"replica": 1, "output": "health_path"},
                    },
                },
            }

            async def nothing_exists():
                assert (await cloud.get(f"{S3}/{aws_name}/health")).status_code == 404
                assert await status(cloud, "aws", aws_name) == 404
                assert await status(cloud, "azure", azure_name) == 404
                assert await zones(cloud, zone_name) == []

            # 1. One request; plans only.
            created = await api.post(
                "/apps", json=body, headers={"Idempotency-Key": uuid.uuid4().hex}
            )
            assert created.status_code == 202, created.text
            app_id = created.json()["id"]
            planned = await wait_for(api, app_id, "awaiting_replica_approval")
            assert planned["router"] is None
            assert [m["state"] for m in planned["members"]] == ["planned", "planned"]
            await nothing_exists()

            # 2. A partial approval is refused; then both digests.
            digests = {m["operation_id"]: m["plan_digest"] for m in planned["members"]}
            first = next(iter(digests))
            partial = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {first: digests[first]}}
            )
            assert partial.status_code == 409, partial.text
            assert partial.json()["error"]["reason"] == "app_gate_mismatch"
            await nothing_exists()
            approved = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": digests})
            assert approved.status_code == 202, approved.text
            gate = await wait_for(api, app_id, "awaiting_router_approval")
            assert [m["state"] for m in gate["members"]] == ["succeeded", "succeeded"]
            assert gate["router"]["state"] == "planned"
            aws_id, azure_id = (m["resource_id"] for m in gate["members"])
            aws = (await api.get(f"/resources/{aws_id}")).json()
            azure = (await api.get(f"/resources/{azure_id}")).json()
            index = await cloud.get(f"{S3}/{aws_name}/index.html")
            assert index.status_code == 200 and aws_name in index.text, index.text
            health = await cloud.get(f"{S3}/{aws_name}/health")
            assert (health.status_code, health.text) == (200, "ok")
            assert await status(cloud, "azure", azure_name) == 200
            container = await cloud.get(f"http://localhost:4577/{azure_name}/app?restype=container")
            assert container.status_code == 200, container.text
            endpoints = {"aws": aws["outputs"]["endpoint"], "azure": azure["outputs"]["endpoint"]}
            assert aws_name in endpoints["aws"] and azure_name in endpoints["azure"], endpoints
            router_id = gate["router"]["resource_id"]
            router = (await api.get(f"/resources/{router_id}")).json()
            assert router["input_refs"] == {
                "primary_endpoint": {"resource_id": aws_id, "output": "endpoint"},
                "secondary_endpoint": {"resource_id": azure_id, "output": "endpoint"},
                "primary_health_path": {"resource_id": aws_id, "output": "health_path"},
                "secondary_health_path": {"resource_id": azure_id, "output": "health_path"},
            }
            assert await zones(cloud, zone_name) == []  # router only planned

            # 3. Approve the router: zone, failover records and per-replica health paths.
            router_digest = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
            ready_req = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": router_digest}
            )
            assert ready_req.status_code == 202, ready_req.text
            ready = await wait_for(api, app_id, "ready")
            assert ready["next_action"] == "done" and ready["error"] is None
            zone_ids = await zones(cloud, zone_name)
            assert len(zone_ids) == 1
            zone_id = zone_ids[0]
            records = await failover_records(cloud, zone_id)
            assert set(records) == {"PRIMARY", "SECONDARY"}
            assert records["PRIMARY"][0] == endpoints["aws"]
            assert records["SECONDARY"][0] == endpoints["azure"]
            checks = await health_checks(cloud)
            assert checks[records["PRIMARY"][1]] == (endpoints["aws"], "/health")
            assert checks[records["SECONDARY"][1]] == (
                endpoints["azure"],
                "/app?restype=container",
            )

            # 4. Listing, labels, no placement identifiers.
            listed = await api.get("/apps")
            assert listed.status_code == 200, listed.text
            assert [a["id"] for a in listed.json()["items"]] == [app_id]
            assert_no_placement(listed.text)
            assert_no_placement((await api.get(f"/apps/{app_id}")).text)
            roles = {aws_id: "replica", azure_id: "replica", router_id: "router"}
            found = await api.get(f"/resources?label=app={app}")
            assert sorted(r["id"] for r in found.json()["items"]) == sorted(roles)
            assert_no_placement(found.text)
            for resource_id, role in roles.items():
                resource = (await api.get(f"/resources/{resource_id}")).json()
                assert resource["labels"] == {"app": app, "app_role": role}
                assert resource["state"] == "ready"
                assert resource["estimated_monthly_cost"] == COST
                assert_no_placement(json_text(resource))
            router = (await api.get(f"/resources/{router_id}")).json()
            assert sorted(o["address"] for o in router["managed_objects"]) == sorted(
                a for a, _ in OBJECTS["ha-router-aws"]
            )

            # 5. Failover through the app: in-place router update behind the router gate.
            def key():
                return {"Idempotency-Key": uuid.uuid4().hex}

            azure_path = "/app?restype=container"
            aws_roles = {"PRIMARY": endpoints["aws"], "SECONDARY": endpoints["azure"]}
            azure_roles = {"PRIMARY": endpoints["azure"], "SECONDARY": endpoints["aws"]}

            noop = await api.post(f"/apps/{app_id}/failover", json={"primary": 0}, headers=key())
            assert noop.status_code == 409, noop.text
            assert noop.json()["error"]["reason"] == "app_failover_noop"

            async def failover(
                primary: int, new_roles: dict, new_paths: dict, old_roles: dict, first_failover
            ):
                started = await api.post(
                    f"/apps/{app_id}/failover", json={"primary": primary}, headers=key()
                )
                assert started.status_code == 202, started.text
                gate = await wait_for(api, app_id, "awaiting_router_approval")
                assert gate["primary"] == 1 - primary  # nothing is deployed yet
                op = (await api.get(f"/operations/{gate['router']['operation_id']}")).json()
                assert op["changes"], json_text(op)
                # Records update in place and the zone is untouched. The first failover updates
                # the health checks in place too. Floci's UpdateHealthCheck drops Type and
                # RequestInterval from later reads (proven by hand 2026-10-03; real AWS keeps
                # them), so on a later failover the provider may replace the health checks, and
                # then only because of exactly those two attributes.
                by_type = {}
                for c in op["changes"]:
                    by_type.setdefault(c["type"], []).append(c)
                assert set(by_type) == {"aws_route53_record", "aws_route53_health_check"}, by_type
                assert [c["actions"] for c in by_type["aws_route53_record"]] == [
                    ["update"],
                    ["update"],
                ], by_type
                for c in by_type["aws_route53_health_check"]:
                    if first_failover:
                        assert c["actions"] == ["update"], c
                    else:
                        assert c["actions"] in (["update"], ["delete", "create"]), c
                        if c["actions"] != ["update"]:
                            assert sorted(c["replace_paths"]) == ["request_interval", "type"], c
                summary = op["change_summary"]
                assert summary["create"] == 0 and summary["delete"] == 0, summary
                assert summary["destructive"] is (summary["replace"] > 0), summary
                # Route 53 is unchanged until the plan is approved.
                before = await failover_records(cloud, zone_id)
                assert {r: before[r][0] for r in before} == old_roles
                assert await zones(cloud, zone_name) == [zone_id]
                digest = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
                approved = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": digest})
                assert approved.status_code == 202, approved.text
                done = await wait_for(api, app_id, "ready")
                assert done["primary"] == primary and done["error"] is None
                assert await zones(cloud, zone_name) == [zone_id]
                now = await failover_records(cloud, zone_id)
                assert {r: now[r][0] for r in now} == new_roles
                checks_now = await health_checks(cloud)
                assert {r: checks_now[now[r][1]] for r in now} == {
                    r: (new_roles[r], new_paths[r]) for r in now
                }

            await failover(
                1,
                azure_roles,
                {"PRIMARY": azure_path, "SECONDARY": "/health"},
                aws_roles,
                first_failover=True,
            )
            await failover(
                0,
                aws_roles,
                {"PRIMARY": "/health", "SECONDARY": azure_path},
                azure_roles,
                first_failover=False,
            )

            # 6. Teardown through the app: router first, then both replicas.
            started = await api.post(f"/apps/{app_id}/destroy", headers=key())
            assert started.status_code == 202, started.text
            gate = await wait_for(api, app_id, "awaiting_router_destroy_approval")
            assert await zones(cloud, zone_name) == [zone_id]
            assert [m["role"] for m in gate["teardown"]] == ["router_destroy"]
            wrong = await api.post(
                f"/apps/{app_id}/approve",
                json={"plan_digests": {m["operation_id"]: "0" * 64 for m in gate["members"]}},
            )
            assert wrong.status_code == 409, wrong.text
            assert wrong.json()["error"]["reason"] == "app_gate_mismatch"
            assert await zones(cloud, zone_name) == [zone_id]
            td = gate["teardown"][0]
            approved = await api.post(
                f"/apps/{app_id}/approve",
                json={"plan_digests": {td["operation_id"]: td["plan_digest"]}},
            )
            assert approved.status_code == 202, approved.text
            gate = await wait_for(api, app_id, "awaiting_replica_destroy_approval")
            assert await zones(cloud, zone_name) == []
            assert (await cloud.get(f"{ROUTE53}/hostedzone/{zone_id}")).status_code == 404
            assert (await cloud.get(f"{S3}/{aws_name}/health")).status_code == 200
            assert await status(cloud, "azure", azure_name) == 200
            replicas = {
                m["operation_id"]: m["plan_digest"]
                for m in gate["teardown"]
                if m["role"] == "replica_destroy"
            }
            assert len(replicas) == 2 and all(replicas.values())
            approved = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": replicas})
            assert approved.status_code == 202, approved.text
            done = await wait_for(api, app_id, "destroyed")
            assert done["next_action"] == "done" and done["error"] is None
            await nothing_exists()
            for resource_id in (aws_id, azure_id, router_id):
                resource = (await api.get(f"/resources/{resource_id}")).json()
                assert resource["state"] == "destroyed", json_text(resource)

    asyncio.run(scenario())
