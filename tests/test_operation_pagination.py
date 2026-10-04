"""Stable operation traversal keeps the existing offset contract intact."""

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.contracts import OperationPage
from app.main import app, caller_context
from app.settings import settings
from app.tenants import Caller
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import INTENT


def submit(client, key):
    response = client.post(
        "/operations", json=INTENT, headers={"Idempotency-Key": key}
    )
    assert response.status_code == 202
    return response.json()["id"]


def test_before_stays_stable_as_operations_arrive_and_states_change(recorded_dispatcher):
    client = TestClient(app)
    original = [submit(client, str(i)) for i in range(5)]
    first = client.get("/v1/operations?limit=2").json()
    assert [item["id"] for item in first["items"]] == original[-1:-3:-1]
    assert first["next_offset"] == 2
    assert first["next_before"] == original[-2]
    assert all(item["links"]["self"].startswith("/v1/") for item in first["items"])

    newest = submit(client, "newest")
    claimed = ledger.claim(original[0], "plan")
    ledger.finish(claimed, "planned", plan_digest="a" * 64)
    seen = [item["id"] for item in first["items"]]
    before = first["next_before"]
    while before:
        page = client.get("/v1/operations", params={"limit": 2, "before": before}).json()
        assert page["next_offset"] is None
        seen.extend(item["id"] for item in page["items"])
        before = page["next_before"]
    assert seen == original[::-1]
    assert newest not in seen
    assert len(seen) == len(set(seen))

    # Offset mode retains its original semantics, including shifts after a new acceptance.
    offset_page = client.get("/v1/operations?limit=2&offset=2").json()
    assert [item["id"] for item in offset_page["items"]] == original[-2:-4:-1]
    assert offset_page["next_offset"] == 4
    assert client.get("/v1/operations?limit=100").json()["next_before"] is None


def test_before_validation_visibility_and_alias_parity(recorded_dispatcher):
    client = TestClient(app)
    older, newer = submit(client, "older"), submit(client, "newer")
    for prefix in ("", "/v1"):
        page = client.get(f"{prefix}/operations", params={"limit": 1, "offset": 0})
        assert page.status_code == 200
        assert page.json()["next_before"] == newer
        follow = client.get(f"{prefix}/operations", params={"before": newer, "limit": 1})
        assert follow.status_code == 200
        assert [item["id"] for item in follow.json()["items"]] == [older]
        assert follow.json()["items"][0]["links"]["self"].startswith(
            f"{prefix}/operations/"
        )
        assert follow.json()["next_before"] is None
        assert follow.json()["next_offset"] is None
        assert (
            client.get(f"{prefix}/operations", params={"before": newer, "offset": 1}).status_code
            == 422
        )
        assert client.get(f"{prefix}/operations", params={"before": "bad"}).status_code == 422
        missing = client.get(f"{prefix}/operations", params={"before": "op_" + "0" * 32})
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"
        assert client.get(f"{prefix}/operations", params={"limit": 0}).status_code == 422

    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        invisible = client.get("/operations", params={"before": newer})
        nonexistent = client.get("/operations", params={"before": "op_" + "0" * 32})
        assert invisible.status_code == nonexistent.status_code == 404
        # Each response carries its own request_id; everything else must be identical.
        bodies = [r.json() for r in (invisible, nonexistent)]
        assert all(b["error"].pop("request_id") for b in bodies)
        assert bodies[0] == bodies[1]
    finally:
        app.dependency_overrides.pop(caller_context, None)


def test_before_anchor_obeys_business_unit_visibility(placed, monkeypatch):
    finance = placed_request(placed, "finance").json()["id"]
    monkeypatch.setattr(settings, "dev_groups", "hr")
    hr = placed_request(placed, "hr").json()["id"]
    for anchor in (finance, "op_" + "0" * 32):
        response = placed.get("/operations", params={"before": anchor})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
    assert [item["id"] for item in placed.get("/operations").json()["items"]] == [hr]


def test_old_page_without_optional_cursor_still_parses():
    assert OperationPage.model_validate({"items": [], "next_offset": None}).next_before is None


@pytest.mark.parametrize("prefix", ("", "/v1"))
@pytest.mark.parametrize("position,path", (("offset", "/operations"), ("after", "/events")))
def test_page_positions_fit_sqlite_integers(recorded_dispatcher, prefix, position, path):
    client = TestClient(app)
    operation_id = submit(client, f"page-position-{'v1' if prefix else 'root'}-{position}")
    url = f"{prefix}/operations" + (f"/{operation_id}{path}" if path == "/events" else "")

    current = client.get(url, params={position: 0})
    assert current.status_code == 200
    assert current.json()["items"]

    last_sqlite_int = (1 << 63) - 1
    end = client.get(url, params={position: last_sqlite_int})
    assert end.status_code == 200
    assert end.json()["items"] == []

    for invalid in (last_sqlite_int + 1, 1 << 100):
        response = client.get(url, params={position: invalid})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert response.json()["error"]["detail"][0]["field"] == ["query", position]
