"""Environment promotion: the same pattern commit deployed as a new resource in another environment.

Real local Terraform through the recorded dispatcher; two environments with different placement."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.conftest import git
from tests.test_operations import agent_client as agent_client
from tests.test_resource_labels import ledger_count, run_to_ready


@pytest.fixture
def promo(tmp_path, monkeypatch, pattern_repo, recorded_dispatcher):
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + '\nvariable "cost_center" { type = string }\n'
        + 'variable "subnet" {\n  type    = string\n  default = ""\n}\n'
    )
    (pattern_repo / "config.yaml").write_text("estimated_costs: 60\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "placement")
    git(pattern_repo, "tag", "v1.2.0")
    (pattern_repo / "README").write_text("next\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "next")
    git(pattern_repo, "tag", "v1.3.0")
    units = {
        "finance": {
            "groups": ["finance"],
            "inject": {"cost_center": "finance"},
            "patterns": ["demo"],
            "environments": {
                "dev": {
                    "subscription_id": "hidden-dev",
                    "budget_monthly": 1000,
                    "network": {"subnet": "dev-subnet"},
                },
                "prod": {
                    "subscription_id": "hidden-prod",
                    "budget_monthly": 130,
                    "network": {"subnet": "prod-subnet"},
                },
                "locked": {"subscription_id": "hidden-locked", "groups": ["nobody"]},
            },
        }
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    monkeypatch.setattr(settings, "dev_groups", "finance")
    return TestClient(app)


def deploy(client, dispatcher, key="dev-1", env="dev", **updates):
    body = {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": env,
        "inputs": {"filename": f"{key}.txt", "content": "from dev"},
        **updates,
    }
    op = client.post("/operations", json=body, headers={"Idempotency-Key": key}).json()
    run_to_ready(client, dispatcher, op)
    return op["resource_id"]


def promote(client, rid, key="p-1", prefix="", **body):
    body.setdefault("environment", "prod")
    return client.post(
        f"{prefix}/resources/{rid}/promote", json=body, headers={"Idempotency-Key": key}
    )


def tfvars(resource_id):
    work = settings.data_dir / "deployments" / resource_id / "work"
    return json.loads((work / "terraform.tfvars.json").read_text())


def counts(*outcomes):
    with ledger.connect() as con:
        return [
            con.execute("SELECT count(*) FROM events WHERE outcome=?", (o,)).fetchone()[0]
            for o in outcomes
        ]


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_promote_plans_prod_with_prod_placement_then_applies(promo, recorded_dispatcher, prefix):
    rid = deploy(promo, recorded_dispatcher, labels={"team": "a"})
    dev = promo.get(f"/resources/{rid}").json()
    response = promote(promo, rid, prefix=prefix, labels={"tier": "gold"})
    assert response.status_code == 202, response.text
    op = response.json()
    assert op["action"] == "deploy" and op["resource_id"] != rid
    assert op["commit"] == dev["commit"] and op["version"] == "v1.2.0"
    assert response.headers["location"] == op["links"]["self"]

    stored = ledger.get(op["id"])
    assert stored["environment"] == "prod" and stored["subscription_id"] == "hidden-prod"
    assert stored["injected"]["subnet"] == "prod-subnet"
    assert stored["promoted_from"] == rid
    assert "hidden-prod" not in response.text and "prod-subnet" not in response.text

    assert recorded_dispatcher.run_next()
    planned = promo.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"  # never auto-applied
    promo.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()

    new = promo.get(f"{prefix}/resources/{op['resource_id']}").json()
    assert new["state"] == "ready" and new["environment"] == "prod"
    assert new["commit"] == dev["commit"]
    assert new["promoted_from"] == rid
    assert new["labels"] == {"team": "a", "tier": "gold", "promoted_from": rid}
    values = tfvars(op["resource_id"])
    assert values["filename"] == "dev-1.txt" and values["content"] == "from dev"
    assert values["subnet"] == "prod-subnet" and values["cost_center"] == "finance"

    after = promo.get(f"/resources/{rid}").json()
    assert after["state"] == "ready" and after["environment"] == "dev"
    assert after["commit"] == dev["commit"] and after["promoted_from"] is None
    assert tfvars(rid)["subnet"] == "dev-subnet"
    assert after["latest_operation_id"] == dev["latest_operation_id"]


def test_overrides_apply_and_injected_override_is_refused(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    op = promote(promo, rid, inputs={"content": "for prod"}).json()
    assert recorded_dispatcher.run_next()
    planned = promo.get(op["links"]["self"]).json()
    promo.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()
    assert tfvars(op["resource_id"])["content"] == "for prod"
    assert tfvars(op["resource_id"])["filename"] == "dev-1.txt"

    before, refused = ledger_count(), counts("refused")
    for name in ("cost_center", "subnet"):
        bad = promote(promo, rid, key=f"bad-{name}", inputs={name: "mine"})
        assert bad.status_code == 422, bad.text
    assert ledger_count() == before
    assert counts("refused") == [refused[0] + 2]


def test_source_refs_must_be_resupplied(promo, recorded_dispatcher):
    base = deploy(promo, recorded_dispatcher, key="base")
    ref = {"content": {"resource_id": base, "output": "content_sha256"}}
    consumer = deploy(
        promo,
        recorded_dispatcher,
        key="cons",
        inputs={"filename": "cons.txt"},
        input_refs=ref,
    )
    prod_base = deploy(promo, recorded_dispatcher, key="pbase", env="prod")
    before, refused = ledger_count(), counts("refused")
    missing = promote(promo, consumer, key="m")
    assert missing.status_code == 422
    error = missing.json()["error"]
    assert error["reason"] == "promotion_needs_refs" and "content" in error["detail"]
    assert ledger_count() == before and counts("refused") == [refused[0] + 1]

    # A ref into the source environment still cannot cross it.
    crossing = promote(promo, consumer, key="x", input_refs=ref)
    assert crossing.status_code == 422 and "environment" in crossing.text

    prod_ref = {"content": {"resource_id": prod_base, "output": "content_sha256"}}
    ok = promote(promo, consumer, key="ok", input_refs=prod_ref)
    assert ok.status_code == 202, ok.text
    op = ok.json()
    assert ledger.get(op["id"])["input_refs"] == prod_ref
    assert promo.get(f"/resources/{op['resource_id']}").json()["input_refs"] == prod_ref
    assert recorded_dispatcher.run_next()
    sha = promo.get(f"/resources/{prod_base}").json()["outputs"]["content_sha256"]
    planned = promo.get(op["links"]["self"]).json()
    promo.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()
    assert tfvars(op["resource_id"])["content"] == sha
    assert tfvars(op["resource_id"])["filename"] == "cons.txt"


def test_same_environment_is_refused(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    before = ledger_count()
    response = promote(promo, rid, environment="dev")
    assert response.status_code == 422 and ledger_count() == before


def test_forbidden_target_is_403_and_unknown_is_not_leaked(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    before, refused = ledger_count(), counts("refused")
    response = promote(promo, rid, environment="locked")
    assert response.status_code == 403, response.text
    assert "hidden-locked" not in response.text
    assert ledger_count() == before and counts("refused") == [refused[0] + 1]


def test_budget_in_target_is_refused(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    deploy(promo, recorded_dispatcher, key="fill", env="prod")
    deploy(promo, recorded_dispatcher, key="fill2", env="prod")  # 120 of 130
    before = ledger_count()
    response = promote(promo, rid)
    assert response.status_code == 403
    assert response.json()["error"]["reason"] == "budget_exceeded"
    assert ledger_count() == before


def test_idempotent_replay_and_reused_key(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    base = counts("accepted")[0]
    first = promote(promo, rid, key="same")
    again = promote(promo, rid, key="same")
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    assert counts("accepted") == [base + 1]
    reused = promote(promo, rid, key="same", inputs={"content": "different"})
    assert reused.status_code == 409
    assert reused.json()["error"]["reason"] == "idempotency_key_reused"
    # Same key and body as a plain deploy intent is a different request.
    plain = promo.post(
        "/operations",
        json={"pattern": "demo", "environment": "prod", "inputs": {"filename": "z.txt"}},
        headers={"Idempotency-Key": "same"},
    )
    assert plain.status_code == 409


def test_source_must_be_ready_and_visible(promo, recorded_dispatcher):
    pending = promo.post(
        "/operations",
        json={
            "pattern": "demo",
            "version": "v1.2.0",
            "environment": "dev",
            "inputs": {"filename": "p.txt", "content": "c"},
        },
        headers={"Idempotency-Key": "pend"},
    ).json()["resource_id"]
    before = ledger_count()
    not_ready = promote(promo, pending, key="nr")
    assert not_ready.status_code == 409
    assert not_ready.json()["error"]["reason"] == "resource_not_ready"
    assert promote(promo, "res_" + "0" * 32, key="missing").status_code == 404
    assert ledger_count() == before

    rid = deploy(promo, recorded_dispatcher, key="vis")
    from app.main import caller_context
    from app.tenants import Caller

    app.dependency_overrides[caller_context] = lambda: Caller("other", frozenset({"stranger"}))
    try:
        assert promote(promo, rid, key="inv").status_code == 404
    finally:
        app.dependency_overrides.pop(caller_context, None)


def test_moved_tag_is_refused_by_the_commit_pin(promo, recorded_dispatcher, pattern_repo):
    rid = deploy(promo, recorded_dispatcher)
    (pattern_repo / "main.tf").write_text((pattern_repo / "main.tf").read_text() + "\n# moved\n")
    git(pattern_repo, "commit", "-qam", "moved")
    git(pattern_repo, "tag", "-f", "v1.2.0")
    from app import catalog

    catalog.load.cache_clear() if hasattr(catalog.load, "cache_clear") else None
    before = ledger_count()
    response = promote(promo, rid)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["reason"] == "revision_moved"
    assert ledger_count() == before


def test_without_tenancy_promotion_has_nowhere_to_go(agent_client, recorded_dispatcher):
    from tests.test_operations import submit

    op = submit(agent_client, "plain").json()
    run_to_ready(agent_client, recorded_dispatcher, op)
    response = promote(agent_client, op["resource_id"])
    assert response.status_code == 422


def test_discovery_advertises_promotion(promo):
    for prefix in ("", "/v1"):
        assert promo.get(f"{prefix}/agent").json()["capabilities"]["promotion"] is True


def upgrade(client, rid, key="u-1", prefix="", **body):
    body.setdefault("version", "v1.1.0")
    return client.post(
        f"{prefix}/resources/{rid}/upgrade", json=body, headers={"Idempotency-Key": key}
    )


def plain_ready(client, dispatcher, key="r1", **inputs):
    op = client.post(
        "/operations",
        json={
            "pattern": "demo",
            "version": "v1.0.0",
            "inputs": {"filename": "u.txt", "content": "keep", **inputs},
        },
        headers={"Idempotency-Key": key},
    ).json()
    run_to_ready(client, dispatcher, op)
    return op["resource_id"]


@pytest.fixture
def upgradable(pattern_repo, tmp_path):
    """v1.4.0 and v1.5.0 keep state outside the workspace, as a shared backend would, so the
    platform may move a deployment between versions (it refuses that for workspace-local state)."""
    state = tmp_path / "shared.tfstate"
    head = (pattern_repo / "main.tf").read_text()
    backend = f'terraform {{\n  backend "local" {{\n    path = "{state}"\n  }}\n}}\n'
    (pattern_repo / "main.tf").write_text(backend + head)
    git(pattern_repo, "commit", "-qam", "shared state")
    git(pattern_repo, "tag", "v1.4.0")
    (pattern_repo / "main.tf").write_text(
        (pattern_repo / "main.tf").read_text().replace('}${var.suffix}"', '}${var.suffix}!"')
    )
    git(pattern_repo, "commit", "-qam", "bang")
    git(pattern_repo, "tag", "v1.5.0")


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_upgrade_preserves_optional_input_and_labels(
    agent_client, recorded_dispatcher, upgradable, prefix
):
    op = agent_client.post(
        "/operations",
        json={
            "pattern": "demo",
            "version": "v1.4.0",
            "inputs": {"filename": "u.txt", "content": "keep", "suffix": "-opt"},
            "labels": {"team": "a"},
        },
        headers={"Idempotency-Key": "first"},
    ).json()
    run_to_ready(agent_client, recorded_dispatcher, op)
    rid = op["resource_id"]
    work = settings.data_dir / "deployments" / rid / "work"
    assert (work / "out" / "u.txt").read_text() == "keep-opt"

    response = upgrade(agent_client, rid, prefix=prefix, version="v1.5.0")
    assert response.status_code == 202, response.text
    new = response.json()
    assert new["resource_id"] == rid and new["version"] == "v1.5.0"
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(new["links"]["self"]).json()
    assert planned["state"] == "planned"  # never auto-applied
    agent_client.post(new["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert recorded_dispatcher.run_next()
    resource = agent_client.get(f"{prefix}/resources/{rid}").json()
    assert resource["state"] == "ready" and resource["version"] == "v1.5.0"
    assert resource["labels"] == {"team": "a"}
    assert tfvars(rid)["suffix"] == "-opt"
    assert (work / "out" / "u.txt").read_text() == "keep-opt!"


def test_upgrade_refusals(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    before = ledger_count()
    noop = upgrade(promo, rid, key="n", version="v1.2.0")
    assert noop.status_code == 409 and noop.json()["error"]["reason"] == "upgrade_noop"
    assert upgrade(promo, rid, key="u", version="v9.9.9").status_code == 422
    assert upgrade(promo, "res_" + "0" * 32, key="m").status_code == 404
    assert ledger_count() == before

    busy = upgrade(promo, rid, key="b1", version="v1.3.0")
    assert busy.status_code == 202, busy.text
    again = upgrade(promo, rid, key="b2", version="v1.3.0")
    assert again.status_code == 409 and again.json()["error"]["reason"] == "resource_busy"
    replay = upgrade(promo, rid, key="b1", version="v1.3.0")
    assert replay.json()["id"] == busy.json()["id"]
    assert upgrade(promo, rid, key="b1", version="v1.3.0", labels={"x": "y"}).status_code == 409

    pending = promo.post(
        "/operations",
        json={
            "pattern": "demo",
            "version": "v1.2.0",
            "environment": "dev",
            "inputs": {"filename": "p.txt", "content": "c"},
        },
        headers={"Idempotency-Key": "pend"},
    ).json()["resource_id"]
    nr = upgrade(promo, pending, key="nr", version="v1.3.0")
    assert nr.status_code == 409 and nr.json()["error"]["reason"] == "resource_not_ready"


def test_upgrade_visibility_and_rights(promo, recorded_dispatcher):
    from app.main import caller_context
    from app.tenants import Caller

    rid = deploy(promo, recorded_dispatcher)
    app.dependency_overrides[caller_context] = lambda: Caller("other", frozenset({"stranger"}))
    try:
        assert upgrade(promo, rid, key="inv", version="v1.3.0").status_code == 404
    finally:
        app.dependency_overrides.pop(caller_context, None)


def test_upgrade_injected_override_refused_and_discovery(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    bad = upgrade(promo, rid, key="i", version="v1.3.0", inputs={"cost_center": "x"})
    assert bad.status_code == 422
    for prefix in ("", "/v1"):
        assert promo.get(f"{prefix}/agent").json()["capabilities"]["resource_upgrade"] is True


def test_promoted_from_survives_later_updates(promo, recorded_dispatcher):
    rid = deploy(promo, recorded_dispatcher)
    op = promote(promo, rid).json()
    run_to_ready(promo, recorded_dispatcher, op)
    new = op["resource_id"]
    assert promo.get(f"/resources/{new}").json()["promoted_from"] == rid
    update = promo.post(
        "/operations",
        json={
            "pattern": "demo",
            "resource_id": new,
            "inputs": {"filename": "dev-1.txt", "content": "changed"},
        },
        headers={"Idempotency-Key": "upd"},
    )
    assert update.status_code == 202, update.text
    run_to_ready(promo, recorded_dispatcher, update.json())
    after = promo.get(f"/resources/{new}").json()
    assert after["latest_operation_id"] == update.json()["id"]
    assert after["promoted_from"] == rid


def test_upgrade_expected_commit_detects_a_moved_tag(promo, recorded_dispatcher, pattern_repo):
    rid = deploy(promo, recorded_dispatcher)
    wrong = upgrade(promo, rid, key="ec", version="v1.3.0", expected_commit="0" * 40)
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["error"]["reason"] == "revision_moved"
    commit = git(pattern_repo, "rev-parse", "v1.3.0^{commit}").strip()
    assert (
        upgrade(promo, rid, key="ec2", version="v1.3.0", expected_commit=commit).status_code == 202
    )


def test_promotion_size_is_overridable_and_never_silently_wrong(
    promo, recorded_dispatcher, pattern_repo
):
    config = {
        "estimated_costs": {"small": {"dev": 10, "prod": 20}, "large": {"dev": 50}},
        "sizing": {"small": {"dev": {}, "prod": {}}, "large": {"dev": {}}},
    }
    (pattern_repo / "config.yaml").write_text(yaml.safe_dump(config))
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "sizes")
    git(pattern_repo, "tag", "v1.6.0")
    rid = deploy(promo, recorded_dispatcher, version="v1.6.0", size="large")
    before = ledger_count()
    refused = promote(promo, rid, key="sz1")
    assert refused.status_code == 422, refused.text
    body = refused.json()["error"]
    assert body["reason"] == "promotion_needs_size" and "small" in str(body)
    assert ledger_count() == before
    chosen = promote(promo, rid, key="sz2", size="small")
    assert chosen.status_code == 202, chosen.text
    stored = ledger.get(chosen.json()["id"])
    assert stored["size"] == "small"
    assert promote(promo, rid, key="sz3", size="huge").status_code == 422
