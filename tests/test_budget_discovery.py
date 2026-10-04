"""Discovery shows the budget headroom that submit enforces."""

import pytest
import yaml

from app import db
from app.settings import settings
from tests.test_budget_accounting import placed, seed_resource  # noqa: F401
from tests.test_operation_policy import request


def budgets(client, prefix=""):
    unit = client.get(f"{prefix}/agent").json()["business_units"][0]
    assert unit["name"] == "finance"
    return unit["budgets"]


def test_budget_tracks_admission_and_is_enforced(placed):  # noqa: F811
    assert budgets(placed) == {"dev": {"monthly_budget": 100, "reserved": 0, "available": 100}}
    assert placed.get("/agent").json()["capabilities"]["budget_discovery"] is True
    assert request(placed, "one").status_code == 202
    assert budgets(placed)["dev"] == {"monthly_budget": 100, "reserved": 60, "available": 40}
    # the pattern costs 60 > available 40: admission agrees with discovery
    refused = request(placed, "two")
    assert refused.status_code == 403
    assert refused.json()["error"]["reason"] == "budget_exceeded"
    assert "hr" not in str(placed.get("/agent").json()["business_units"])


def test_within_available_is_accepted(placed):  # noqa: F811
    seed_resource(1, 40)
    assert budgets(placed, "/v1")["dev"]["available"] == 60
    assert request(placed, "fits").status_code == 202


def test_destroyed_ignored_and_legacy_counted(placed):  # noqa: F811
    seed_resource(1, 30, state="destroyed")
    seed_resource(2, 10, unit="hr")
    db.create("demo", {}, business_unit="finance", environment="dev", estimated_monthly_cost=25)
    assert budgets(placed)["dev"] == {"monthly_budget": 100, "reserved": 25, "available": 75}


def test_over_budget_available_is_zero(placed):  # noqa: F811
    seed_resource(1, 150)
    assert budgets(placed)["dev"] == {"monthly_budget": 100, "reserved": 150, "available": 0}


def test_environment_without_budget_is_omitted(placed, monkeypatch):  # noqa: F811
    units = {
        "finance": {
            "groups": ["finance"],
            "patterns": ["demo"],
            "environments": {
                "dev": {"subscription_id": "s1", "budget_monthly": 100},
                "stage": {"subscription_id": "s2"},
            },
        }
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    unit = placed.get("/agent").json()["business_units"][0]
    assert set(unit["environments"]) == {"dev", "stage"}
    assert set(unit["budgets"]) == {"dev"}


@pytest.mark.parametrize("corrupt", ["stored", "limit"])
def test_invalid_data_is_sanitized_503(placed, monkeypatch, corrupt):  # noqa: F811
    if corrupt == "stored":
        seed_resource(1, -5)
    else:
        units = {
            "finance": {
                "groups": ["finance"],
                "patterns": ["demo"],
                "environments": {"dev": {"subscription_id": "s1", "budget_monthly": "bad"}},
            }
        }
        monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    response = placed.get("/v1/agent")
    assert response.status_code == 503, response.text
    assert "bad" not in response.text and "-5" not in response.text
