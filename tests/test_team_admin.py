"""Database-backed team administration: storage, tenancy source, operator-only /admin API."""

import json
import shutil
import sqlite3
import threading
import time

import pytest
import yaml
from fastapi.testclient import TestClient

from app import catalog, ledger, policy, team_admin, team_check, tenants
from app.contracts import Intent, OperationError, canonical_intent
from app.main import app
from app.settings import settings
from tests.conftest import git

OPS, FIN, HR = "grp-ops", "grp-fin", "grp-hr"
TARGET_ID = "hidden-subscription-id-1234"
LOCAL = {"inputs": {"filename": "x.txt", "content": "hello"}}


def team(groups=(FIN,), patterns=("local-file",), **env):
    return {
        "groups": list(groups),
        "inject": {"business_unit": "finance"},
        "patterns": list(patterns),
        "environments": {"dev": env},
    }


class Admin:
    """Calls the API as a caller with the given groups (auth is off; dev_groups names them)."""

    def __init__(self, monkeypatch):
        self.monkeypatch, self.http = monkeypatch, TestClient(app)

    def call(self, method, path, *, groups=OPS, headers=None, **kwargs):
        self.monkeypatch.setattr(settings, "dev_groups", groups)
        return self.http.request(method, path, headers=headers, **kwargs)

    def create(self, name="finance", doc=None, **kw):
        body = {"name": name, "team": doc or team(), "reason": "onboard"}
        return self.call("POST", "/admin/teams", json=body, **kw)

    def put(self, name, doc, rev, *, ack=(), reason="change", **kw):
        headers = {"If-Match": f'"{rev}"'} if rev is not None else None
        body = {"team": doc, "reason": reason, "acknowledge_warnings": list(ack)}
        return self.call("PUT", f"/admin/teams/{name}", json=body, headers=headers, **kw)

    def post_with(self, name, action, body, rev, **kw):
        headers = {"If-Match": f'"{rev}"'} if rev is not None else None
        return self.call("POST", f"/admin/teams/{name}/{action}", json=body, headers=headers, **kw)

    def intent(self, key="k1", groups=FIN, **updates):
        body = {"pattern": "local-file", "environment": "dev", **LOCAL, **updates}
        return self.call(
            "POST", "/operations", json=body, groups=groups, headers={"Idempotency-Key": key}
        )

    def get(self, path, **kw):
        return self.call("GET", path, **kw)


@pytest.fixture
def admin(monkeypatch, recorded_dispatcher):
    monkeypatch.setattr(settings, "tenants_source", "db")
    monkeypatch.setattr(settings, "operator_groups", OPS)
    return Admin(monkeypatch)


def events(*, outcome=None):
    with ledger.connect(write=False) as con:
        sql = "SELECT actor, action, outcome FROM events WHERE action LIKE 'team.%'"
        rows = con.execute(
            sql + (" AND outcome=?" if outcome else ""), [outcome] if outcome else []
        )
        return [tuple(r) for r in rows]


def table(name):
    with ledger.connect(write=False) as con:
        return con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]


# --- file mode -------------------------------------------------------------------------------


def test_file_mode_is_unchanged_and_admin_is_read_only(monkeypatch, recorded_dispatcher):
    mapping = {"operators": [OPS], "business_units": {"finance": team()}}
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    admin = Admin(monkeypatch)
    assert settings.tenants_source == "file"
    assert not hasattr(tenants, "team_store")  # never loaded in file mode
    listed = admin.get("/admin/teams")
    assert listed.status_code == 200 and listed.json()["source"] == "file"
    assert [t["name"] for t in listed.json()["items"]] == ["finance"]
    one = admin.get("/admin/teams/finance")
    assert one.status_code == 200 and one.headers["ETag"] == '"0"'
    assert one.json()["team"]["groups"] == [FIN] and one.json()["source"] == "file"
    # Reads of validation work; writes explain what to do.
    check = admin.call("POST", "/admin/teams/validate", json={"name": "other", "team": team()})
    assert check.status_code == 200
    for response in (
        admin.create("other"),
        admin.put("finance", team(), 0),
        admin.post_with("finance", "archive", {"reason": "r"}, 0),
        admin.call(
            "POST",
            "/admin/teams/import",
            json={"yaml": "business_units: {}", "apply": True, "reason": "r"},
        ),
    ):
        assert response.status_code == 409, response.text
        assert response.json()["error"]["reason"] == "tenants_source_file"
    assert table("teams") == 0
    # Existing behavior: a member of the file's team places an intent as before.
    assert admin.intent(groups=FIN).status_code == 202
    # Non-operators: 403 even in file mode.
    assert admin.get("/admin/teams", groups=FIN).status_code == 403


# --- creating, reading, placing --------------------------------------------------------------


def test_create_then_read_back_with_etag_and_audit(admin):
    created = admin.create()
    assert created.status_code == 201, created.text
    assert created.headers["ETag"] == '"1"'
    assert created.headers["Location"] == "/admin/teams/finance"
    body = created.json()
    assert body["revision"] == 1 and body["state"] == "active" and body["source"] == "db"
    assert body["created_by"] == "local"
    read = admin.get("/admin/teams/finance")
    assert read.json()["team"] == team() and read.headers["ETag"] == '"1"'
    listing = admin.get("/v1/admin/teams").json()["items"][0]
    assert listing["name"] == "finance" and listing["groups"] == 1 and listing["resources"] == 0
    assert listing["environments"] == ["dev"] and listing["findings"]["errors"] == 0
    assert admin.create().status_code == 409  # exists
    assert admin.create().json()["error"]["reason"] == "team_exists"
    # Audit: accepted names only team and revision; the refusal names the actor and team.
    assert ("local", "team.create finance r1", "accepted") in events()
    assert ("local", "team.create finance", "refused") in events(outcome="refused")
    for _, action, _ in events():
        assert FIN not in action and "groups" not in action
    assert admin.get("/admin/teams/nope").status_code == 404
    bad = admin.create("Bad_Name")
    assert bad.status_code == 422


def test_errors_refuse_with_findings_and_write_nothing(admin):
    doc = team(groups=())
    response = admin.create(doc=doc)
    assert response.status_code == 422
    detail = response.json()["error"]["detail"]
    assert "no_groups" in {f["code"] for f in detail["findings"]}
    assert table("teams") == 0 and table("team_revisions") == 0
    assert not events(outcome="accepted")
    assert events(outcome="refused") == [("local", "team.create finance", "refused")]
    # An operators key inside a team is a validation error: no self-escalation by API.
    sneaky = {**team(), "operators": [FIN]}
    response = admin.create(doc=sneaky)
    assert response.status_code == 422
    assert "schema_unknown_key" in {
        f["code"] for f in response.json()["error"]["detail"]["findings"]
    }


def test_operators_come_only_from_settings(admin, monkeypatch):
    assert admin.create(doc=team(groups=(FIN, HR))).status_code == 201
    # Team members are not operators, however the database is edited through the API.
    assert admin.get("/admin/teams", groups=FIN).status_code == 403
    monkeypatch.setattr(settings, "operator_groups", "")
    assert admin.get("/admin/teams", groups=OPS).status_code == 403
    monkeypatch.setattr(settings, "operator_groups", f"x, {FIN}")
    assert admin.get("/admin/teams", groups=FIN).status_code == 200
    assert tenants.is_operator(tenants.Caller("c", frozenset({FIN})))
    assert not tenants.is_operator(tenants.Caller("c", frozenset({HR})))
    assert not tenants.is_auditor(tenants.Caller("c", frozenset({OPS})))
    monkeypatch.setattr(settings, "auditor_groups", OPS)
    assert tenants.is_auditor(tenants.Caller("c", frozenset({OPS})))


def test_every_admin_route_is_403_for_non_operators(admin):
    assert admin.create().status_code == 201
    routes = [
        ({m.upper() for m in methods}, path)
        for path, methods in app.openapi()["paths"].items()
        if "/admin/" in path
    ]
    assert len(routes) == 18  # 9 paths, root and /v1
    hits = 0
    for methods, path in routes:
        concrete = path.replace("{name}", "finance").replace("{revision}", "1")
        for method in methods:
            headers = {"If-Match": '"1"'}
            response = admin.call(
                method, concrete, groups=FIN, headers=headers, json={} if method != "GET" else None
            )
            assert response.status_code == 403, (method, path, response.status_code)
            assert response.json()["error"]["code"] == "permission_denied"
            hits += 1
    assert hits == 22
    nobody = admin.get("/admin/teams", groups="")
    assert nobody.status_code == 403
    with ledger.connect(write=False) as con:
        denied = [
            r["action"] for r in con.execute("SELECT action FROM events WHERE outcome='refused'")
        ]
    # Every denied write (POST/PUT, root and /v1) was audited, with the route template.
    assert len([a for a in denied if "/admin/" in a or a.endswith("/admin/teams")]) == 14
    assert admin.get("/admin/teams/finance", groups=OPS).status_code == 200


def test_discovery_advertises_admin(admin):
    body = admin.get("/v1/agent").json()
    assert body["capabilities"]["team_admin"] is True
    assert body["caller"] == {"operator": True}
    assert admin.create().status_code == 201
    member = admin.get("/v1/agent", groups=FIN).json()
    assert member["caller"] == {"operator": False}
    assert member["business_units"][0]["name"] == "finance"
    assert member["business_units"][0]["archived"] is False


def cloud_pattern(pattern_repo):
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + '\nvariable "subscription_id" { type = string }\nvariable "region" { type = string }\n'
        'output "placement_id" { value = var.subscription_id }\n'
    )
    (pattern_repo / "config.yaml").write_text("estimated_costs: 60\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "cloud target contract")
    git(pattern_repo, "tag", "v1.2.0")
    raw = yaml.safe_load(settings.catalog_path.read_text())
    raw["patterns"]["demo"]["cloud"] = "azure"
    settings.catalog_path.write_text(yaml.safe_dump(raw))


def test_a_db_team_places_a_real_plan_with_the_right_target(
    admin, pattern_repo, recorded_dispatcher
):
    cloud_pattern(pattern_repo)
    target = {"azure": {"subscription_id": TARGET_ID, "region": "chosen-region"}}
    doc = team(patterns=("demo",), targets=target, budget_monthly=100)
    assert admin.create(doc=doc).status_code == 201
    body = {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": "dev",
        "inputs": {"filename": "proof.txt", "content": "hi"},
    }
    submitted = admin.call(
        "POST", "/operations", json=body, groups=FIN, headers={"Idempotency-Key": "p1"}
    )
    assert submitted.status_code == 202, submitted.text
    assert recorded_dispatcher.run_next()  # real local Terraform plan
    op = admin.get(submitted.json()["links"]["self"], groups=FIN).json()
    assert op["state"] == "planned", op
    stored = ledger.get(op["id"])
    assert stored["cloud_target"] == target["azure"]
    assert stored["injected"]["subscription_id"] == TARGET_ID
    assert stored["business_unit"] == "finance"
    # The identifiers never reach a non-operator, anywhere.
    for path in ("/operations", f"/operations/{op['id']}", "/resources", "/agent", "/patterns"):
        text = admin.get(path, groups=FIN).text
        assert TARGET_ID not in text, path
    assert TARGET_ID not in submitted.text and TARGET_ID not in str(op)
    # Operators can read them (and nothing else does).
    assert TARGET_ID in admin.get("/admin/teams/finance").text
    assert TARGET_ID not in admin.get("/admin/teams").text
    # A non-member gets nothing.
    assert admin.get("/operations", groups=HR).json()["items"] == []
    assert admin.intent("p2", groups=HR).status_code in (403, 404)


# --- concurrency, warnings, impact ------------------------------------------------------------


def test_if_match_is_required_and_checked(admin):
    admin.create()
    assert admin.put("finance", team(groups=(FIN, HR)), None).status_code == 428
    assert admin.put("finance", team(groups=(FIN, HR)), None).json()["error"]["code"] == (
        "precondition_required"
    )
    stale = admin.put("finance", team(groups=(FIN, HR)), 7)
    assert stale.status_code == 412 and stale.json()["error"]["code"] == "precondition_failed"
    assert admin.put("finance", team(groups=(FIN, HR)), "garbage").status_code == 412
    assert table("team_revisions") == 1
    ok = admin.put("finance", team(groups=(FIN, HR)), 1)
    assert ok.status_code == 200 and ok.headers["ETag"] == '"2"' and ok.json()["revision"] == 2
    assert admin.put("finance", team(groups=(FIN, HR)), 1).status_code == 412  # now stale
    weak = admin.call(
        "PUT",
        "/admin/teams/finance",
        json={"team": team(groups=(FIN,)), "reason": "weak etag"},
        headers={"If-Match": 'W/"2"'},
    )
    assert weak.status_code == 409  # ack needed (access_removed): the header itself matched
    for action, body in (("archive", {"reason": "r"}), ("revert", {"revision": 1, "reason": "r"})):
        assert admin.post_with("finance", action, body, None).status_code == 428
        assert admin.post_with("finance", action, body, 1).status_code == 412
    assert admin.put("nope", team(), 1).status_code == 404
    assert ("local", "team.update finance", "refused") in events(outcome="refused")


def test_warnings_need_acknowledgement(admin):
    admin.create(doc=team(groups=(FIN, HR)))
    smaller = team(groups=(FIN,))
    blocked = admin.put("finance", smaller, 1)
    assert blocked.status_code == 409
    error = blocked.json()["error"]
    assert error["reason"] == "warnings_not_acknowledged"
    assert error["detail"]["warning_codes"] == ["access_removed"]
    assert admin.get("/admin/teams/finance").json()["revision"] == 1
    wrong = admin.put("finance", smaller, 1, ack=["something_else"])
    assert wrong.status_code == 409
    done = admin.put("finance", smaller, 1, ack=["access_removed", "unneeded"])
    assert done.status_code == 200 and done.json()["revision"] == 2
    same = admin.put("finance", smaller, 2)
    assert same.status_code == 409 and same.json()["error"]["reason"] == "team_unchanged"
    assert admin.put("finance", smaller, 2, reason="").status_code == 422  # reason required


def seed_resource(admin, key="seed"):
    op = admin.intent(key).json()
    assert op["state"] == "queued"
    return op


def test_impact_errors_are_refused_without_writing_and_audited(admin, recorded_dispatcher):
    doc = team(subscription_id="sub-a")
    admin.create(doc=doc)
    op = seed_resource(admin)
    before_revisions = table("team_revisions")
    drop_env = {**doc, "environments": {"qa": {}}}
    removed = admin.put("finance", drop_env, 1)
    assert removed.status_code == 422
    codes = {f["code"] for f in removed.json()["error"]["detail"]["findings"]}
    assert "env_has_resources" in codes
    moved = admin.put("finance", team(subscription_id="sub-b"), 1)
    assert moved.status_code == 422
    codes = {f["code"] for f in moved.json()["error"]["detail"]["findings"]}
    assert "placement_change_with_resources" in codes
    assert "sub-b" not in moved.text and "sub-a" not in moved.text
    assert table("team_revisions") == before_revisions
    assert admin.get("/admin/teams/finance").json()["revision"] == 1
    assert events(outcome="refused").count(("local", "team.update finance", "refused")) == 2
    assert not [e for e in events(outcome="accepted") if "r2" in e[1]]
    # Same placement, other settings: fine. Validate reports the same impact without writing.
    check = admin.call(
        "POST", "/admin/teams/validate", json={"name": "finance", "team": drop_env}
    ).json()
    assert check["mode"] == "update" and check["valid"] is False and check["current_revision"] == 1
    assert check["impact"]["resources"]["dev"] == 1
    assert check["errors"] >= 1 and table("team_revisions") == before_revisions
    assert op["resource_id"]


def test_cloud_target_change_with_resources_is_refused(admin, pattern_repo, recorded_dispatcher):
    cloud_pattern(pattern_repo)
    azure = {"azure": {"subscription_id": TARGET_ID, "region": "r1"}}
    admin.create(doc=team(patterns=("demo",), targets=azure))
    body = {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": "dev",
        "inputs": {"filename": "a.txt", "content": "c"},
    }
    admin.call("POST", "/operations", json=body, groups=FIN, headers={"Idempotency-Key": "c1"})
    moved = {"azure": {"subscription_id": TARGET_ID, "region": "r2"}}
    refused = admin.put("finance", team(patterns=("demo",), targets=moved), 1)
    assert refused.status_code == 422
    findings = refused.json()["error"]["detail"]["findings"]
    assert "placement_change_with_resources" in {f["code"] for f in findings}
    assert TARGET_ID not in refused.text


def test_budget_below_reserved_warns_with_numbers(admin, pattern_repo, recorded_dispatcher):
    source = pattern_repo / "main.tf"
    source.write_text(source.read_text() + '\nvariable "cost_center" { type = string }\n')
    (pattern_repo / "config.yaml").write_text("estimated_costs: 60\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "cost")
    git(pattern_repo, "tag", "v1.2.0")
    doc = {**team(patterns=("demo",), budget_monthly=100), "inject": {"cost_center": "c"}}
    admin.create(doc=doc)
    body = {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": "dev",
        "inputs": {"filename": "a.txt", "content": "c"},
    }
    accepted = admin.call(
        "POST", "/operations", json=body, groups=FIN, headers={"Idempotency-Key": "b1"}
    )
    assert accepted.status_code == 202
    lowered = {**doc, "environments": {"dev": {"budget_monthly": 50}}}
    report = admin.call("POST", "/admin/teams/validate", json={"name": "finance", "team": lowered})
    data = report.json()
    (finding,) = [f for f in data["findings"] if f["code"] == "budget_below_reserved"]
    assert (
        finding["level"] == "warning" and "50" in finding["message"] and "60" in finding["message"]
    )
    assert data["impact"]["budgets"]["dev"] == {
        "reserved": 60.0,
        "current_budget": 100,
        "new_budget": 50,
    }
    assert data["valid"] is True
    listing = admin.get("/admin/teams").json()["items"][0]
    assert listing["budgets"]["dev"] == {"reserved": 60.0, "monthly_budget": 100}
    assert listing["resources"] == 1
    blocked = admin.put("finance", lowered, 1)
    assert blocked.status_code == 409
    assert "budget_below_reserved" in blocked.json()["error"]["detail"]["warning_codes"]
    assert admin.put("finance", lowered, 1, ack=["budget_below_reserved"]).status_code == 200


# --- history ---------------------------------------------------------------------------------


def test_revisions_are_append_only_with_diffs_and_paging(admin):
    admin.create(doc=team(groups=(FIN,), budget_monthly=10))
    admin.put("finance", team(groups=(FIN,), budget_monthly=20), 1, reason="raise")
    admin.put("finance", team(groups=(FIN, HR), budget_monthly=20), 2, reason="add hr")
    page = admin.get("/admin/teams/finance/revisions?limit=2").json()
    assert [r["revision"] for r in page["items"]] == [3, 2] and page["next_before"] == 2
    rest = admin.get("/admin/teams/finance/revisions?limit=2&before=2").json()
    assert [r["revision"] for r in rest["items"]] == [1] and rest["next_before"] is None
    first = admin.get("/admin/teams/finance/revisions/1").json()
    assert first["action"] == "create" and first["reason"] == "onboard"
    assert "environments.dev.budget_monthly" in first["change"]["added"]
    assert first["team"]["environments"]["dev"]["budget_monthly"] == 10
    second = admin.get("/admin/teams/finance/revisions/2").json()
    assert second["change"] == {
        "added": [],
        "removed": [],
        "changed": ["environments.dev.budget_monthly"],
    }
    assert second["actor"] == "local" and second["action"] == "update"
    third = admin.get("/admin/teams/finance/revisions/3").json()
    assert third["change"]["added"] == [f"groups[{HR}]"]
    assert admin.get("/admin/teams/finance/revisions/9").status_code == 404
    assert admin.get("/admin/teams/nope/revisions").status_code == 404
    with ledger.connect() as con:
        for sql in (
            "UPDATE team_revisions SET reason='x'",
            "DELETE FROM team_revisions",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                con.execute(sql)
    assert table("team_revisions") == 3


def test_revert_creates_a_new_revision_with_the_old_document(admin):
    old = team(groups=(FIN, HR))
    admin.create(doc=old)
    admin.put("finance", team(groups=(FIN, HR), budget_monthly=5), 1)
    reverted = admin.post_with("finance", "revert", {"revision": 1, "reason": "oops"}, 2)
    assert reverted.status_code == 200 and reverted.json()["revision"] == 3
    assert reverted.json()["team"] == old
    entry = admin.get("/admin/teams/finance/revisions/3").json()
    assert entry["action"] == "revert" and entry["reason"] == "oops"
    assert table("team_revisions") == 3
    same = admin.post_with("finance", "revert", {"revision": 1, "reason": "again"}, 3)
    assert same.status_code == 409 and same.json()["error"]["reason"] == "team_unchanged"
    assert (
        admin.post_with("finance", "revert", {"revision": 9, "reason": "r"}, 3).status_code == 404
    )
    # Reverting follows the PUT rules: warnings need acknowledgement.
    admin.put("finance", team(groups=(FIN,)), 3, ack=["access_removed"])
    needs = admin.post_with("finance", "revert", {"revision": 1, "reason": "back"}, 4)
    assert needs.status_code == 200  # adding a group is not a warning
    admin.put("finance", team(groups=(FIN, HR), budget_monthly=1), 5)
    gone = admin.post_with("finance", "revert", {"revision": 4, "reason": "fewer"}, 6)
    assert gone.status_code == 409
    assert gone.json()["error"]["reason"] == "warnings_not_acknowledged"


# --- archive ---------------------------------------------------------------------------------


def run_to_ready(admin, recorded_dispatcher, key="a1"):
    op = admin.intent(key).json()
    assert recorded_dispatcher.run_next()
    planned = admin.get(op["links"]["self"], groups=FIN).json()
    execute = admin.call(
        "POST",
        planned["links"]["execute"],
        json={"plan_digest": planned["plan_digest"]},
        groups=FIN,
    )
    assert execute.status_code == 202
    assert recorded_dispatcher.run_next()
    assert admin.get(op["links"]["self"], groups=FIN).json()["state"] == "succeeded"
    return op


def test_archive_blocks_new_intents_but_destroy_still_works(admin, recorded_dispatcher):
    admin.create()
    op = run_to_ready(admin, recorded_dispatcher)
    refused = admin.post_with("finance", "archive", {"reason": "disband"}, 1)
    assert refused.status_code == 409 and refused.json()["error"]["reason"] == "team_has_resources"
    assert admin.get("/admin/teams/finance").json()["state"] == "active"
    done = admin.post_with("finance", "archive", {"reason": "disband", "force_archive": True}, 1)
    assert done.status_code == 200 and done.json()["state"] == "archived"
    assert done.json()["revision"] == 2 and done.headers["ETag"] == '"2"'
    assert admin.post_with("finance", "archive", {"reason": "r"}, 2).status_code == 409
    # No new intents, updates or validation of new work.
    new = admin.intent("n1", filename="other")
    assert new.status_code in (409, 422)
    fresh = admin.intent("n2")
    assert fresh.status_code == 409 and "archived" in fresh.text
    update = admin.intent("n3", resource_id=op["resource_id"])
    assert update.status_code == 409
    validated = admin.call(
        "POST",
        "/intents/validate",
        json={"pattern": "local-file", "environment": "dev", **LOCAL},
        groups=FIN,
    )
    assert validated.status_code == 409
    # Members still see the resource and the team, and can destroy.
    assert admin.get("/resources", groups=FIN).json()["items"][0]["id"] == op["resource_id"]
    me = admin.get("/agent", groups=FIN).json()["business_units"][0]
    assert me["archived"] is True
    destroy = admin.intent("d1", action="destroy", resource_id=op["resource_id"])
    assert destroy.status_code == 202, destroy.text
    assert recorded_dispatcher.run_next()
    planned = admin.get(destroy.json()["links"]["self"], groups=FIN).json()
    execute = admin.call(
        "POST", planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]},
        groups=FIN,
    )  # fmt: skip
    assert execute.status_code == 202
    assert recorded_dispatcher.run_next()
    assert admin.get(destroy.json()["links"]["self"], groups=FIN).json()["state"] == "succeeded"
    # Now nothing is left: archive needs no force, and unarchive restores new work.
    assert admin.post_with("finance", "unarchive", {"reason": "back"}, 2).status_code == 200
    assert admin.post_with("finance", "unarchive", {"reason": "back"}, 3).status_code == 409
    assert admin.intent("n4").status_code == 202
    entry = admin.get("/admin/teams/finance/revisions/3").json()
    assert entry["action"] == "unarchive" and entry["state"] == "active"
    assert entry["change"] == {"added": [], "removed": [], "changed": []}


def test_archived_teams_stay_in_the_loaded_mapping(admin):
    admin.create()
    admin.post_with("finance", "archive", {"reason": "r"}, 1)
    units = tenants.load()
    assert units["finance"].archived is True
    assert [u.name for u in tenants.units_for(tenants.Caller("c", frozenset({FIN})))] == ["finance"]
    unit = units["finance"]
    with pytest.raises(tenants.TenancyError) as error:
        tenants.place(tenants.Caller("c", frozenset({FIN})), unit, "dev", "local-file")
    assert error.value.status == 409
    placed = tenants.place(
        tenants.Caller("c", frozenset({FIN})), unit, "dev", "local-file", allow_archived=True
    )
    assert placed.environment.name == "dev"


# --- import ----------------------------------------------------------------------------------


def mapping_text(*units):
    class Plain(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    return yaml.dump({"operators": ["ignored"], "business_units": dict(units)}, Dumper=Plain)


def test_import_dry_run_and_apply_are_all_or_nothing(admin):
    good = mapping_text(("finance", team()), ("hr", team(groups=(HR,))))
    dry = admin.call("POST", "/admin/teams/import", json={"yaml": good, "reason": "r"})
    report = dry.json()
    assert dry.status_code == 200 and report["apply"] is False and report["applied"] is False
    assert {t["name"]: t["action"] for t in report["teams"]} == {
        "finance": "create",
        "hr": "create",
    }
    assert "top_level_ignored" in {f["code"] for f in report["findings"]}
    assert table("teams") == 0 and not events()

    # One bad team: nothing written, even though the other is fine.
    broken = mapping_text(("finance", team()), ("hr", team(groups=())))
    refused = admin.call(
        "POST", "/admin/teams/import", json={"yaml": broken, "apply": True, "reason": "r"}
    )
    assert refused.status_code == 422
    assert table("teams") == 0 and table("team_revisions") == 0
    assert not events(outcome="accepted")
    assert ("local", "team.import", "refused") in events(outcome="refused")
    assert (
        admin.call("POST", "/admin/teams/import", json={"yaml": good, "apply": True}).status_code
        == 422
    )  # reason required to apply

    applied = admin.call(
        "POST", "/admin/teams/import", json={"yaml": good, "apply": True, "reason": "load"}
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    assert sorted(t["revision"] for t in applied.json()["teams"]) == [1, 1]
    assert table("teams") == 2
    assert ("local", "team.import finance r1", "accepted") in events()
    assert admin.get("/admin/teams/hr/revisions/1").json()["action"] == "import"

    # Again: unchanged; modified: update; new: create; teams missing from the text stay.
    changed = mapping_text(
        ("finance", team(budget_monthly=5)),
        ("hr", team(groups=(HR,))),
        ("ops", team(groups=("o",))),
    )
    plan = admin.call("POST", "/admin/teams/import", json={"yaml": changed}).json()
    assert {t["name"]: t["action"] for t in plan["teams"]} == {
        "finance": "update",
        "hr": "unchanged",
        "ops": "create",
    }
    assert plan["teams"][0]["impact"] is not None
    only = mapping_text(("hr", team(groups=(HR,))))
    result = admin.call(
        "POST", "/admin/teams/import", json={"yaml": only, "apply": True, "reason": "noop"}
    ).json()
    assert result["applied"] is False and table("teams") == 2


def test_import_applies_impact_checks_and_warning_gate(admin):
    admin.create(doc=team(groups=(FIN, HR)))
    shrink = mapping_text(("finance", team(groups=(FIN,))))
    blocked = admin.call(
        "POST",
        "/admin/teams/import",
        json={"yaml": shrink, "apply": True, "reason": "r", "expected_revisions": {"finance": 1}},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["reason"] == "warnings_not_acknowledged"
    assert admin.get("/admin/teams/finance").json()["revision"] == 1
    ok = admin.call(
        "POST",
        "/admin/teams/import",
        json={
            "yaml": shrink,
            "apply": True,
            "reason": "r",
            "acknowledge_warnings": ["access_removed"],
            "expected_revisions": {"finance": 1},
        },
    )
    assert ok.status_code == 200 and admin.get("/admin/teams/finance").json()["revision"] == 2
    garbage = admin.call("POST", "/admin/teams/import", json={"yaml": "a: [", "apply": False})
    assert garbage.status_code == 200 and garbage.json()["errors"] == 1


def test_validate_writes_nothing_and_judges_create_vs_update(admin):
    created = admin.call("POST", "/admin/teams/validate", json={"name": "finance", "team": team()})
    assert created.json()["mode"] == "create" and created.json()["valid"] is True
    assert created.json()["current_revision"] is None
    bad = admin.call(
        "POST", "/admin/teams/validate", json={"name": "finance", "team": team(groups=())}
    ).json()
    assert bad["valid"] is False and bad["errors"] == 1
    assert table("teams") == 0 and not events()
    assert (
        admin.call("POST", "/admin/teams/validate", json={"name": "BAD", "team": {}}).status_code
        == 422
    )


# --- storage ---------------------------------------------------------------------------------


def test_a_backup_of_the_data_directory_includes_teams(admin, tmp_path):
    admin.create()
    admin.put("finance", team(groups=(FIN, HR)), 1)
    snapshot = tmp_path / "snapshot"
    shutil.copytree(settings.data_dir, snapshot, symlinks=True)
    with sqlite3.connect(snapshot / "operations.sqlite") as con:
        assert con.execute("SELECT name, revision, state FROM teams").fetchall() == [
            ("finance", 2, "active")
        ]
        assert con.execute("SELECT count(*) FROM team_revisions").fetchone()[0] == 2
    settings.data_dir.rename(tmp_path / "original-offline")
    shutil.copytree(snapshot, settings.data_dir, symlinks=True)
    assert tenants.load()["finance"].groups == {FIN, HR}
    assert admin.get("/admin/teams/finance").json()["revision"] == 2


def test_stored_docs_never_contain_operators_and_load_reads_active_and_archived(admin):
    admin.create(doc=team(groups=(FIN,)))
    admin.create("hr", doc=team(groups=(HR,)))
    with ledger.connect(write=False) as con:
        docs = [row["doc"] for row in con.execute("SELECT doc FROM teams")]
    assert all("operators" not in d for d in docs)
    assert set(tenants.load()) == {"finance", "hr"}
    assert tenants.enabled()
    assert catalog.load()  # the catalog stays file-based


# --- security review fixes ---------------------------------------------------------------------


def counts():
    with ledger.connect(write=False) as con:
        refused = con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0]
        return table("operations"), table("resources"), refused


def _archive(**_):
    body = team_admin.TeamArchive(reason="race", force_archive=True)
    team_admin.archive("op", "finance", body, '"1"')


def _drop_group(**_):
    body = team_admin.TeamReplace(
        team=team(groups=(HR,)), reason="race", acknowledge_warnings=["access_removed"]
    )
    team_admin.replace("op", "finance", body, '"1"')


def _move_subscription(**_):
    doc = team()
    doc["environments"]["dev"]["subscription_id"] = "someone-elses-subscription"
    team_admin.replace("op", "finance", team_admin.TeamReplace(team=doc, reason="race"), '"1"')


@pytest.mark.parametrize("edit", [_archive, _drop_group, _move_subscription])
def test_team_edit_between_validation_and_acceptance_is_refused(admin, monkeypatch, edit):
    admin.create()
    real = policy.validate

    def validate_then_edit(intent, caller):
        result = real(intent, caller)
        edit()
        return result

    monkeypatch.setattr(policy, "validate", validate_then_edit)
    before = counts()
    response = admin.intent("race")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["reason"] == "placement_stale"
    after = counts()
    assert after[:2] == before[:2] == (0, 0)  # nothing written
    assert after[2] == before[2] + 1  # audited as refused


def test_placement_fields_are_compared_without_a_revision_stamp(admin):
    admin.create()
    caller = tenants.Caller("c", frozenset({FIN}))
    intent = Intent(pattern="local-file", environment="dev", **LOCAL)
    resolved, budget = policy.validate(intent, caller)
    assert resolved.pop("_team_revision") == 1
    _move_subscription()
    material = canonical_intent(intent)
    with pytest.raises(OperationError) as stale:
        ledger.accept("c", "k", material, resolved, budget)
    assert stale.value.reason == "placement_stale"
    # A fresh validation of the new placement is accepted.
    resolved, budget = policy.validate(intent, caller)
    assert ledger.accept("c", "k2", material, resolved, budget)["state"] == "queued"


def test_caller_who_lost_deploy_rights_is_refused_at_acceptance(admin):
    admin.create()
    intent = Intent(pattern="local-file", environment="dev", **LOCAL)
    resolved, budget = policy.validate(intent, tenants.Caller("c", frozenset({FIN})))
    with pytest.raises(OperationError) as stale:
        ledger.accept("c", "k", canonical_intent(intent), resolved, budget, frozenset({HR}))
    assert stale.value.reason == "placement_stale"
    ok = ledger.accept("c", "k", canonical_intent(intent), resolved, budget, frozenset({FIN}))
    assert ok["state"] == "queued" and "_team_revision" not in ok


def test_execute_refuses_new_work_accepted_after_archive_but_not_older_plans(
    admin, recorded_dispatcher
):
    admin.create()
    old = admin.intent("old").json()
    assert recorded_dispatcher.run_next()
    admin.post_with("finance", "archive", {"reason": "r", "force_archive": True}, 1)

    def execute(op):
        planned = admin.get(op["links"]["self"], groups=FIN).json()
        return admin.call(
            "POST", planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]},
            groups=FIN,
        )  # fmt: skip

    # Planned before the archive: still executes.
    assert execute(old).status_code == 202
    assert recorded_dispatcher.run_next()
    # Same shape of operation, but stamped as accepted after the archive: refused.
    admin.post_with("finance", "unarchive", {"reason": "r"}, 2)
    late = admin.intent("late").json()
    assert recorded_dispatcher.run_next()
    admin.post_with("finance", "archive", {"reason": "r", "force_archive": True}, 3)
    with ledger.connect() as con:
        body = json.loads(
            con.execute("SELECT body FROM operations WHERE id=?", (late["id"],)).fetchone()[0]
        )
        body["created_at"] = "9999-01-01T00:00:00+00:00"
        con.execute("UPDATE operations SET body=? WHERE id=?", (json.dumps(body), late["id"]))
    refused = execute(late)
    assert refused.status_code == 409
    assert refused.json()["error"]["reason"] == "team_archived"


@pytest.mark.parametrize("name", ["finance\n", "new\n", "ops\n"])
def test_trailing_newline_names_are_refused_in_import(admin, name):
    quoted = json.dumps(name)  # a YAML double-quoted key keeps the real newline
    text = f"business_units:\n  {quoted}:\n    groups: [g1]\n"
    report = admin.call("POST", "/admin/teams/import", json={"yaml": text}).json()
    assert report["teams"] == [] and "name_invalid" in {f["code"] for f in report["findings"]}
    done = admin.call(
        "POST", "/admin/teams/import", json={"yaml": text, "apply": True, "reason": "r"}
    )
    assert done.status_code == 422 and table("teams") == 0
    assert not team_check.valid_name(name)


def test_yaml_alias_bomb_is_refused_quickly(admin):
    levels = ["a: &a0 [x, x, x, x, x, x, x, x, x]"]
    levels += [
        f"b{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 9) + "]" for i in range(1, 9)
    ]
    bomb = "\n".join(levels) + "\nbusiness_units: {}\n"
    started = time.monotonic()
    for apply in (False, True):
        response = admin.call(
            "POST", "/admin/teams/import", json={"yaml": bomb, "apply": apply, "reason": "r"}
        )
        assert "yaml_invalid" in response.text, response.text
        assert response.status_code == (422 if apply else 200)
    assert time.monotonic() - started < 5
    assert table("teams") == 0


def test_oversized_yaml_document_is_refused():
    with pytest.raises(ValueError):
        team_check.load_yaml("k: " + json.dumps("x" * 1_100_000))
    big = "k: " + json.dumps("x" * 1_100_000)
    assert "yaml_invalid" in {f.code for f in team_check.parse_mapping(big)[1]}


def test_import_apply_needs_the_revisions_it_was_planned_against(admin):
    text = mapping_text(("finance", team(budget_monthly=5)), ("hr", team(groups=(HR,))))
    admin.create()
    dry = admin.call("POST", "/admin/teams/import", json={"yaml": text}).json()
    revisions = {t["name"]: t["current_revision"] for t in dry["teams"]}
    assert revisions == {"finance": 1, "hr": None}

    def apply(**extra):
        return admin.call(
            "POST",
            "/admin/teams/import",
            json={"yaml": text, "apply": True, "reason": "r", **extra},
        )

    missing = apply()  # finance exists, no revision sent
    assert missing.status_code == 428
    assert missing.json()["error"]["reason"] == "expected_revisions_required"
    # A concurrent edit lands after the dry run.
    admin.put("finance", team(groups=(FIN, HR)), 1)
    stale = apply(expected_revisions=revisions)
    assert stale.status_code == 412 and stale.json()["error"]["reason"] == "revision_stale"
    assert table("teams") == 1 and admin.get("/admin/teams/finance").json()["revision"] == 2
    fresh = admin.call("POST", "/admin/teams/import", json={"yaml": text}).json()
    revisions = {t["name"]: t["current_revision"] for t in fresh["teams"]}
    ok = apply(expected_revisions=revisions, acknowledge_warnings=["access_removed"])
    assert ok.status_code == 200, ok.text
    assert admin.get("/admin/teams/finance").json()["revision"] == 3
    # Create-only imports need no revisions.
    brand_new = mapping_text(("ops", team(groups=("o",))))
    created = admin.call(
        "POST", "/admin/teams/import", json={"yaml": brand_new, "apply": True, "reason": "r"}
    )
    assert created.status_code == 200 and created.json()["applied"] is True


def test_team_warnings_use_only_the_pinned_version_and_survive_concurrent_checks(monkeypatch):
    good_key, bad_key = ("p", "c-good", "", None), ("p", "c-bad", "", None)
    monkeypatch.setattr(policy, "_check_cache", {good_key: [], bad_key: [{"level": "error"}]})
    monkeypatch.setattr(policy, "_default_key", {"p": good_key})
    assert team_admin._contract_findings() == {"p": []}  # the old failing tag is ignored
    policy._default_key["p"] = bad_key
    assert team_admin._contract_findings() == {"p": [{"level": "error"}]}

    stop = threading.Event()

    def churn():
        i = 0
        while not stop.is_set():
            policy._check_cache[("q", f"c{i}", "", None)] = []
            policy._check_cache.pop(("q", f"c{i - 5}", "", None), None)
            i += 1

    thread = threading.Thread(target=churn)
    thread.start()
    try:
        for _ in range(2000):
            team_admin._contract_findings()
    finally:
        stop.set()
        thread.join()
