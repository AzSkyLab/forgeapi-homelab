import yaml
from fastapi.testclient import TestClient

from app import db
from app.main import app, get_dispatcher
from app.models import State
from app.settings import settings
from tests.conftest import git

INPUTS = {"filename": "hello.txt", "content": "hi"}


def _post(client, **body):
    return client.post("/deployments", json={"pattern": "demo", "inputs": INPUTS, **body})


def test_catalog_lists_patterns(client):
    names = [p["name"] for p in client.get("/patterns").json()]
    assert names == ["demo", "local-file"]


def test_pattern_versions_come_from_git_tags_newest_first(client):
    body = client.get("/patterns/demo").json()
    assert body["versions"] == ["v1.1.0", "v1.0.0"]  # non-semver tags ignored
    assert body["version"] == "v1.1.0"


def test_input_schema_is_read_from_the_pattern_at_each_version(client, pattern_repo):
    latest = {v["name"]: v for v in client.get("/patterns/demo").json()["inputs"]}
    old = {v["name"]: v for v in client.get("/patterns/demo?version=v1.0.0").json()["inputs"]}
    assert latest["filename"]["required"] and latest["filename"]["type"] == "string"
    assert latest["suffix"] == {
        "name": "suffix",
        "type": "string",
        "required": False,
        "default": "",
        "description": "",
        "sensitive": False,
        "rules": [],
    }
    assert "suffix" not in old


def test_create_defaults_to_latest_tag_and_records_commit(client, dispatched, pattern_repo):
    created = _post(client)
    assert created.status_code == 202
    body = created.json()
    assert body["state"] == "accepted"
    assert body["version"] == "v1.1.0"
    # Annotated tag: the recorded commit is the commit, not the tag object.
    assert body["commit"] == git(pattern_repo, "rev-parse", "v1.1.0^{commit}")
    assert dispatched == [body["id"]]
    assert client.get(body["links"]["self"]).json()["inputs"] == INPUTS


def test_create_pins_requested_version(client, pattern_repo):
    body = _post(client, version="v1.0.0").json()
    assert body["version"] == "v1.0.0"
    assert body["commit"] == git(pattern_repo, "rev-parse", "v1.0.0")


def test_catalog_can_pin_the_default_version(client, pattern_repo):
    spec = {"patterns": {"demo": {"repo": f"file://{pattern_repo}", "default_version": "v1.0.0"}}}
    settings.catalog_path.write_text(yaml.safe_dump(spec))

    assert _post(client).json()["version"] == "v1.0.0"
    assert _post(client, version="v1.1.0").json()["version"] == "v1.1.0"  # explicit still wins
    described = client.get("/patterns/demo").json()
    assert described["default_version"] == "v1.0.0" and described["version"] == "v1.0.0"
    assert "suffix" not in [v["name"] for v in described["inputs"]]


def test_pinned_default_that_does_not_exist_is_an_error(client, pattern_repo, dispatched):
    spec = {"patterns": {"demo": {"repo": f"file://{pattern_repo}", "default_version": "v7.0.0"}}}
    settings.catalog_path.write_text(yaml.safe_dump(spec))
    assert _post(client).status_code == 422
    assert dispatched == []


def test_inputs_are_validated_against_the_requested_version(client, dispatched):
    with_suffix = {**INPUTS, "suffix": "!"}
    assert _post(client, inputs=with_suffix).status_code == 202
    assert _post(client, inputs=with_suffix, version="v1.0.0").status_code == 422
    assert len(dispatched) == 1


def test_rejects_missing_wrong_type_and_unknown_inputs(client, dispatched):
    for inputs in ({"filename": "a.txt"}, {**INPUTS, "content": ["x"]}, {**INPUTS, "extra": "x"}):
        assert _post(client, inputs=inputs).status_code == 422, inputs
    assert dispatched == []


def test_unknown_pattern_and_version(client, dispatched):
    assert client.post("/deployments", json={"pattern": "nope"}).status_code == 404
    assert _post(client, version="v9.9.9").status_code == 422
    assert client.get("/patterns/nope").status_code == 404
    assert dispatched == []


def test_unreachable_pattern_repo_is_502(client, pattern_repo, dispatched):
    pattern_repo.rename(pattern_repo.with_name("gone"))
    assert _post(client).status_code == 502
    assert dispatched == []


def test_unknown_deployment_is_404(client):
    assert client.get("/deployments/dep_missing").status_code == 404
    assert client.get("/deployments/dep_missing/logs").status_code == 404


def test_logs_empty_before_any_run(client):
    deployment_id = _post(client).json()["id"]
    response = client.get(f"/deployments/{deployment_id}/logs")
    assert response.status_code == 200
    assert response.text == ""


def test_dispatch_failure_marks_deployment_failed():
    async def broken(deployment_id: str, action: str = "deploy") -> None:
        raise ConnectionError("temporal down")

    app.dependency_overrides[get_dispatcher] = lambda: broken
    try:
        response = _post(TestClient(app))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    deployment_id = response.json()["detail"].split()[-2]
    assert db.get(deployment_id).state == State.failed


def test_retry_only_failed_deployments(client, dispatched):
    deployment_id = _post(client).json()["id"]
    assert client.post(f"/deployments/{deployment_id}/retry").status_code == 409

    db.update(deployment_id, State.failed, error="boom")
    retried = client.post(f"/deployments/{deployment_id}/retry")
    assert retried.status_code == 202
    assert retried.json()["state"] == "accepted" and retried.json()["error"] is None
    assert dispatched == [deployment_id, deployment_id]
    assert client.post("/deployments/dep_missing/retry").status_code == 404


def test_destroy_only_settled_deployments(client, dispatched):
    deployment_id = _post(client).json()["id"]
    assert client.delete(f"/deployments/{deployment_id}").status_code == 409  # still accepted

    db.update(deployment_id, State.succeeded, outputs={})
    destroying = client.delete(f"/deployments/{deployment_id}")
    assert destroying.status_code == 202 and destroying.json()["state"] == "destroying"
    assert dispatched[-1] == f"destroy:{deployment_id}"
    assert client.delete(f"/deployments/{deployment_id}").status_code == 409  # already destroying
    assert client.delete("/deployments/dep_missing").status_code == 404


def test_update_changes_inputs_and_version_then_reapplies(client, dispatched, pattern_repo):
    deployment_id = _post(client, version="v1.0.0").json()["id"]
    assert client.put(f"/deployments/{deployment_id}", json={}).status_code == 409  # not settled
    db.update(deployment_id, State.succeeded, outputs={"path": "/x"})

    # `suffix` only exists from v1.1.0: rejected on the current version, accepted with the bump.
    new_inputs = {**INPUTS, "suffix": "!"}
    assert (
        client.put(f"/deployments/{deployment_id}", json={"inputs": new_inputs}).status_code == 422
    )
    updated = client.put(
        f"/deployments/{deployment_id}", json={"version": "v1.1.0", "inputs": new_inputs}
    )

    assert updated.status_code == 202
    body = updated.json()
    assert body["state"] == "accepted" and body["version"] == "v1.1.0"
    assert body["commit"] == git(pattern_repo, "rev-parse", "v1.1.0^{commit}")
    assert body["inputs"] == new_inputs
    assert body["outputs"] == {"path": "/x"}  # kept until the new run replaces them
    assert dispatched == [deployment_id, deployment_id]


def test_update_keeps_inputs_when_only_the_version_changes(client, dispatched):
    deployment_id = _post(client, version="v1.0.0").json()["id"]
    db.update(deployment_id, State.succeeded, outputs={})
    body = client.put(f"/deployments/{deployment_id}", json={"version": "v1.1.0"}).json()
    assert body["inputs"] == INPUTS and body["version"] == "v1.1.0"
    assert client.put("/deployments/dep_missing", json={}).status_code == 404
    assert (
        client.put(f"/deployments/{deployment_id}", json={"version": "v9.9.9"}).status_code == 409
    )


def test_store_outage_is_a_clear_503(client, monkeypatch):
    from azure.core.exceptions import HttpResponseError

    def broken(_):
        raise HttpResponseError("AuthorizationPermissionMismatch: secret-looking detail")

    monkeypatch.setattr(db, "get", broken)
    response = client.get("/deployments/dep_any")
    assert response.status_code == 503
    assert "secret-looking" not in response.text
