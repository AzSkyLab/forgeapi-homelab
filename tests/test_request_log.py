"""Request-ID correlation and the one-line-per-request structured access log."""

import json
import logging

from fastapi.testclient import TestClient

from app.main import REQUEST_ID, app


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_valid_request_id_is_echoed():
    client = TestClient(app)
    response = client.get("/healthz", headers={"X-Request-ID": "caller-chosen-id.123"})
    assert response.headers["x-request-id"] == "caller-chosen-id.123"


def test_missing_or_invalid_request_id_is_replaced():
    client = TestClient(app)
    missing = client.get("/healthz")
    assert REQUEST_ID.fullmatch(missing.headers["x-request-id"])

    invalid = client.get("/healthz", headers={"X-Request-ID": "has a space"})
    assert invalid.headers["x-request-id"] != "has a space"
    assert REQUEST_ID.fullmatch(invalid.headers["x-request-id"])

    too_long = client.get("/healthz", headers={"X-Request-ID": "a" * 129})
    assert too_long.headers["x-request-id"] != "a" * 129
    assert REQUEST_ID.fullmatch(too_long.headers["x-request-id"])


def test_request_id_present_in_error_envelope_and_matches_header():
    client = TestClient(app)
    response = client.get(
        "/v1/operations/op_" + "0" * 32, headers={"X-Request-ID": "lookup-error-case"}
    )
    assert response.status_code == 404
    assert response.json()["error"]["request_id"] == "lookup-error-case"
    assert response.headers["x-request-id"] == "lookup-error-case"


def test_access_log_line_has_exactly_the_documented_keys_and_no_sensitive_content():
    client = TestClient(app)
    capture = _Capture()
    logger = logging.getLogger("forgeapi.access")
    logger.addHandler(capture)
    secret_token = "zz-never-logged-bearer-token"
    resource_filter = "res_" + "ab" * 16
    try:
        response = client.get(
            "/v1/operations",
            params={"resource_id": resource_filter},
            headers={
                "Authorization": f"Bearer {secret_token}",
                "X-Request-ID": "access-log-case",
            },
        )
    finally:
        logger.removeHandler(capture)
    assert response.status_code == 200

    matching = [m for m in capture.messages if "access-log-case" in m]
    assert len(matching) == 1
    line = matching[0]
    entry = json.loads(line)
    assert set(entry) == {"request_id", "method", "route", "status", "duration_ms", "caller"}
    assert entry["request_id"] == "access-log-case"
    assert entry["method"] == "GET"
    assert entry["route"] == "/v1/operations"
    assert entry["status"] == 200
    assert isinstance(entry["duration_ms"], (int, float))

    assert secret_token not in line
    assert "Authorization" not in line and "authorization" not in line
    assert resource_filter not in line
    assert "resource_id" not in line
