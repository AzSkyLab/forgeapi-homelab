"""The v1 surface keeps old clients and idempotent intents compatible."""

import hashlib
import json
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, ValidationError

from app import ledger
from app.contracts import Discovery, Intent, Operation, canonical_intent
from app.main import SOFTWARE_RELEASE, app
from tests.test_operations import INTENT

FIXTURE = Path(__file__).parent / "fixtures" / "v1" / "openapi.json"
REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(recorded_dispatcher):
    return TestClient(app)


def test_root_and_v1_replay_share_one_acceptance(client):
    headers = {"Idempotency-Key": "same-body"}
    root = client.post("/operations", json=INTENT, headers=headers)
    versioned = client.post("/v1/operations", json=INTENT, headers=headers)
    assert root.status_code == versioned.status_code == 202
    assert root.json()["id"] == versioned.json()["id"]
    assert root.headers["location"] == root.json()["links"]["self"]
    assert versioned.headers["location"] == versioned.json()["links"]["self"]
    assert root.json()["links"]["self"].startswith("/operations/")
    assert versioned.json()["links"]["self"].startswith("/v1/operations/")
    assert (
        client.get(versioned.json()["links"]["self"]).json()["links"] == versioned.json()["links"]
    )
    assert client.get(versioned.json()["links"]["events"]).status_code == 200
    with ledger.connect() as con:
        assert (
            con.execute("SELECT count(*) FROM events WHERE outcome='accepted'").fetchone()[0] == 1
        )


def test_versioned_refusal_and_validation_audit_exemption(client):
    invalid = client.post("/v1/intents/validate", json={"pattern": "demo", "typo": "hidden"})
    assert invalid.status_code == 422
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0] == 0
    refused = client.post(
        "/v1/operations",
        json={"pattern": "demo", "typo": "hidden"},
        headers={"Idempotency-Key": "invalid"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_request"
    assert "hidden" not in refused.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0] == 1


def test_dispatch_unconfirmed_uses_versioned_status_url(client, recorded_dispatcher, monkeypatch):
    async def unavailable(*_args):
        raise RuntimeError("raw transport error")

    monkeypatch.setattr(recorded_dispatcher, "dispatch", unavailable)
    response = client.post(
        "/v1/operations", json=INTENT, headers={"Idempotency-Key": "dispatch-ambiguity"}
    )
    assert response.status_code == 503
    status = response.json()["error"]["status_url"]
    assert status == response.headers["location"]
    assert status.startswith("/v1/operations/")
    assert response.json()["error"]["operation_id"] == client.get(status).json()["id"]
    assert "raw transport error" not in response.text


def test_discovery_and_schema_are_versioned(client):
    root = client.get("/agent").json()
    v1 = client.get("/v1/agent").json()
    assert root["links"]["openapi"] == "/openapi.json"
    assert v1["links"]["openapi"] == "/v1/openapi.json"
    assert v1["supported_api_major"] == 1
    assert v1["software_release"] == SOFTWARE_RELEASE == app.version
    assert (
        tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
        == SOFTWARE_RELEASE
    )
    assert v1["capabilities"] == {
        "idempotent_intents": True,
        "exact_plan_execution": True,
        "operation_events": True,
        "stable_operation_pagination": True,
        "discard_planned_operation": True,
        "resource_inventory": True,
        "operator_reconciliation": True,
        "operation_filters": True,
        "operation_long_poll": True,
        "plan_guardrails": True,
        "reference_protection": True,
        "resource_filters": True,
        "upgrade_detection": True,
        "pattern_listing": True,
        "guardrail_discovery": True,
        "budget_discovery": True,
        "resource_labels": True,
        "input_references": True,
        "plan_expiry": True,
        "failure_diagnostics": True,
        "web_console": True,
        "app_rollouts": True,
        "app_failover": True,
        "app_teardown": True,
        "drift_checks": True,
        "promotion": True,
        "resource_upgrade": True,
        "cost_history": True,
        "deployable_patterns": True,
        "pattern_changes": True,
        "pattern_checks": True,
        "team_admin": True,
    }
    assert Discovery.model_validate(
        {
            key: value
            for key, value in root.items()
            if key not in ("supported_api_major", "software_release", "capabilities")
        }
    )
    current = client.get("/v1/openapi.json").json()
    assert current == json.loads(FIXTURE.read_text())
    assert all(path.startswith("/v1/") for path in current["paths"])
    for name in (
        "Discovery",
        "PatternDescription",
        "ValidationResult",
        "EventPage",
        "ErrorEnvelope",
    ):
        assert name in current["components"]["schemas"]
    root_schema = client.get("/openapi.json").json()
    assert root_schema["paths"]["/operations"]["post"]["operationId"] == "submit_intent"
    assert (
        root_schema["paths"]["/operations/{operation_id}/execute"]["post"]["operationId"]
        == "execute_plan"
    )


def test_old_and_new_consumers_use_shared_safe_subset(client):
    class OldDiscovery(BaseModel):
        model_config = ConfigDict(extra="ignore")
        contract: str
        patterns: list[str]
        links: dict[str, str]

    old = json.loads((FIXTURE.parent / "old_discovery.json").read_text())
    legacy_operation = json.loads((FIXTURE.parent / "old_operation.json").read_text())
    new = client.get("/v1/agent").json()

    def bootstrap_and_submit(advertisement, key, optional_capability=None):
        discovered = Discovery.model_validate(advertisement)
        if optional_capability and not discovered.capabilities.get(optional_capability, False):
            return None
        return client.post(discovered.links.submit, json=INTENT, headers={"Idempotency-Key": key})

    legacy_client = OldDiscovery.model_validate(new)
    assert legacy_client.contract == "agent-v1"
    old_client_response = client.post(
        legacy_client.links["submit"],
        json=INTENT,
        headers={"Idempotency-Key": "old-client-new-server"},
    )
    assert old_client_response.status_code == 202
    assert old_client_response.json()["links"]["self"].startswith("/v1/operations/")
    discovery = Discovery.model_validate(old)
    assert discovery.supported_api_major == 1
    assert discovery.capabilities == {}
    assert discovery.links.openapi == "/openapi.json"
    with ledger.connect() as con:
        before = con.execute("SELECT count(*) FROM operations").fetchone()[0]
    assert bootstrap_and_submit(old, "capability-absent", "exact_plan_execution") is None
    assert (
        bootstrap_and_submit(
            {**new, "capabilities": {"exact_plan_execution": False}},
            "capability-false",
            "exact_plan_execution",
        )
        is None
    )
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == before
    assert bootstrap_and_submit(old, "new-client-old-server").status_code == 202
    assert (
        bootstrap_and_submit(
            {**new, "capabilities": {**new["capabilities"], "future_capability": True}},
            "new-client-new-server",
            "exact_plan_execution",
        ).status_code
        == 202
    )
    assert Operation.model_validate(legacy_operation).state == "queued"
    with pytest.raises(ValidationError):
        Operation.model_validate({**legacy_operation, "state": "paused"})
    with pytest.raises(ValidationError):
        Operation.model_validate({**legacy_operation, "next_action": "replay_apply"})


def test_intent_material_stays_default_expanded_and_strict(monkeypatch):
    intent = Intent.model_validate({"pattern": "demo"})
    body = canonical_intent(intent)
    assert body == {
        "action": "deploy",
        "resource_id": None,
        "pattern": "demo",
        "version": None,
        "expected_commit": None,
        "business_unit": None,
        "environment": None,
        "size": None,
        "inputs": {},
    }
    assert (
        ledger.fingerprint(body)
        == hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    )
    with pytest.raises(ValueError):
        Intent.model_validate({"pattern": "demo", "future_intent_field": "must fail"})
    original = Intent.model_dump

    def expanded(self, **kwargs):
        return {**original(self, **kwargs), "new_default": None}

    monkeypatch.setattr(Intent, "model_dump", expanded)
    with pytest.raises(RuntimeError, match="explicit canonicalization"):
        canonical_intent(intent)
