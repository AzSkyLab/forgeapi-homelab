"""Operation ownership rejects malformed authenticated principals."""

import base64
import json

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app as operation_app
from app.settings import settings
from tests.test_auth import entra as _entra_fixture
from tests.test_operations import INTENT

AUTH_MARKER = "auth-boundary-secret"
SECRET_INTENT = {**INTENT, "inputs": {**INTENT["inputs"], "content": AUTH_MARKER}}


@pytest.fixture
def entra(monkeypatch):
    return _entra_fixture.__wrapped__(monkeypatch)


def _principal(value):
    return base64.b64encode(json.dumps(value).encode()).decode()


def _operation_counts():
    with ledger.connect() as con:
        return tuple(
            con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("operations", "resources", "events")
        )


def _assert_last_refused():
    with ledger.connect() as con:
        event = con.execute(
            "SELECT actor, outcome FROM events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
    assert tuple(event) == ("unauthenticated", "refused")


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "principal",
    [
        [],
        {"claims": "bad"},
        {"claims": [None]},
        {"claims": [{"typ": 17, "val": "user-1"}]},
        {"claims": []},
        {"claims": [{"typ": "new-claim", "val": AUTH_MARKER}]},
        {"claims": [{"typ": "oid", "val": ""}]},
        {"claims": [{"typ": "oid", "val": "   "}]},
        {"claims": [{"typ": "oid", "val": 17}]},
        {"claims": [{"typ": "oid", "val": "first"}, {"typ": "oid", "val": None}]},
        {"claims": [{"typ": "oid", "val": None}, {"typ": "oid", "val": "second"}]},
        {"claims": [{"typ": "oid", "val": "user-1"}, {"typ": "groups", "val": 7}]},
        {"claims": [{"typ": "oid", "val": "user-1"}, {"typ": "groups", "val": " "}]},
    ],
)
def test_malformed_easy_auth_principal_cannot_submit(
    monkeypatch, recorded_dispatcher, prefix, principal
):
    monkeypatch.setattr(settings, "auth_mode", "easyauth")
    client = TestClient(operation_app)
    before = _operation_counts()
    header = _principal(principal)
    response = client.post(
        f"{prefix}/operations",
        json=SECRET_INTENT,
        headers={
            "Idempotency-Key": "malformed-principal",
            "x-ms-client-principal": header,
        },
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "authentication_required"
    assert AUTH_MARKER not in response.text and header not in response.text
    assert _operation_counts() == (before[0], before[1], before[2] + 1)
    _assert_last_refused()
    stored = (settings.data_dir / "operations.sqlite").read_bytes()
    assert AUTH_MARKER.encode() not in stored and header.encode() not in stored
    assert not recorded_dispatcher.pending
    assert client.get("/healthz").status_code == 200


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "claims",
    [
        {"oid": None},
        {"oid": ""},
        {"oid": "   "},
        {"oid": 17, "sub": "fallback"},
        {"groups": "finance"},
        {"groups": ["finance", 7]},
        {"groups": ["finance", " "]},
        {"groups": {"finance": True}},
    ],
)
def test_malformed_jwt_principal_cannot_submit(
    monkeypatch, recorded_dispatcher, entra, prefix, claims
):
    client = TestClient(operation_app)
    before = _operation_counts()
    token = entra(**claims, marker=AUTH_MARKER)
    response = client.post(
        f"{prefix}/operations",
        json=SECRET_INTENT,
        headers={
            "Idempotency-Key": "malformed-principal",
            "Authorization": f"Bearer {token}",
        },
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"]["code"] == "authentication_required"
    assert AUTH_MARKER not in response.text and token not in response.text
    assert _operation_counts() == (before[0], before[1], before[2] + 1)
    _assert_last_refused()
    stored = (settings.data_dir / "operations.sqlite").read_bytes()
    assert AUTH_MARKER.encode() not in stored and token.encode() not in stored
    assert not recorded_dispatcher.pending


def test_valid_jwt_sub_fallback_and_distinct_operation_owners(entra, recorded_dispatcher):
    client = TestClient(operation_app)
    first = {"Authorization": f"Bearer {entra(oid='', sub='sub-only')}", "Idempotency-Key": "same"}
    second = {"Authorization": f"Bearer {entra(oid='other')}", "Idempotency-Key": "same"}
    one = client.post("/v1/operations", json=INTENT, headers=first)
    two = client.post("/v1/operations", json=INTENT, headers=second)
    assert one.status_code == two.status_code == 202
    assert one.json()["id"] != two.json()["id"]
    assert ledger.get(one.json()["id"])["actor"] == "sub-only"
    assert ledger.get(two.json()["id"])["actor"] == "other"
    assert client.get(one.json()["links"]["self"], headers=second).status_code == 404
    assert len(recorded_dispatcher.pending) == 2


def test_easy_auth_alias_groups_and_additive_claims(monkeypatch, recorded_dispatcher):
    monkeypatch.setattr(settings, "auth_mode", "easyauth")
    client = TestClient(operation_app)
    principal = {
        "claims": [
            {
                "typ": "http://schemas.microsoft.com/identity/claims/objectidentifier",
                "val": "first",
            },
            {"typ": "oid", "val": "second"},
            {"typ": "groups", "val": "finance"},
            {"typ": "new-claim", "val": {"new": True}, "additive": True},
        ]
    }
    accepted = client.post(
        "/v1/operations",
        json=INTENT,
        headers={"x-ms-client-principal": _principal(principal), "Idempotency-Key": "valid"},
    )
    assert accepted.status_code == 202, accepted.text
    assert ledger.get(accepted.json()["id"])["actor"] == "first"
    assert len(recorded_dispatcher.pending) == 1


def test_jwt_oid_precedence_groups_none_and_overage(entra, recorded_dispatcher):
    client = TestClient(operation_app)
    valid = client.post(
        "/v1/operations",
        json=INTENT,
        headers={
            "Authorization": f"Bearer {entra(oid='primary', sub='secondary', groups=None)}",
            "Idempotency-Key": "valid",
        },
    )
    assert valid.status_code == 202, valid.text
    assert ledger.get(valid.json()["id"])["actor"] == "primary"
    overage = client.post(
        "/v1/operations",
        json=INTENT,
        headers={
            "Authorization": f"Bearer {entra(_claim_names={'groups': 'src1'})}",
            "Idempotency-Key": "overage",
        },
    )
    assert overage.status_code == 403
    assert len(recorded_dispatcher.pending) == 1
