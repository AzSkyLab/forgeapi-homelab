"""Real applies and one injected output response exercise the storage boundary."""

import json
import math

import pytest
from fastapi.testclient import TestClient

from app import ledger, terraform
from app.main import app
from app.settings import settings
from tests.conftest import git


@pytest.mark.parametrize(
    ("value", "sensitive", "inject_overflow"),
    [
        pytest.param("1e400", False, False, id="real-large-integer"),
        pytest.param("42", False, False, id="real-finite"),
        pytest.param("1e400", True, False, id="real-sensitive-large-integer"),
        pytest.param("1e400", False, True, id="injected-overflowing-output"),
    ],
)
def test_real_apply_output_boundary_with_one_injected_overflow(
    pattern_repo, recorded_dispatcher, monkeypatch, value, sensitive, inject_overflow
):
    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + f'\noutput "numeric_boundary" {{\n  value = {value}'
        + ('\n  sensitive = true' if sensitive else '')
        + '\n}\n'
    )
    git(pattern_repo, "commit", "-qam", "numeric output")
    git(pattern_repo, "tag", "v1.2.0")
    client = TestClient(app)
    intent = {
        "pattern": "demo",
        "version": "v1.2.0",
        "inputs": {"filename": "boundary.txt", "content": "boundary"},
    }
    created = client.post("/operations", json=intent, headers={"Idempotency-Key": "boundary"})
    assert created.status_code == 202, created.text
    op = created.json()
    assert recorded_dispatcher.run_next()
    planned = ledger.get(op["id"])
    assert planned["state"] == "planned"
    execute = client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert execute.status_code == 202, execute.text
    observed = []
    if inject_overflow:
        original_run = terraform._run

        def inject_output(*args, **kwargs):
            raw = original_run(*args, **kwargs)
            if args[1] != "output":
                return raw
            payload = json.loads(raw)
            original_value = payload["numeric_boundary"]["value"]
            payload["numeric_boundary"]["value"] = "INJECTED_OVERFLOW_MARKER"
            injected = json.dumps(payload).replace('"INJECTED_OVERFLOW_MARKER"', "1e400")
            decoded = json.loads(injected)["numeric_boundary"]["value"]
            observed.append((type(original_value) is int, original_value == 10**400,
                             type(decoded) is float and math.isinf(decoded)))
            return injected

        monkeypatch.setattr(terraform, "_run", inject_output)
    assert recorded_dispatcher.run_next()

    stored = ledger.get(op["id"])
    if inject_overflow:
        assert observed == [(True, True, True)]
    expected_state = "uncertain" if inject_overflow else "succeeded"
    assert stored["state"] == expected_state
    assert terraform.log_path(op["resource_id"]).read_text().count("$ terraform apply ") == 1
    assert not recorded_dispatcher.run_next()
    assert len([e for e in ledger.events(op["id"], 0, 100) if e["outcome"] == expected_state]) == 1
    response = client.get(op["links"]["self"])
    assert response.status_code == 200, response.text
    assert "Infinity" not in response.text
    assert b"Infinity" not in (settings.data_dir / "operations.sqlite").read_bytes()

    if expected_state == "uncertain":
        assert stored["outputs"] is None
        assert stored["error"] == "execution outcome requires operator reconciliation"
        assert response.json()["next_action"] == "reconcile_with_operator"
        assert (
            client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
            .status_code
            == 409
        )
        replacement = client.post(
            "/operations",
            json={**intent, "resource_id": op["resource_id"]},
            headers={"Idempotency-Key": "replacement"},
        )
        assert replacement.status_code == 409
        assert terraform.log_path(op["resource_id"]).read_text().count("$ terraform apply ") == 1
    elif sensitive:
        assert "numeric_boundary" not in stored["outputs"]
        assert "numeric_boundary" in stored["withheld_outputs"]
    else:
        assert stored["outputs"]["numeric_boundary"] == (10**400 if value == "1e400" else 42)
