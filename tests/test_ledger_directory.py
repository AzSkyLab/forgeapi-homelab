"""A non-directory ledger path fails safely and accepts a retry after repair."""

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.test_operations import INTENT

SECRET = "ledger-directory-secret"


@pytest.mark.parametrize("prefix", ("", "/v1"))
def test_regular_file_at_ledger_directory_fails_safely_then_recovers(
    prefix, recorded_dispatcher
):
    path = settings.data_dir
    path.write_text(SECRET)
    client = TestClient(app)
    missing_id = "op_" + "0" * 32
    for suffix in (f"/{missing_id}", "", f"/{missing_id}/events"):
        response = client.get(f"{prefix}/operations{suffix}")
        assert response.status_code == 503, response.text
        body = response.json()
        assert body["error"].pop("request_id") == response.headers["x-request-id"]
        assert body == {
            "error": {
                "code": "service_unavailable",
                "detail": "operation ledger unavailable",
                "next_action": "inspect_and_correct_request",
            }
        }
        assert SECRET not in response.text and str(path) not in response.text

    refused = client.post(
        f"{prefix}/operations", json=INTENT, headers={"Idempotency-Key": "directory-retry"}
    )
    assert refused.status_code == 503, refused.text
    error = refused.json()["error"]
    assert error.pop("request_id") == refused.headers["x-request-id"]
    assert error == {
        "code": "service_unavailable",
        "detail": "audit unavailable; request was not accepted",
        "next_action": "inspect_and_correct_request",
    }
    assert SECRET not in refused.text and str(path) not in refused.text
    assert path.read_text() == SECRET
    assert not recorded_dispatcher.pending

    path.unlink()
    path.mkdir()
    accepted = client.post(
        f"{prefix}/operations", json=INTENT, headers={"Idempotency-Key": "directory-retry"}
    )
    assert accepted.status_code == 202, accepted.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 1
        assert [row[0] for row in con.execute("SELECT outcome FROM events")] == ["accepted"]
    assert list(recorded_dispatcher.pending) == [(accepted.json()["id"], "plan")]
