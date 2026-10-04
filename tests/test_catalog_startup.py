"""An unavailable Git executable is a safe catalog refusal and can recover."""

import pytest
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from tests.test_operations import INTENT

SECRET = "git-startup-secret"


@pytest.mark.parametrize("prefix", ("", "/v1"))
@pytest.mark.parametrize("failure", ("missing", "not-executable"))
def test_unavailable_git_is_sanitized_and_same_key_recovers(
    prefix, failure, tmp_path, monkeypatch, pattern_repo, recorded_dispatcher
):
    client = TestClient(app)
    body = {**INTENT, "inputs": {"filename": "start.txt", "content": SECRET}}
    missing_path = tmp_path / "private-git-path-marker"
    missing_path.mkdir()
    if failure == "not-executable":
        git = missing_path / "git"
        git.write_text("not an executable")
        git.chmod(0o644)
    with monkeypatch.context() as scoped:
        scoped.setenv("PATH", str(missing_path))
        responses = [
            client.get(f"{prefix}/patterns/demo?version=v1.0.0"),
            client.post(f"{prefix}/intents/validate", json=body),
            client.post(
                f"{prefix}/operations", json=body, headers={"Idempotency-Key": "git-startup"}
            ),
        ]
    for response in responses:
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "catalog_unavailable"
        assert response.json()["error"]["detail"] == "pattern repository unavailable"
        assert SECRET not in response.text and str(missing_path) not in response.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        assert [row[0] for row in con.execute("SELECT outcome FROM events")] == ["refused"]
    assert not recorded_dispatcher.pending

    assert client.get(f"{prefix}/patterns/demo?version=v1.0.0").status_code == 200
    assert client.post(f"{prefix}/intents/validate", json=body).status_code == 200
    accepted = client.post(
        f"{prefix}/operations", json=body, headers={"Idempotency-Key": "git-startup"}
    )
    assert accepted.status_code == 202, accepted.text
    assert len(recorded_dispatcher.pending) == 1
