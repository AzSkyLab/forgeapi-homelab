"""Opt-in lazy plan expiry: a stale planned operation fails on execute or on a new intent."""

from datetime import UTC, datetime, timedelta

import pytest
import yaml
from pydantic import ValidationError

from app import ledger
from app.settings import Settings, settings
from app.terraform import deployment_dir
from tests.test_discard import refused_count
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit


def planned(client, dispatcher, key, **updates):
    op = submit(client, key, **updates).json()
    assert dispatcher.run_next()
    body = client.get(op["links"]["self"]).json()
    assert body["state"] == "planned"
    return body


def execute(client, op):
    return client.post(op["links"]["execute"], json={"plan_digest": op["plan_digest"]})


def age(monkeypatch, hours):
    later = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
    monkeypatch.setattr(ledger, "now", lambda: later)


def expire_events(operation_id):
    return [e for e in ledger.events(operation_id, 0, 100) if e["action"] == "operation.expire"]


def test_unset_setting_never_expires(agent_client, recorded_dispatcher, monkeypatch):
    op = planned(agent_client, recorded_dispatcher, "exp-unset")
    assert op["plan_expires_at"] is None
    age(monkeypatch, 24 * 365)
    assert execute(agent_client, op).status_code == 202


def test_fresh_plan_executes_and_reports_expiry(agent_client, recorded_dispatcher, monkeypatch):
    monkeypatch.setattr(settings, "plan_max_age_hours", 1.0)
    op = planned(agent_client, recorded_dispatcher, "exp-fresh")
    assert datetime.fromisoformat(op["plan_expires_at"]) > datetime.now(UTC)
    response = execute(agent_client, op)
    assert response.status_code == 202
    assert response.json()["plan_expires_at"] is None


def test_expired_plan_fails_once_and_frees_resource(agent_client, recorded_dispatcher, monkeypatch):
    monkeypatch.setattr(settings, "plan_max_age_hours", 1.0)
    op = planned(agent_client, recorded_dispatcher, "exp-old")
    plan = deployment_dir(op["resource_id"]) / "work" / "tfplan"
    assert plan.exists()
    age(monkeypatch, 2)
    before = refused_count()
    response = execute(agent_client, op)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["reason"] == "plan_expired"
    assert error["next_action"] == "validate_and_resubmit"
    assert refused_count() == before + 1
    failed = agent_client.get(op["links"]["self"]).json()
    assert failed["state"] == "failed"
    assert failed["error"] == "plan expired before execution; submit a new intent"
    assert failed["plan_expires_at"] is None
    assert len(expire_events(op["id"])) == 1
    assert not plan.exists()
    assert execute(agent_client, op).status_code == 409
    assert len(expire_events(op["id"])) == 1
    assert submit(agent_client, "exp-new", resource_id=op["resource_id"]).status_code == 202


def test_expiry_restores_budget_reservation(placed, recorded_dispatcher, monkeypatch):
    monkeypatch.setattr(settings, "plan_max_age_hours", 1.0)
    first = placed_request(placed, "exp-budget-1").json()
    assert recorded_dispatcher.run_next()
    op = placed.get(first["links"]["self"]).json()
    assert placed_request(placed, "exp-budget-2").status_code == 403
    age(monkeypatch, 2)
    assert execute(placed, op).status_code == 409
    assert placed_request(placed, "exp-budget-3").status_code == 202


def test_new_intent_expires_stale_blocker(agent_client, recorded_dispatcher, monkeypatch):
    monkeypatch.setattr(settings, "plan_max_age_hours", 1.0)
    op = planned(agent_client, recorded_dispatcher, "exp-block")
    busy = submit(agent_client, "exp-block-2", resource_id=op["resource_id"])
    assert busy.status_code == 409
    assert busy.json()["error"]["reason"] == "resource_busy"
    age(monkeypatch, 2)
    accepted = submit(agent_client, "exp-block-3", resource_id=op["resource_id"])
    assert accepted.status_code == 202
    old = agent_client.get(op["links"]["self"]).json()
    assert old["state"] == "failed"
    assert len(expire_events(op["id"])) == 1


def test_plan_expires_at_absent_unless_planned(agent_client, monkeypatch):
    monkeypatch.setattr(settings, "plan_max_age_hours", 1.0)
    assert submit(agent_client, "exp-queued").json()["plan_expires_at"] is None


@pytest.mark.parametrize("value", [0, -1])
def test_setting_must_be_positive(value):
    with pytest.raises(ValidationError):
        Settings(plan_max_age_hours=value)


def test_refused_intent_keeps_expired_blockers_plan(placed, recorded_dispatcher, monkeypatch):
    monkeypatch.setattr(settings, "plan_max_age_hours", 1.0)
    first = placed_request(placed, "exp-keep-1").json()
    assert recorded_dispatcher.run_next()
    plan = deployment_dir(first["resource_id"]) / "work" / "tfplan"
    assert plan.exists()
    age(monkeypatch, 2)
    units = yaml.safe_load(settings.tenants_yaml)
    units["business_units"]["finance"]["environments"]["dev"]["budget_monthly"] = 10
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))
    refused = placed_request(placed, "exp-keep-2", resource_id=first["resource_id"])
    assert refused.status_code == 403
    assert refused.json()["error"]["reason"] == "budget_exceeded"
    assert placed.get(first["links"]["self"]).json()["state"] == "planned"
    assert plan.exists()
