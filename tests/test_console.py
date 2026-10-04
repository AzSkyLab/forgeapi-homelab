"""The built-in web console: static, dependency-free, same-origin; adds no data endpoints."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.settings import settings

CONSOLE = Path(__file__).resolve().parent.parent / "app" / "console"
PAGES = {
    "/console": "text/html; charset=utf-8",
    "/console/console.js": "text/javascript; charset=utf-8",
    "/console/console.css": "text/css; charset=utf-8",
}
FILES = {
    "/console": "index.html",
    "/console/console.js": "console.js",
    "/console/console.css": "console.css",
}
SECURITY_HEADERS = {
    "content-security-policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


@pytest.fixture
def client(recorded_dispatcher):
    return TestClient(app)


@pytest.mark.parametrize("path,media_type", PAGES.items())
def test_console_assets_served_with_strict_headers(client, path, media_type):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value
    assert response.content == (CONSOLE / FILES[path]).read_bytes()


def test_console_static_files_need_no_auth_but_data_still_does(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "entra")
    monkeypatch.setattr(settings, "entra_tenant_id", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(settings, "entra_audience", "api://forgeapi-test")
    for path in PAGES:
        assert client.get(path).status_code == 200
    for path in ("/agent", "/operations", "/resources", "/patterns"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.headers["www-authenticate"] == "Bearer"


def test_console_capability_advertised(client):
    for prefix in ("", "/v1"):
        assert client.get(f"{prefix}/agent").json()["capabilities"]["web_console"] is True


def test_console_routes_are_not_in_openapi(client):
    for document in ("/openapi.json", "/v1/openapi.json"):
        assert not [p for p in client.get(document).json()["paths"] if "console" in p]


def _route_templates(client):
    return set(client.get("/openapi.json").json()["paths"])


def _template_regex(template):
    parts = re.split(r"\{[^}]+\}", template)
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "$")


def test_console_js_calls_only_existing_routes(client):
    source = (CONSOLE / "console.js").read_text()
    literals = re.findall(r"""["'`](/[A-Za-z][^"'`\s]*)["'`]""", source)
    assert literals, "console.js should reference API paths"
    matchers = [_template_regex(t) for t in _route_templates(client)]
    for literal in literals:
        path = re.sub(r"\$\{[^}]*\}", "x", literal.split("?")[0])
        assert any(m.match(path) for m in matchers), f"{literal} is not an app route"
    assert {"/agent", "/operations", "/resources", "/patterns"} <= set(literals)
    assert not any(lit.startswith("/console") for lit in literals)


def test_console_js_never_builds_markup_from_data():
    source = (CONSOLE / "console.js").read_text()
    for banned in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "localStorage",
        "sessionStorage",
        "document.cookie",
        "window.confirm",
        "location.search",
        "XMLHttpRequest",
    ):
        assert banned not in source, banned
    assert "new Function" not in source
    # The SVG namespace is an identifier, never fetched; it is the only URL allowed.
    assert re.search(r"https?://", source.replace('"http://www.w3.org/2000/svg"', "")) is None
    assert source.count("http://www.w3.org/2000/svg") <= 1


def test_console_html_has_no_inline_script_or_style():
    html = (CONSOLE / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)
    assert not re.search(r"<script[^>]*>(?!</script>)", html)
    assert "<style" not in html
    assert not re.search(r"\sstyle\s*=", html)
    assert not re.search(r"\son[a-z]+\s*=", html)
    assert re.search(r"https?://", html) is None
    assert "@import" not in (CONSOLE / "console.css").read_text()
    assert "url(http" not in (CONSOLE / "console.css").read_text()


def test_console_js_has_portal_views_and_fields():
    source = (CONSOLE / "console.js").read_text()
    routes = ("home", "infrastructure", "zones", "jobs", "history", "catalog")
    routes += ("resources", "operations", "apps")
    for route in routes:
        assert re.search(rf"""["']{route}["']""", source), route
    for field in (
        "owned_by_caller",
        "managed_objects",
        "clouds",
        "estimated_monthly_cost",
        "input_refs",
        "created_at",
        "business_units",
        "guardrails",
        "withheld_outputs",
    ):
        assert field in source, field
    html = (CONSOLE / "index.html").read_text()
    for route in ("home", "infrastructure", "apps", "zones", "jobs", "history", "catalog"):
        assert f'href="#{route}"' in html, route


def test_console_apps_view_follows_label_convention():
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        "app_role",
        "app_fqdn",
        "primary_endpoint",
        "secondary_endpoint",
        "labels.app",
        '"#apps/"',
        "app=<name>",
        "app_role=replica|router",
        "replicas ready",
        "PRIMARY",
        "SECONDARY",
        "healthy",
        "degraded",
        "To fail over, submit an update to the router",
        "viewApp",
    ):
        assert needle in source, needle
    assert "st-healthy" in (CONSOLE / "console.css").read_text()


def test_console_degrades_and_respects_motion_and_width():
    css = (CONSOLE / "console.css").read_text()
    assert "prefers-reduced-motion" in css
    assert "prefers-color-scheme: dark" in css
    assert "max-width: 480px" in css
    source = (CONSOLE / "console.js").read_text()
    assert "Array.isArray(r.managed_objects)" in source
    assert "textContent" in source or "createTextNode" in source


def test_console_rollouts_view_shows_and_drives_app_rollouts():
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        "viewRollout",
        '"rollouts"',
        '"#rollouts/"',
        '"/apps"',
        "planning_replicas",
        "awaiting_replica_approval",
        "applying_replicas",
        "planning_router",
        "awaiting_router_approval",
        "applying_router",
        "Replicas planned",
        "Approve replicas",
        "Replicas applying",
        "Router planned",
        "Approve router",
        "Ready",
        "plan_digests",
        "plan_digest",
        "links.approve",
        "operation_id",
        "change_summary",
        "destructive",
        "next_action",
        "poll_after_seconds",
        "Rollouts in progress",
        "Review and approve",
        "Confirm approve",
        "Approval refused",
        "notice",
    ):
        assert needle in source, needle
    # the approve call goes to the app's own link, never a hand-built path
    assert "safePath(ro.links.approve)" in source
    css = (CONSOLE / "console.css").read_text()
    for needle in (".stepper", ".step.current", ".step.gate", ".approve"):
        assert needle in css, needle


def test_console_rollout_paths_exist_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/apps"] and "post" in paths["/apps"]
    assert "get" in paths["/apps/{app_id}"]
    assert "post" in paths["/apps/{app_id}/approve"]
    source = (CONSOLE / "console.js").read_text()
    assert "/apps/${enc(id)}" in source
    assert "/approve" not in source


def test_console_failover_and_teardown_controls():
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        "failoverPanel",
        "teardownPanel",
        "Fail over",
        "Make replica ",
        "Confirm failover",
        "Failover plan: review and approve below.",
        "Tear down app",
        "Confirm tear down",
        'id: "destroy-confirm"',
        "input.value !== ro.name",
        "ro.primary",
        "ro.teardown",
        "teardown",
        "PRIMARY",
        "SECONDARY",
        "Idempotency-Key",
        "crypto.randomUUID",
        "actionKey(",
        "primary: index",
        "Router destroy planned",
        "Approve router destroy",
        "Router destroying",
        "Replica destroys planned",
        "Approve replica destroys",
        "Replicas destroying",
        "Destroyed",
        "Approve the router destroy",
        "Approve the replica destroys",
        "Destructive: this deletes real infrastructure",
        "Failover refused",
        "Tear down refused",
        "errorBox(flash",
    ):
        assert needle in source, needle
    for state in (
        "planning_router_destroy",
        "awaiting_router_destroy_approval",
        "destroying_router",
        "planning_replica_destroy",
        "awaiting_replica_destroy_approval",
        "destroying_replicas",
        "destroyed",
    ):
        assert state in source, state
    for role in ("router_destroy", "replica_destroy"):
        assert role in source, role
    # the new calls are built from the app's own approve link, never a hand-written path
    assert 'sibling(ro.links.approve, "failover")' in source
    assert 'sibling(ro.links.approve, "destroy")' in source
    assert "/failover" not in source and "/destroy" not in source
    # a destroyed app is not "in progress" on Home
    assert re.search(r'ROLLOUT_DONE = \[[^\]]*"destroyed"', source)
    css = (CONSOLE / "console.css").read_text()
    for needle in (".danger-zone", ".failover", ".destroyed"):
        assert needle in css, needle


def test_console_failover_destroy_routes_exist_in_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    failover = paths["/apps/{app_id}/failover"]["post"]
    destroy = paths["/apps/{app_id}/destroy"]["post"]
    for operation in (failover, destroy):
        names = [p["name"].lower() for p in operation.get("parameters", [])]
        assert "idempotency-key" in names
    assert "requestBody" in failover
    assert "requestBody" not in destroy
    states = client.get("/openapi.json").json()["components"]["schemas"]["App"]["properties"]
    assert "primary" in states and "teardown" in states


def test_console_fleet_view_routes_and_fields():
    source = (CONSOLE / "console.js").read_text()
    html = (CONSOLE / "index.html").read_text()
    assert 'href="#fleet"' in html and 'data-route="fleet"' in html
    assert re.search(r"""["']fleet["']""", source)
    for needle in (
        "viewFleet",
        "drift_status",
        "drift_checked_at",
        "latest_version",
        "upgrade_available",
        "Resources checked",
        "Upgrades available",
        "Never checked",
        "Last check",
        "Check again",
        "Check now",
        "Check all visible (",
        "Plan upgrade",
        "Fleet health",
        "Check drift now",
        "drift_check",
        "include_checks",
        "drift_checks",
    ):
        assert needle in source, needle
    css = (CONSOLE / "console.css").read_text()
    for needle in (".st-drifted", ".st-in_sync", ".st-upgrade", ".ind"):
        assert needle in css, needle


def test_console_drift_check_uses_idempotency_key_and_existing_routes(client):
    paths = client.get("/openapi.json").json()["paths"]
    post = paths["/resources/{resource_id}/drift-check"]["post"]
    assert "idempotency-key" in [p["name"].lower() for p in post.get("parameters", [])]
    source = (CONSOLE / "console.js").read_text()
    assert "/resources/${enc(r.id)}/drift-check" in source
    # one key per resource, dropped after each call so a later click is a new check
    assert 'actionKey("drift:" + r.id)' in source or 'actionKey(name)' in source
    assert 'const name = "drift:" + r.id;' in source
    assert "actionKeys.delete(name)" in source
    # upgrades go through the existing POST /operations with an Idempotency-Key
    assert 'capOn(agentV, "resource_upgrade")' in source
    assert 'safePath(r.links.self) + "/" + "upgrade"' in source
    assert "body: { version: r.latest_version }" in source
    assert 'method: "POST", key: actionKey("upgrade:' not in source
    assert 'api("/operations", { method: "POST"' not in source
    assert "The plan is reviewed before anything is applied." in source


def test_console_drift_check_operation_has_no_apply_or_discard():
    source = (CONSOLE / "console.js").read_text()
    assert 'op.action === "drift_check"' in source
    assert "Drift result" in source
    # execute/discard only ever appear for a planned operation, which a drift check never is
    assert 'if (op.state !== "planned" || !op.links) return null;' in source


def test_console_promotion_is_feature_detected_and_never_a_literal_path(client):
    source = (CONSOLE / "console.js").read_text()
    assert 'capOn(agentV, "promotion")' in source
    assert "promotePanel" in source
    assert "Confirm promote" in source
    assert 'safePath(r.links.self) + "/" + "promote"' in source
    paths = client.get("/openapi.json").json()["paths"]
    literal = '"/resources/{resource_id}/promote"'
    assert literal not in source
    if "/resources/{resource_id}/promote" in paths:
        post = paths["/resources/{resource_id}/promote"]["post"]
        assert "idempotency-key" in [p["name"].lower() for p in post.get("parameters", [])]


def test_console_resource_fields_exist_in_openapi(client):
    props = client.get("/openapi.json").json()["components"]["schemas"]["Resource"]["properties"]
    fields = ("drift_status", "drift_checked_at", "drift", "latest_version", "upgrade_available")
    for field in fields:
        assert field in props, field


def test_console_cost_charts_are_svg_built_with_createelementns(client):
    source = (CONSOLE / "console.js").read_text()
    assert "createElementNS" in source
    assert 'capOn(agent, "cost_history")' in source
    for needle in (
        "viewZone",
        "zoneHref(",
        '"#zones/"',
        "sparkline(",
        "costChart(",
        "ch-budget",
        "monthly_budget",
        "by_pattern",
        "change_over_period",
        "movers",
        "currency_note",
        "Spend trend",
        "7 days",
        "90 days",
        "ArrowLeft",
        "/budgets/history",
    ):
        assert needle in source, needle
    paths = client.get("/openapi.json").json()["paths"]
    assert "get" in paths["/budgets/history"]
    params = [p["name"] for p in paths["/budgets/history"]["get"]["parameters"]]
    assert {"business_unit", "environment", "days"} <= set(params)
    css = (CONSOLE / "console.css").read_text()
    for needle in (".ch-line", ".ch-area", ".ch-budget", "var(--accent)", "var(--warn)"):
        assert needle in css, needle
    assert ".delta.up" in css
    # drawing never uses markup strings; the namespace is not a fetched URL
    assert "innerHTML" not in source
    assert "createElement(\"svg\")" not in source


def test_console_pattern_changes_panel_and_deployable_patterns(client):
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        'capOn(agentV, "pattern_changes")',
        'capOn(agentR, "pattern_changes")',
        'capOn(agent, "deployable_patterns")',
        "deployable_patterns",
        "new_required_inputs",
        "New required inputs: ",
        "cannot supply",
        "from_commit",
        "to_commit",
        "What changes",
        "From version",
        "To version",
        "changesBlock(",
        "loadChanges(",
    ):
        assert needle in source, needle
    assert "/patterns/${enc(o.name)}/changes" in source
    paths = client.get("/openapi.json").json()["paths"]
    changes = paths["/patterns/{name}/changes"]["get"]
    names = {p["name"] for p in changes["parameters"]}
    assert {"from", "to", "business_unit", "environment"} <= names
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert any("deployable_patterns" in s.get("properties", {}) for s in schemas.values())
    css = (CONSOLE / "console.css").read_text()
    assert ".warn-box" in css
    caps = client.get("/agent").json()["capabilities"]
    assert caps["cost_history"] and caps["pattern_changes"] and caps["deployable_patterns"]


def test_console_pattern_contract_panel_badges_and_chip(client):
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        'capOn(agentR, "pattern_checks")',
        'capOn(agentV, "pattern_checks")',
        'capOn(agent, "pattern_checks")',
        "contractPanel(",
        "fillContractBadges(",
        "addContractChip(",
        "loadCheck(",
        "checkMemo",
        "CHECK_BADGE_MAX = 20",
        "findings",
        "errors",
        "warnings",
        "docs/pattern-onboarding.md",
        "contract-chip",
        '"#catalog/"',
    ):
        assert needle in source, needle
    assert "/patterns/${enc(name)}/check" in source
    paths = client.get("/openapi.json").json()["paths"]
    check = paths["/patterns/{name}/check"]["get"]
    assert "version" in {p["name"] for p in check["parameters"]}
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    check_props = set(schemas["PatternCheck"]["properties"])
    finding_props = set(schemas["PatternFinding"]["properties"])
    assert {"findings", "errors", "warnings", "commit"} <= check_props
    assert {"level", "code", "message", "file", "line"} <= finding_props
    assert client.get("/agent").json()["capabilities"]["pattern_checks"] is True
    css = (CONSOLE / "console.css").read_text()
    for needle in (".findings", ".finding.lv-error", ".finding.lv-warning", ".contract-chip"):
        assert needle in css, needle


ADMIN_LITERAL = re.compile(r"""["'`](/admin/[^"'`\s]*)["'`]""")


def test_console_teams_view_is_operator_gated_and_has_no_static_nav(client):
    source = (CONSOLE / "console.js").read_text()
    html = (CONSOLE / "index.html").read_text()
    # the nav item is created only for operators; it is never hard-coded in the page
    assert 'data-route="teams"' not in html and 'href="#teams' not in html
    for needle in (
        "isOperator(",
        'capOn(agent, "team_admin")',
        "agent.caller.operator === true",
        "syncTeamsNav",
        'data-route": "teams"',
        "Operators only",
        "e.status === 403",
        "viewTeams",
        '"teams"',
        '"#teams/new"',
        '"#teams/import"',
        "#teams/${enc(name)}/edit",
        "#teams/${enc(name)}/history",
    ):
        assert needle in source, needle
    # discovery advertises what the gate reads
    agent = client.get("/agent").json()
    assert agent["capabilities"]["team_admin"] is True
    assert isinstance(agent["caller"]["operator"], bool)


def test_console_teams_admin_paths_exist_in_openapi(client):
    source = (CONSOLE / "console.js").read_text()
    literals = set(ADMIN_LITERAL.findall(source))
    paths = client.get("/openapi.json").json()["paths"]
    matchers = {t: _template_regex(t) for t in paths}
    used = set()
    for literal in literals:
        path = re.sub(r"\$\{[^}]*\}", "x", literal.split("?")[0])
        hits = [t for t, m in matchers.items() if m.match(path)]
        assert hits, f"{literal} is not an app route"
        used.update(hits)
    expected = {
        "/admin/teams",
        "/admin/teams/validate",
        "/admin/teams/import",
        "/admin/teams/{name}",
        "/admin/teams/{name}/revisions",
        "/admin/teams/{name}/revisions/{revision}",
        "/admin/teams/{name}/revert",
        "/admin/teams/{name}/archive",
        "/admin/teams/{name}/unarchive",
    }
    assert expected <= used
    assert {"get", "post"} <= set(paths["/admin/teams"])
    assert "put" in paths["/admin/teams/{name}"] and "get" in paths["/admin/teams/{name}"]
    for path in (
        "/admin/teams/{name}",
        "/admin/teams/{name}/revert",
        "/admin/teams/{name}/archive",
        "/admin/teams/{name}/unarchive",
    ):
        write = paths[path].get("put") or paths[path]["post"]
        assert "if-match" in [p["name"].lower() for p in write.get("parameters", [])], path


def test_console_teams_writes_use_if_match_and_acknowledgements():
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        'headers["If-Match"] = o.ifMatch',
        "withHeaders: true",
        'res.headers.get("ETag")',
        "ifMatch: etag",
        "acknowledge_warnings",
        "warnings_not_acknowledged",
        "ackPanel(",
        "allTicked(",
        "p.status === 412",
        "This team changed since you opened it",
        "Reload the latest",
        "Keep my edits on top of it",
        "force_archive",
        "team_has_resources",
        "Force archive",
        "Confirm archive",
        "Confirm revert",
        "Confirm apply",
        "tenants_source_file",
        "FORGEAPI_TENANTS_SOURCE=db",
        "Import from YAML",
        "Dry run",
        'id: "import-yaml"',
        "/admin/teams/validate",
    ):
        assert needle in source, needle
    # every write goes through api() with an explicit method, and PUT/revert/archive carry the ETag
    for call in ('method: "PUT", ifMatch: etag', "/revert`, { method: \"POST\", ifMatch: etag"):
        assert call in source, call
    assert source.count("ifMatch: etag") >= 4


def test_console_teams_wizard_editor_and_history_features():
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        "TSTEPS",
        "Landing zones",
        "Regions and patterns",
        "Platform inputs",
        "subscription_id",
        "aws_account_id",
        "project_id",
        "protected_resource_types",
        "allow_destroy",
        "budget_monthly",
        "network",
        "inject",
        "regions",
        "Live checks",
        "Impact preview",
        "would be affected",
        "budgetBar(",
        "findingsList(",
        "jumpTo(",
        "diffView(",
        "teamYaml(",
        "navigator.clipboard.writeText",
        "execCommand",
        "Generated YAML",
        "pattern_checks",
        "Can place in",
        "cached(\"catalog-list\"",
        "next_before",
        "Revert to this revision",
        "Onboard a team",
        "setTimeout(runValidate",
        "goStep(",
        "tstep-h",
        "tabindex",
    ):
        assert needle in source, needle
    # identifiers stay in memory: never stored, logged or put in the URL
    for banned in ("console.log", "console.error", "indexedDB", "location.search"):
        assert banned not in source, banned


def test_console_teams_response_fields_exist_in_openapi(client):
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    def props(name):
        return set(schemas[name]["properties"])

    assert {"items", "source"} <= props("TeamList")
    assert {"name", "state", "groups", "environments", "resources", "budgets", "findings"} <= props(
        "TeamSummary"
    )
    assert {"revision", "source", "team", "updated_by", "updated_at", "created_by"} <= props(
        "TeamView"
    )
    assert {"valid", "mode", "findings", "errors", "warnings", "impact"} <= props(
        "ValidationReport"
    )
    assert {"resources", "budgets"} <= props("Impact")
    assert {"reserved", "current_budget", "new_budget"} <= props("BudgetImpact")
    assert {"added", "removed", "changed"} <= props("Diff")
    assert {"items", "next_before"} <= props("RevisionPage")
    assert {"revision", "actor", "at", "reason", "action", "change"} <= props("RevisionSummary")
    assert {"applied", "teams", "findings"} <= props("ImportReport")
    assert {"action", "findings", "impact", "revision"} <= props("ImportEntry")
    source = (CONSOLE / "console.js").read_text()
    for field in ("monthly_budget", "current_budget", "new_budget", "next_before", "updated_by"):
        assert field in source, field


def test_console_teams_css_covers_the_new_screens():
    css = (CONSOLE / "console.css").read_text()
    for needle in (
        ".tlayout",
        ".tchecks",
        ".cloud-card",
        ".diff-add",
        ".diff-rem",
        ".diff-chg",
        ".ack",
        ".st-active",
        ".st-archived",
        ".bbar",
        ".kv-row",
        ".idrow",
        ".step-btn",
    ):
        assert needle in css, needle
    assert css.count("max-width: 480px") >= 2


def test_console_deploy_view_is_routed_and_linked():
    source = (CONSOLE / "console.js").read_text()
    html = (CONSOLE / "index.html").read_text()
    assert 'href="#deploy"' in html and 'data-route="deploy"' in html
    assert re.search(r"""["']deploy["']""", source)
    for needle in (
        "viewDeploy",
        "startDeploy(",
        "Deploy here",
        "Deploy this pattern",
        "DSTEPS",
        "Where do you want it?",
        "What do you want to deploy?",
        "Configure it",
        "Review and submit",
        "tstep-h",
        "radiogroup",
    ):
        assert needle in source, needle
    # the draft never travels in the URL: the only deploy route is the bare #deploy
    assert '"#deploy?' not in source and '"#deploy/' not in source
    assert source.count('"#deploy"') == 2  # the same-page check and the navigation, both bare


def test_console_deploy_uses_validate_and_idempotent_submit(client):
    source = (CONSOLE / "console.js").read_text()
    paths = client.get("/openapi.json").json()["paths"]
    validate = paths["/intents/validate"]["post"]
    submit = paths["/operations"]["post"]
    assert "idempotency-key" in [p["name"].lower() for p in submit.get("parameters", [])]
    assert "idempotency-key" not in [p["name"].lower() for p in validate.get("parameters", [])]
    describe = paths["/patterns/{name}"]["get"]
    described = {p["name"] for p in describe["parameters"]}
    assert {"version", "business_unit", "environment"} <= described
    assert "environment" in {p["name"] for p in paths["/patterns"]["get"]["parameters"]}
    assert '"/intents/validate", { method: "POST", body: req }' in source
    assert 'api("/operations", { key: keyNow, method: "POST", body: sent })' in source
    assert "/patterns/${enc(name)}" in source
    # one key per request body: reused on a retry, fresh when the body changes
    assert "dep.keyed.sig !== " in source and "newKey()" in source
    assert "crypto.randomUUID" in source
    # a successful submit goes to the operation page, where the plan is reviewed
    assert 'location.hash = "#operations/" + enc(op.id)' in source
    assert "Submit and plan" in source
    assert "Nothing is applied. A plan is created and shown for your review." in source
    props = client.get("/openapi.json").json()["components"]["schemas"]["Intent"]["properties"]
    for field in ("pattern", "version", "expected_commit", "business_unit", "environment"):
        assert field in props, field
        assert f"body.{field}" in source or f"{field}:" in source or f'"{field}"' in source, field
    for field in ("size", "inputs", "labels", "input_refs"):
        assert field in props, field
        assert f"body.{field}" in source, field
    assert "action" not in re.findall(r"body\.(\w+) =", source)


def test_console_deploy_form_covers_schema_types_labels_and_refs():
    source = (CONSOLE / "console.js").read_text()
    for needle in (
        "schemaKind(",
        '"bool"',
        '"list"',
        '"map"',
        "chipEditor(",
        "kvEditor(",
        'type: "checkbox"',
        'type: "number"',
        "s.enum",
        "s.pattern",
        "s.minimum",
        "s.maxLength",
        "aria-required",
        "aria-invalid",
        "aria-describedby",
        "clientProblem(",
        "constraintText(",
        "^[a-z][a-z0-9_.-]{0,62}$",
        "labelProblems(",
        "LABEL_SEED",
        "At most 16 labels",
        "over 128 characters",
        "dep.desc.sizes",
        "Named size",
        "Take it from another resource's output",
        'r.state === "ready"',
        "r.business_unit === dep.bu",
        "Object.keys(r.outputs)",
        "resource_id: r.id",
        "problemsOf(",
        "schedule(400)",
        "setTimeout(runValidate",
        "estimated_monthly_cost",
        "budgetImpact(",
        "Over budget",
        "budget_exceeded",
        "revision_moved",
        "Idempotency-Key: ",
        "copyBtn(json",
        "dep.preset",
        "fillContractBadges(",
        'capOn(agent, "pattern_checks")',
        "deployableNames(",
        "about.description",
        "about.category",
    ):
        assert needle in source, needle
    deploy_code = source.split("async function viewDeploy(")[1].split("/* ---------- chrome")[0]
    assert "withheld_outputs" not in deploy_code


def test_console_deploy_css_and_form_widths():
    css = (CONSOLE / "console.css").read_text()
    needles = (".pick", ".pick.sel", ".ferr", ".fhint", ".req", ".dp-ref", ".dp-json")
    for needle in needles + (".tlayout.solo",):
        assert needle in css, needle
    assert css.count("max-width: 480px") >= 3
    assert "overflow-x: hidden" in css
