"""Resource inventory: a caller's view of what exists, independent of any one operation."""

import json
from pathlib import Path

import pytest

from app.client import Client, ClientError, main
from app.contracts import Discovery
from app.main import app, caller_context
from app.settings import settings
from app.tenants import Caller
from tests.conftest import git
from tests.test_client import http_client as http_client
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import INTENT, submit
from tests.test_operations import agent_client as agent_client

OLD = Path(__file__).parent / "fixtures" / "v1"

PUBLIC_RESOURCE_FIELDS = {
    "id",
    "state",
    "pattern",
    "version",
    "commit",
    "business_unit",
    "environment",
    "labels",
    "promoted_from",
    "input_refs",
    "outputs",
    "withheld_outputs",
    "latest_operation_id",
    "updated_at",
    "cloud",
    "region",
    "estimated_monthly_cost",
    "owned_by_caller",
    "created_at",
    "managed_objects",
    "drift_status",
    "drift_checked_at",
    "drift",
    "latest_version",
    "upgrade_available",
    "links",
}


def test_lifecycle_pending_ready_destroyed_through_public_contract(
    agent_client, recorded_dispatcher
):
    op = submit(agent_client, "lifecycle").json()
    resource_id = op["resource_id"]

    pending = agent_client.get(f"/resources/{resource_id}").json()
    assert set(pending) == PUBLIC_RESOURCE_FIELDS
    assert pending["state"] == "pending"
    assert pending["outputs"] is None
    assert pending["withheld_outputs"] is None
    assert pending["updated_at"] is None
    assert pending["pattern"] == "demo"
    assert pending["latest_operation_id"] == op["id"]
    assert pending["links"]["self"] == f"/resources/{resource_id}"
    assert pending["links"]["latest_operation"] == f"/operations/{op['id']}"

    assert recorded_dispatcher.run_next()  # plan only; never applies automatically
    assert agent_client.get(f"/resources/{resource_id}").json()["state"] == "pending"

    planned = agent_client.get(op["links"]["self"]).json()
    agent_client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()

    ready = agent_client.get(f"/resources/{resource_id}").json()
    assert ready["state"] == "ready"
    assert ready["outputs"]["path"]
    assert ready["withheld_outputs"] == []
    assert ready["latest_operation_id"] == op["id"]
    assert ready["updated_at"]

    destroy_op = submit(
        agent_client, "lifecycle-destroy", action="destroy", resource_id=resource_id
    ).json()
    mid_destroy = agent_client.get(f"/resources/{resource_id}").json()
    assert mid_destroy["state"] == "ready"  # not yet applied
    assert mid_destroy["latest_operation_id"] == destroy_op["id"]  # already in flight

    assert recorded_dispatcher.run_next()
    destroy_planned = agent_client.get(destroy_op["links"]["self"]).json()
    agent_client.post(
        destroy_op["links"]["execute"], json={"plan_digest": destroy_planned["plan_digest"]}
    )
    assert recorded_dispatcher.run_next()

    destroyed = agent_client.get(f"/resources/{resource_id}").json()
    assert destroyed["state"] == "destroyed"
    assert destroyed["latest_operation_id"] == destroy_op["id"]

    listing = agent_client.get("/resources").json()
    assert [item["id"] for item in listing["items"]] == [resource_id]
    assert listing["items"][0]["state"] == "destroyed"


def test_sensitive_outputs_withheld_and_no_private_field_leaks(
    agent_client, pattern_repo, monkeypatch, recorded_dispatcher
):
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + """
output "private_value" {
  value = "sensitive-output-marker"
  sensitive = true
}
output "arm_reference" { value = "/subscriptions/hidden-subscription/resourceGroups/test" }
"""
    )
    git(pattern_repo, "commit", "-qam", "sensitive outputs")
    git(pattern_repo, "tag", "v1.3.0")
    monkeypatch.setattr(settings, "azure_subscription_id", "hidden-subscription")
    op = submit(agent_client, version="v1.3.0").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    agent_client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()

    resource = agent_client.get(f"/resources/{op['resource_id']}")
    body = resource.json()
    assert body["state"] == "ready"
    assert set(body["withheld_outputs"]) == {"private_value", "arm_reference"}
    assert "sensitive-output-marker" not in resource.text
    assert "hidden-subscription" not in resource.text
    assert set(body) == PUBLIC_RESOURCE_FIELDS

    listing = agent_client.get("/resources")
    assert "sensitive-output-marker" not in listing.text
    assert "hidden-subscription" not in listing.text
    assert set(listing.json()["items"][0]) == PUBLIC_RESOURCE_FIELDS


def test_placement_details_never_leak_into_resource_view(placed):
    accepted = placed_request(placed, "leak-check").json()
    resource = placed.get(f"/resources/{accepted['resource_id']}")
    assert resource.status_code == 200
    assert "hidden-finance" not in resource.text
    assert "cost_center" not in resource.text
    assert set(resource.json()) == PUBLIC_RESOURCE_FIELDS
    listing = placed.get("/resources")
    assert "hidden-finance" not in listing.text
    assert "cost_center" not in listing.text


def test_other_actor_gets_404_and_empty_list(agent_client):
    op = submit(agent_client, "iso-actor").json()
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        assert agent_client.get(f"/resources/{op['resource_id']}").status_code == 404
        assert agent_client.get("/resources").json()["items"] == []
    finally:
        app.dependency_overrides.clear()


def test_other_business_unit_gets_404_and_empty_list(placed, monkeypatch):
    accepted = placed_request(placed, "unit-iso").json()
    monkeypatch.setattr(settings, "dev_groups", "hr")
    assert placed.get(f"/resources/{accepted['resource_id']}").status_code == 404
    assert placed.get("/resources").json()["items"] == []


def test_malformed_resource_id_in_path_is_404_not_422(agent_client):
    assert agent_client.get("/resources/not-a-real-id").status_code == 404


def test_after_cursor_does_not_require_an_existing_anchor(agent_client):
    submit(agent_client, "after-no-anchor")
    response = agent_client.get("/resources", params={"after": "res_" + "0" * 32})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1


def test_invalid_after_and_limit_are_422(agent_client):
    for params in ({"after": "bad"}, {"limit": 0}, {"limit": 101}, {"after": "op_" + "a" * 32}):
        response = agent_client.get("/resources", params=params)
        assert response.status_code == 422, params
        assert response.json()["error"]["code"] == "invalid_request"


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_root_and_v1_parity(agent_client, prefix):
    op = submit(agent_client, f"parity-{'v1' if prefix else 'root'}").json()
    listing = agent_client.get(f"{prefix}/resources").json()
    assert listing["items"][0]["links"]["self"] == f"{prefix}/resources/{op['resource_id']}"
    single = agent_client.get(f"{prefix}/resources/{op['resource_id']}").json()
    assert single["links"]["self"] == f"{prefix}/resources/{op['resource_id']}"
    assert single["links"]["latest_operation"] == f"{prefix}/operations/{op['id']}"
    operation = agent_client.get(f"{prefix}/operations/{op['id']}").json()
    assert operation["links"]["resource"] == f"{prefix}/resources/{op['resource_id']}"


def test_capability_and_links_advertised(agent_client):
    for prefix in ("", "/v1"):
        discovery = agent_client.get(f"{prefix}/agent").json()
        assert discovery["capabilities"]["resource_inventory"] is True
        assert discovery["links"]["resources"] == f"{prefix}/resources"


def test_pagination_stable_across_insert_and_inplace_update(agent_client, recorded_dispatcher):
    created = [submit(agent_client, f"page-{i}").json() for i in range(5)]
    by_id = {op["resource_id"]: op for op in created}
    ids = sorted(by_id)

    first = agent_client.get("/v1/resources?limit=2").json()
    assert [item["id"] for item in first["items"]] == ids[:2]
    assert first["next_after"] == ids[1]
    assert all(item["links"]["self"].startswith("/v1/resources/") for item in first["items"])

    # A resource not yet returned is updated in place: INSERT OR REPLACE changes its rowid,
    # but pagination pages by id, so it must still appear exactly once at its same position.
    not_yet_seen = ids[2]
    submit(
        agent_client,
        "page-mutate",
        resource_id=not_yet_seen,
        inputs={"filename": "agent.txt", "content": "updated-mid-traversal"},
    )
    # A brand-new resource is also created mid-traversal.
    submit(agent_client, "page-insert")

    seen = [item["id"] for item in first["items"]]
    after = first["next_after"]
    while after is not None:
        page = agent_client.get("/v1/resources", params={"limit": 2, "after": after}).json()
        seen.extend(item["id"] for item in page["items"])
        after = page["next_after"]

    assert [i for i in seen if i in by_id] == ids  # every pre-existing resource, once, in order
    assert len(seen) == len(set(seen))  # no duplicates anywhere, including the new insert


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_pagination_across_business_unit_visibility(placed, monkeypatch, prefix):
    finance = placed_request(placed, "finance-page").json()["resource_id"]
    monkeypatch.setattr(settings, "dev_groups", "hr")
    hr = placed_request(placed, "hr-page").json()["resource_id"]
    listing = placed.get(f"{prefix}/resources").json()
    assert [item["id"] for item in listing["items"]] == [hr]
    assert finance not in [item["id"] for item in listing["items"]]


# --- app/client.py ---------------------------------------------------------------------------


def _discovery_with_resources():
    old = json.loads((OLD / "old_discovery.json").read_text())
    old["capabilities"] = {"resource_inventory": True}
    old["links"] = {**old["links"], "resources": "/resources"}
    return old


def _resource_fixture(char):
    rid, opid = "res_" + char * 32, "op_" + char * 32
    return {
        "id": rid,
        "state": "pending",
        "pattern": "demo",
        "version": "v1.0.0",
        "commit": "c" * 40,
        "business_unit": None,
        "environment": None,
        "outputs": None,
        "withheld_outputs": None,
        "latest_operation_id": opid,
        "updated_at": None,
        "links": {"self": f"/resources/{rid}", "latest_operation": f"/operations/{opid}"},
    }


def test_client_resources_and_resource_round_trip_over_real_http(http_client, recorded_dispatcher):
    client = http_client
    discovery = client.discover()
    assert discovery["capabilities"]["resource_inventory"] is True
    submitted = client.submit(INTENT, "client-resource")
    resource_id = submitted["resource_id"]

    page = client.resources(limit=10)
    assert [item["id"] for item in page["items"]] == [resource_id]
    assert page["next_after"] is None

    single = client.resource(resource_id)
    assert single["id"] == resource_id
    assert single["state"] == "pending"
    assert single["latest_operation_id"] == submitted["id"]


def test_client_resources_refuses_without_capability(monkeypatch):
    old = json.loads((OLD / "old_discovery.json").read_text())
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: old)
    with pytest.raises(ClientError, match="resource inventory"):
        client.resources()
    with pytest.raises(ClientError, match="resource inventory"):
        client.resource("res_" + "a" * 32)

    client._discovery = Discovery.model_validate(
        {**old, "capabilities": {"resource_inventory": False}}
    )
    with pytest.raises(ClientError, match="resource inventory"):
        client.resources()
    with pytest.raises(ClientError, match="resource inventory"):
        client.resource("res_" + "a" * 32)


@pytest.mark.parametrize(
    "kwargs", [{"limit": 0}, {"limit": 101}, {"limit": True}, {"after": "bad"}]
)
def test_client_resources_reject_invalid_arguments_before_http(monkeypatch, kwargs):
    client = Client("http://localhost:8080")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: pytest.fail("HTTP request"))
    with pytest.raises(ClientError):
        client.resources(**kwargs)


@pytest.mark.parametrize(
    ("items", "next_after", "kwargs"),
    [
        (["a", "a"], None, {}),
        (["a"], None, {"after": "res_" + "a" * 32}),
        (["a", "b"], "res_" + "c" * 32, {}),
        ([], "res_" + "a" * 32, {}),
    ],
)
def test_client_resources_reject_inconsistent_page(monkeypatch, items, next_after, kwargs):
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(_discovery_with_resources())
    page = {"items": [_resource_fixture(c) for c in items], "next_after": next_after}
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: page)
    with pytest.raises(ClientError):
        client.resources(**kwargs)


def test_client_resources_accepts_consistent_page(monkeypatch):
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(_discovery_with_resources())
    page = {
        "items": [_resource_fixture("a"), _resource_fixture("b")],
        "next_after": "res_" + "b" * 32,
    }
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: page)
    assert client.resources() == page


def test_client_resource_rejects_identity_and_link_mismatch(monkeypatch):
    client = Client("http://localhost:8080")
    client._discovery = Discovery.model_validate(_discovery_with_resources())
    good = _resource_fixture("a")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: good)
    assert client.resource("res_" + "a" * 32)["id"] == good["id"]

    mismatched = {**good, "id": "res_" + "b" * 32}
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: mismatched)
    with pytest.raises(ClientError, match="identity mismatch"):
        client.resource("res_" + "a" * 32)

    bad_link = {**good, "links": {**good["links"], "self": "https://evil.example/resources/x"}}
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: bad_link)
    with pytest.raises(ClientError):
        client.resource("res_" + "a" * 32)


def test_cli_resources_invokes_client(monkeypatch, capsys):
    page = {"items": [], "next_after": None}
    called = []

    def resources(self, *, limit, after, **filters):
        called.append((limit, after))
        return page

    monkeypatch.setattr(Client, "resources", resources)
    assert (
        main(
            [
                "--url",
                "http://localhost:8080",
                "resources",
                "--limit",
                "5",
                "--after",
                "res_" + "a" * 32,
            ]
        )
        == 0
    )
    assert called == [(5, "res_" + "a" * 32)]
    assert json.loads(capsys.readouterr().out) == page


def test_cli_resource_invokes_client(monkeypatch, capsys):
    result = {"id": "res_" + "a" * 32, "state": "ready"}
    called = []

    def resource(self, resource_id):
        called.append(resource_id)
        return result

    monkeypatch.setattr(Client, "resource", resource)
    assert main(["--url", "http://localhost:8080", "resource", "res_" + "a" * 32]) == 0
    assert called == ["res_" + "a" * 32]
    assert json.loads(capsys.readouterr().out) == result


def _ready_one(client, dispatcher):
    """Two resources: the first applied to ready, the second left pending."""
    ready = submit(client, "flt-ready", inputs={**INTENT["inputs"], "filename": "a.txt"}).json()
    assert dispatcher.run_next()
    planned = client.get(ready["links"]["self"]).json()
    client.post(ready["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert dispatcher.run_next()
    pending = submit(client, "flt-pending", inputs={**INTENT["inputs"], "filename": "b.txt"}).json()
    return ready["resource_id"], pending["resource_id"]


def _ids(client, **params):
    return [r["id"] for r in client.get("/resources", params=params).json()["items"]]


def test_resource_filters_each_and_combined(agent_client, recorded_dispatcher):
    ready, pending = _ready_one(agent_client, recorded_dispatcher)
    assert _ids(agent_client, state="ready") == [ready]
    assert _ids(agent_client, state="pending") == [pending]
    assert _ids(agent_client, state="destroyed") == []
    assert sorted(_ids(agent_client, pattern="demo")) == sorted([ready, pending])
    assert _ids(agent_client, pattern="local-file") == []
    assert _ids(agent_client, pattern="demo", state="ready") == [ready]
    assert _ids(agent_client, pattern="local-file", state="ready") == []
    assert agent_client.get("/resources", params={"state": "bogus"}).status_code == 422


def test_resource_filter_composes_with_pagination(agent_client, recorded_dispatcher):
    for i in range(3):
        submit(agent_client, f"flt-page-{i}", inputs={**INTENT["inputs"], "filename": f"{i}.txt"})
    first = agent_client.get("/resources", params={"state": "pending", "limit": 2}).json()
    assert len(first["items"]) == 2 and first["next_after"]
    second = agent_client.get(
        "/resources", params={"state": "pending", "limit": 2, "after": first["next_after"]}
    ).json()
    assert len(second["items"]) == 1 and second["next_after"] is None
    assert agent_client.get("/resources", params={"state": "ready"}).json()["items"] == []


def test_resource_filters_never_cross_business_units(placed, monkeypatch):
    placed_request(placed, "flt-unit")
    monkeypatch.setattr(settings, "dev_groups", "hr")
    for params in ({"pattern": "demo"}, {"environment": "dev"}, {"state": "pending"}):
        assert placed.get("/resources", params=params).json()["items"] == []


def test_environment_filter_in_placed_mode(placed):
    accepted = placed_request(placed, "flt-env").json()
    only = {"id": accepted["resource_id"]}
    assert [
        r["id"] for r in placed.get("/resources", params={"environment": "dev"}).json()["items"]
    ] == [only["id"]]
    assert placed.get("/resources", params={"environment": "prod"}).json()["items"] == []
