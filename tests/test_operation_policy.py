"""Agent policy isolation and atomic budget reservation."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.conftest import git


@pytest.fixture
def placed(tmp_path, monkeypatch, pattern_repo, recorded_dispatcher):
    source = pattern_repo / "main.tf"
    source.write_text(source.read_text() + '\nvariable "cost_center" { type = string }\n')
    (pattern_repo / "config.yaml").write_text("estimated_costs: 60\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "placement")
    git(pattern_repo, "tag", "v1.2.0")
    units = {
        name: {
            "groups": [name],
            "inject": {"cost_center": name},
            "patterns": ["demo"],
            "environments": {"dev": {"subscription_id": f"hidden-{name}", "budget_monthly": 100}},
        }
        for name in ("finance", "hr")
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    monkeypatch.setattr(settings, "dev_groups", "finance")
    return TestClient(app)


def request(client, key, prefix="", **updates):
    body = {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": "dev",
        "inputs": {"filename": "x.txt", "content": "test"},
        **updates,
    }
    return client.post(f"{prefix}/operations", json=body, headers={"Idempotency-Key": key})


def operation_counts():
    path = settings.data_dir / "operations.sqlite"
    if not path.exists():
        return (0, 0, 0, 0)
    with sqlite3.connect(path) as con:
        return tuple(
            con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("operations", "resources", "events WHERE outcome='accepted'", "events")
        )


def assert_bad_configuration(client, prefix, recorded_dispatcher, version, *, size=None):
    body = {
        "pattern": "demo",
        "version": version,
        "environment": "dev",
        "inputs": {"filename": "x.txt", "content": "boundary-secret-value"},
    }
    if size is not None:
        body["size"] = size
    before = operation_counts()
    validation = client.post(f"{prefix}/intents/validate", json=body)
    assert validation.status_code == 503, validation.text
    assert validation.json()["error"]["code"] == "service_unavailable"
    assert "boundary-secret-value" not in validation.text
    assert operation_counts() == before

    submission = client.post(
        f"{prefix}/operations", json=body, headers={"Idempotency-Key": "bad-config"}
    )
    assert submission.status_code == 503, submission.text
    assert submission.json()["error"]["code"] == "service_unavailable"
    assert "boundary-secret-value" not in submission.text
    assert operation_counts() == (before[0], before[1], before[2], before[3] + 1)
    assert not recorded_dispatcher.pending
    assert b"boundary-secret-value" not in (settings.data_dir / "operations.sqlite").read_bytes()


@pytest.fixture
def priced_pattern(placed, pattern_repo):
    def set_cost(value):
        (pattern_repo / "config.yaml").write_text(yaml.safe_dump({"estimated_costs": value}))
        git(pattern_repo, "add", "config.yaml")
        git(pattern_repo, "commit", "--allow-empty", "-qm", "new price")
        git(pattern_repo, "tag", "v1.3.0")
        return placed

    return set_cost


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize("cost", [-1, float("nan"), float("inf"), -float("inf"), 10**400])
def test_bad_estimate_refused_before_admission(
    priced_pattern, cost, prefix, recorded_dispatcher
):
    client = priced_pattern(cost)
    assert_bad_configuration(client, prefix, recorded_dispatcher, "v1.3.0")


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "limit", [-1, float("nan"), float("inf"), -float("inf"), 10**400, True, "bad"]
)
def test_bad_budget_refused_before_admission(
    placed, monkeypatch, limit, prefix, recorded_dispatcher
):
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["business_units"]["finance"]["environments"]["dev"]["budget_monthly"] = limit
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    assert_bad_configuration(placed, prefix, recorded_dispatcher, "v1.2.0")


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize("entry", [None, 7, True, [], "boundary-secret-value"])
def test_malformed_selected_size_price_refused_before_admission(
    placed, pattern_repo, recorded_dispatcher, prefix, entry
):
    (pattern_repo / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "sizing": {"small": {"dev": {}}},
                "estimated_costs": {"small": entry, "dev": 60},
            }
        )
    )
    git(pattern_repo, "add", "config.yaml")
    git(pattern_repo, "commit", "-qm", "sized price")
    git(pattern_repo, "tag", "v1.3.0")
    assert_bad_configuration(placed, prefix, recorded_dispatcher, "v1.3.0", size="small")


@pytest.mark.parametrize(
    ("cost", "limit", "status"),
    [
        (None, None, 202),
        (None, 100, 403),
        (0, 0, 202),
        (0.25, 1, 202),
        (60, 60, 202),
        (60, 59, 403),
        ({"dev": 60}, 60, 202),
    ],
)
def test_valid_prices_keep_budget_behavior(
    priced_pattern, monkeypatch, cost, limit, status, recorded_dispatcher
):
    client = priced_pattern(cost)
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["business_units"]["finance"]["environments"]["dev"]["budget_monthly"] = limit
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    response = request(client, "valid-price", version="v1.3.0")
    assert response.status_code == status, response.text
    assert len(recorded_dispatcher.pending) == int(status == 202)


def test_injected_inputs_hidden_and_other_unit_operations_invisible(placed, monkeypatch):
    described = placed.get("/patterns/demo?environment=dev").json()
    assert "cost_center" not in described["input_schema"]["properties"]
    assert (
        request(
            placed, "bad", inputs={"filename": "x", "content": "y", "cost_center": "hr"}
        ).status_code
        == 422
    )
    accepted = request(placed, "finance")
    assert accepted.status_code == 202
    assert "hidden-finance" not in accepted.text and "cost_center" not in accepted.text
    assert ledger.get(accepted.json()["id"])["injected"]["cost_center"] == "finance"
    monkeypatch.setattr(settings, "dev_groups", "hr")
    assert placed.get(accepted.json()["links"]["self"]).status_code == 404
    assert placed.get("/operations").json()["items"] == []
    assert (
        placed.post(accepted.json()["links"]["execute"], json={"plan_digest": "a" * 64}).status_code
        == 404
    )


@pytest.mark.parametrize("attempt", range(5))
def test_concurrent_budget_reservations_cannot_overspend(placed, attempt):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda k: request(TestClient(app), k), ["one", "two"]))
    assert sorted(r.status_code for r in results) == [202, 403], [r.json() for r in results]
    assert len(placed.get("/operations").json()["items"]) == 1


def test_revoked_deploy_rights_cannot_execute_saved_plan(placed, monkeypatch):
    op = request(placed, "one").json()
    ledger.finish(ledger.claim(op["id"], "plan"), "planned", plan_digest="b" * 64)
    mapping = yaml.safe_load(settings.tenants_yaml)
    mapping["business_units"]["finance"]["environments"]["dev"]["groups"] = ["release-only"]
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(mapping))
    assert placed.get(op["links"]["self"]).status_code == 200
    assert placed.post(op["links"]["execute"], json={"plan_digest": "b" * 64}).status_code == 403


def test_existing_legacy_budget_is_read_without_migrating_state(placed):
    from app import db
    from app.models import State

    db.create("demo", {}, business_unit="finance", environment="dev", estimated_monthly_cost=50)
    path = settings.data_dir / "forgeapi.db"
    before = path.read_bytes()
    assert request(placed, "new").status_code == 403
    assert path.read_bytes() == before
    # A legacy resource that was explicitly destroyed no longer reserves cost.
    prior = db.list_for(["finance"])[0]
    db.update(prior.id, State.destroyed)
    assert request(placed, "new").status_code == 202
