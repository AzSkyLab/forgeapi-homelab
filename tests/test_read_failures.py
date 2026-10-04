"""SQLite read errors stay sanitized and never create refusal audit events."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.test_operations import INTENT

SECRET = "read-failure-secret /tmp/private-ledger.sqlite"


def rows():
    with sqlite3.connect(settings.data_dir / "operations.sqlite") as con:
        return tuple(
            con.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in ("operations", "resources", "events")
        )


@pytest.mark.parametrize("prefix", ("", "/v1"))
@pytest.mark.parametrize(
    "read, suffix", (("get", "/{id}"), ("list_operations", ""), ("events", "/{id}/events"))
)
def test_read_failure_returns_fixed_503_without_audit_or_mutation(
    prefix, read, suffix, monkeypatch, recorded_dispatcher
):
    client = TestClient(app)
    created = client.post(
        "/operations", json=INTENT, headers={"Idempotency-Key": "read-failure-seed"}
    )
    assert created.status_code == 202, created.text
    operation_id = created.json()["id"]
    before = rows()
    pending = list(recorded_dispatcher.pending)
    assert pending == [(operation_id, "plan")]
    refusals = []

    def forbidden_refusal(*args):
        refusals.append(args)

    def broken_read(*args, **kwargs):
        raise sqlite3.OperationalError(SECRET)

    monkeypatch.setattr(ledger, "refused", forbidden_refusal)
    monkeypatch.setattr(ledger, read, broken_read)
    response = client.get(f"{prefix}/operations{suffix.format(id=operation_id)}")
    assert response.status_code == 503
    body = response.json()
    assert body["error"].pop("request_id") == response.headers["x-request-id"]
    assert body == {
        "error": {
            "code": "service_unavailable",
            "detail": "operation ledger unavailable",
            "next_action": "inspect_and_correct_request",
        }
    }
    assert SECRET not in response.text
    assert rows() == before
    assert list(recorded_dispatcher.pending) == pending
    assert refusals == []
