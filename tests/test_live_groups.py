import io
import json
import os
import urllib.error

import pytest
import yaml
from fastapi.testclient import TestClient

from app import app_activities, apps, azure_identity, ledger
from app.contracts import OperationError
from app.main import app
from app.settings import settings
from tests.test_apps import (  # noqa: F401  (fixtures and helpers reused)
    app_body,
    digests,
    ha_router_pattern,
    key,
    ready_app,
    router_pattern,
    run_plans,
)

DEPLOY, OTHER = "deploy-group", "other-group"


@pytest.fixture
def tenants(monkeypatch):
    units = {
        "business_units": {
            "finance": {
                "groups": [DEPLOY],
                "patterns": ["demo"],
                "environments": {"dev": {"subscription_id": "sub"}},
            }
        }
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))
    return {"business_unit": "finance", "environment": "dev"}


@pytest.fixture
def live(monkeypatch):
    """Live checks on with entra auth; `live.groups` is what Graph reports (None: gone)."""
    monkeypatch.setattr(settings, "live_group_checks", True)
    monkeypatch.setattr(settings, "auth_mode", "entra")
    state = type("Live", (), {"groups": {DEPLOY}, "calls": [], "error": None})()

    def fake(object_id):
        state.calls.append(object_id)
        if state.error:
            raise state.error
        return state.groups

    monkeypatch.setattr(azure_identity, "member_groups", fake)
    return state


def test_off_by_default_uses_snapshot(monkeypatch, tenants):
    def boom(_):
        raise AssertionError("Graph must not be called")

    monkeypatch.setattr(azure_identity, "member_groups", boom)
    caller = apps.current_caller("oid", [DEPLOY])
    assert caller.groups == {DEPLOY}
    apps.ensure_access(tenants, caller)


def test_on_but_auth_none_keeps_snapshot(monkeypatch, live):
    monkeypatch.setattr(settings, "auth_mode", "none")
    assert apps.current_caller("oid", [DEPLOY]).groups == {DEPLOY}
    assert live.calls == []


def test_still_member_unchanged(live, tenants):
    caller = apps.current_caller("oid", [DEPLOY])
    assert caller.groups == {DEPLOY} and live.calls == ["oid"]
    apps.ensure_access(tenants, caller)


def test_removed_from_group_is_revoked(live, tenants):
    live.groups = {OTHER}
    with pytest.raises(OperationError) as err:
        apps.ensure_access(tenants, apps.current_caller("oid", [DEPLOY]))
    assert err.value.reason == "requester_access_revoked"


def test_group_added_later_is_not_granted(live, tenants):
    live.groups = {DEPLOY, OTHER}
    assert apps.current_caller("oid", [DEPLOY]).groups == {DEPLOY}
    live.groups = {DEPLOY, OTHER}
    assert apps.current_caller("oid", [OTHER]).groups == {OTHER}
    assert apps.current_caller("oid", [OTHER, "x"]).groups == {OTHER}


def test_unknown_object_has_no_groups(live, tenants):
    live.groups = None
    caller = apps.current_caller("oid", [DEPLOY])
    assert caller.groups == frozenset()
    with pytest.raises(OperationError) as err:
        apps.ensure_access(tenants, caller)
    assert err.value.reason == "requester_access_revoked"


def test_graph_error_is_transient(live):
    live.error = RuntimeError("graph down")
    with pytest.raises(OperationError) as err:
        apps.current_caller("oid", [DEPLOY])
    assert err.value.reason == "group_check_unavailable"
    assert err.value.status_code == 503
    assert apps.transient(err.value)


class FakeToken:
    token = "secret-token"


class FakeCredential:
    scopes = None

    def get_token(self, scope):
        FakeCredential.scopes = scope
        return FakeToken()


def http_error(code):
    return urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(b"{}"))


def test_member_groups_request(monkeypatch):
    monkeypatch.setattr(azure_identity, "credential", FakeCredential)
    seen = {}

    def fake_urlopen(request, timeout):
        seen.update(request=request, timeout=timeout)
        return io.BytesIO(json.dumps({"value": ["g1", "g2"]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert azure_identity.member_groups("a/b") == {"g1", "g2"}
    request = seen["request"]
    assert FakeCredential.scopes == "https://graph.microsoft.com/.default"
    assert request.full_url == (
        "https://graph.microsoft.com/v1.0/directoryObjects/a%2Fb/getMemberGroups"
    )
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert json.loads(request.data) == {"securityEnabledOnly": False}
    assert seen["timeout"] == 10


@pytest.mark.parametrize("code, outcome", [(404, None), (500, "raises")])
def test_member_groups_http_errors(monkeypatch, code, outcome):
    monkeypatch.setattr(azure_identity, "credential", FakeCredential)

    def fake_urlopen(request, timeout):
        raise http_error(code)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    if outcome is None:
        assert azure_identity.member_groups("oid") is None
    else:
        with pytest.raises(urllib.error.HTTPError):
            azure_identity.member_groups("oid")


REAL = (
    "FORGEAPI_TEST_ENTRA_OID",
    "FORGEAPI_TEST_ENTRA_MEMBER_GROUP",
    "FORGEAPI_TEST_ENTRA_NONMEMBER_GROUP",
)


@pytest.mark.skipif(not all(os.environ.get(v) for v in REAL), reason="needs real Entra ids")
def test_real_graph(monkeypatch):
    oid, member, nonmember = (os.environ[v] for v in REAL)
    # The az CLI login, not any certificate or managed identity from the environment.
    monkeypatch.setattr(settings, "azure_client_certificate_path", None)
    monkeypatch.setattr(settings, "azure_use_managed_identity", False)
    monkeypatch.setattr(settings, "live_group_checks", True)
    monkeypatch.setattr(settings, "auth_mode", "entra")
    groups = azure_identity.member_groups(oid)
    assert member in groups and nonmember not in groups
    assert apps.current_caller(oid, [member, nonmember]).groups == {member}


def test_router_accept_retries_when_group_check_unavailable(
    recorded_dispatcher, router_pattern, monkeypatch  # noqa: F811
):
    client = TestClient(app)
    created = client.post("/apps", json=app_body(), headers=key()).json()
    app_id = created["id"]
    run_plans(recorded_dispatcher)
    planned = client.get(created["links"]["self"]).json()
    approved = client.post(created["links"]["approve"], json={"plan_digests": digests(planned)})
    assert approved.status_code == 202
    run_plans(recorded_dispatcher)

    monkeypatch.setattr(settings, "live_group_checks", True)
    monkeypatch.setattr(settings, "auth_mode", "entra")
    graph = {"error": RuntimeError("graph down"), "groups": set()}

    def fake(_object_id):
        if graph["error"]:
            raise graph["error"]
        return graph["groups"]

    monkeypatch.setattr(azure_identity, "member_groups", fake)
    before = ledger.get_app(app_id)
    # 503 group_check_unavailable is transient: "retry", not a raise and not a failed app.
    assert app_activities.accept_app_router(app_id) == "retry"
    after = ledger.get_app(app_id)
    assert after["router_operation_id"] is None
    assert after == before  # untouched: no error recorded, no router operation
    assert app_activities.check_app(app_id) == "router_needed"  # not failed

    graph["error"] = None  # Graph is back; the requester's groups are unchanged
    router_id = app_activities.accept_app_router(app_id)
    assert router_id.startswith("op_")
    assert ledger.get_app(app_id)["router_operation_id"] == router_id


def test_teardown_accept_retries_when_group_check_unavailable(
    recorded_dispatcher, ha_router_pattern, monkeypatch  # noqa: F811
):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    op = gate["teardown"][0]
    approve = client.post(
        f"/apps/{app_id}/approve", json={"plan_digests": {op["operation_id"]: op["plan_digest"]}}
    )
    assert approve.status_code == 202
    run_plans(recorded_dispatcher)
    assert app_activities.check_teardown(app_id) == "replicas_needed"

    monkeypatch.setattr(settings, "live_group_checks", True)
    monkeypatch.setattr(settings, "auth_mode", "entra")
    graph = {"error": RuntimeError("graph down")}

    def fake(_object_id):
        if graph["error"]:
            raise graph["error"]
        return {DEPLOY}

    monkeypatch.setattr(azure_identity, "member_groups", fake)
    result = app_activities.accept_teardown_replica_destroys(app_id)
    assert result == {"operations": [], "refused": False, "retry": True}
    assert ledger.get_app(app_id)["teardown"]["replicas"] is None
    assert app_activities.check_teardown(app_id) == "replicas_needed"  # not failed

    graph["error"] = None
    accepted = app_activities.accept_teardown_replica_destroys(app_id)
    assert accepted["refused"] is False and len(accepted["operations"]) == 2
