"""Malformed agent requests stop before acceptance or dispatch on both v1 routes."""

import json
import sqlite3

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings

INTENT = {
    "pattern": "demo",
    "version": "v1.0.0",
    "inputs": {"filename": "agent.txt", "content": "safe"},
}
SECRET = "boundary-secret-value"
JSON_HEADERS = {"content-type": "application/json", "Idempotency-Key": "boundary-key"}


def counts():
    path = settings.data_dir / "operations.sqlite"
    if not path.exists():
        return (0, 0, 0, 0)
    with sqlite3.connect(path) as con:
        return tuple(
            con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("operations", "resources", "events", "events WHERE outcome='refused'")
        )


def assert_error(response, status):
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == {
        400: "request_failed",
        404: "not_found",
        409: "conflict",
        422: "invalid_request",
    }[status]
    assert response.json()["error"]["next_action"] == "inspect_and_correct_request"
    assert SECRET not in response.text


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    ("body", "headers"),
    [
        ('{"pattern":', JSON_HEADERS),
        ('["' + SECRET + '"]', JSON_HEADERS),
        ('{"pattern":"demo","surprise":"' + SECRET + '"}', JSON_HEADERS),
        (INTENT, {"Idempotency-Key": "bad key"}),
        (INTENT, {}),
    ],
)
def test_invalid_submission_has_one_refusal_and_no_acceptance(
    prefix, body, headers, recorded_dispatcher
):
    client = TestClient(app)
    before = counts()
    if isinstance(body, str):
        response = client.post(f"{prefix}/operations", content=body, headers=headers)
    else:
        response = client.post(f"{prefix}/operations", json=body, headers=headers)
    assert_error(response, 422)
    assert counts() == (before[0], before[1], before[2] + 1, before[3] + 1)
    assert not recorded_dispatcher.pending
    assert SECRET not in (settings.data_dir / "operations.sqlite").read_bytes().decode(
        "utf-8", errors="ignore"
    )


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "body",
    ['{"pattern":', '["' + SECRET + '"]', '{"pattern":"demo","surprise":"' + SECRET + '"}'],
)
def test_invalid_validation_has_no_audit_or_mutation(prefix, body, recorded_dispatcher):
    client = TestClient(app)
    before = counts()
    response = client.post(
        f"{prefix}/intents/validate", content=body, headers={"content-type": "application/json"}
    )
    assert_error(response, 422)
    assert counts() == before
    assert not recorded_dispatcher.pending


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    ("body", "status"),
    [
        ('{"plan_digest":', 422),
        ('["' + SECRET + '"]', 422),
        ('{"plan_digest":"' + SECRET + '"}', 422),
        ('{"plan_digest":"' + "a" * 64 + '","surprise":"' + SECRET + '"}', 422),
        ('{"plan_digest":"' + "a" * 64 + '"}', 409),
    ],
)
def test_invalid_execution_keeps_saved_operation(prefix, body, status, recorded_dispatcher):
    client = TestClient(app)
    accepted = client.post(
        f"{prefix}/operations", json=INTENT, headers={"Idempotency-Key": "create"}
    )
    assert accepted.status_code == 202
    operation = accepted.json()
    before = counts()
    dispatched = list(recorded_dispatcher.pending)
    response = client.post(
        operation["links"]["execute"],
        content=body,
        headers={"content-type": "application/json"},
    )
    assert_error(response, status)
    assert counts() == (before[0], before[1], before[2] + 1, before[3] + 1)
    assert SECRET not in (settings.data_dir / "operations.sqlite").read_bytes().decode(
        "utf-8", errors="ignore"
    )
    assert client.get(operation["links"]["self"]).json() == operation
    assert list(recorded_dispatcher.pending) == dispatched


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "path",
    [
        "/operations?offset=9223372036854775808",
        "/operations?before=bad",
        "/operations/op_00000000000000000000000000000000/events?after=9223372036854775808",
    ],
)
def test_invalid_operation_query_is_read_only(prefix, path, recorded_dispatcher):
    client = TestClient(app)
    before = counts()
    assert_error(client.get(prefix + path), 422)
    assert counts() == before
    assert not recorded_dispatcher.pending


@pytest.fixture
def numeric_pattern(tmp_path):
    pattern = tmp_path / "numeric-pattern"
    pattern.mkdir()
    (pattern / "main.tf").write_text(
        'variable "value" { type = any }\nvariable "note" {\n  type = string\n  default = ""\n}\n'
    )
    settings.catalog_path.write_text(
        yaml.safe_dump({"patterns": {"numeric": {"local": str(pattern)}}})
    )


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "value",
    ["NaN", "Infinity", "-Infinity", "1e400", '{"nested":[0,"NaN",Infinity]}'],
)
def test_nonfinite_inputs_stop_before_validation_or_acceptance(
    prefix, value, numeric_pattern, recorded_dispatcher
):
    client = TestClient(app)
    body = '{"pattern":"numeric","inputs":{"value":' + value + ',"note":"' + SECRET + '"}}'
    before = counts()
    validation = client.post(
        f"{prefix}/intents/validate", content=body, headers={"content-type": "application/json"}
    )
    assert_error(validation, 422)
    assert counts() == before

    submission = client.post(f"{prefix}/operations", content=body, headers=JSON_HEADERS)
    assert_error(submission, 422)
    assert counts() == (before[0], before[1], before[2] + 1, before[3] + 1)
    assert not recorded_dispatcher.pending
    assert SECRET not in (settings.data_dir / "operations.sqlite").read_bytes().decode(
        "utf-8", errors="ignore"
    )


@pytest.mark.parametrize("value", ["0", "12", "0.25", "1e2", '"NaN"'])
def test_finite_inputs_and_numeric_string_preserve_value(
    value, numeric_pattern, recorded_dispatcher
):
    client = TestClient(app)
    body = '{"pattern":"numeric","inputs":{"value":' + value + "}}"
    checked = client.post(
        "/v1/intents/validate", content=body, headers={"content-type": "application/json"}
    )
    assert checked.status_code == 200, checked.text
    accepted = client.post("/v1/operations", content=body, headers=JSON_HEADERS)
    assert accepted.status_code == 202, accepted.text
    assert ledger.get(accepted.json()["id"])["inputs"]["value"] == json.loads(value)
    assert len(recorded_dispatcher.pending) == 1
