"""Pattern config YAML is a mapping or empty, with safe errors before acceptance."""

import pytest
from fastapi.testclient import TestClient

from app import catalog, ledger
from app.main import app
from tests.conftest import git
from tests.test_operations import INTENT

MARKER = "catalog-config-secret"


@pytest.fixture
def agent_client(recorded_dispatcher):
    return TestClient(app)


def versioned_config(pattern_repo, document):
    if document is not None:
        (pattern_repo / "config.yaml").write_text(document)
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "--allow-empty", "-qm", "config fixture")
    git(pattern_repo, "tag", "v1.2.0")


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize(
    "document",
    [
        f"estimated_costs: [{MARKER}\n",
        f"{MARKER}\n",
        f"- {MARKER}\n",
    ],
)
def test_bad_config_fails_safely_before_admission(
    agent_client, pattern_repo, recorded_dispatcher, prefix, document
):
    versioned_config(pattern_repo, document)
    body = {**INTENT, "version": "v1.2.0"}
    responses = [
        agent_client.get(f"{prefix}/patterns/demo?version=v1.2.0"),
        agent_client.post(f"{prefix}/intents/validate", json=body),
        agent_client.post(
            f"{prefix}/operations", json=body, headers={"Idempotency-Key": "bad-config"}
        ),
    ]
    for response in responses:
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "catalog_unavailable"
        assert MARKER not in response.text and str(pattern_repo) not in response.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        assert [row[0] for row in con.execute("SELECT outcome FROM events")] == ["refused"]
    assert not recorded_dispatcher.pending


@pytest.mark.parametrize("document", [None, "", "null\n", "description: valid config\n"])
def test_empty_and_mapping_configs_keep_existing_behavior(
    agent_client, pattern_repo, recorded_dispatcher, document
):
    versioned_config(pattern_repo, document)
    described = agent_client.get("/v1/patterns/demo?version=v1.2.0")
    assert described.status_code == 200, described.text
    assert described.json()["about"] == (
        {"description": "valid config"} if document and document.startswith("description:") else {}
    )
    body = {**INTENT, "version": "v1.2.0"}
    assert agent_client.post("/v1/intents/validate", json=body).status_code == 200
    submitted = agent_client.post(
        "/v1/operations", json=body, headers={"Idempotency-Key": "good-config"}
    )
    assert submitted.status_code == 202, submitted.text
    assert len(recorded_dispatcher.pending) == 1


@pytest.mark.parametrize("prefix", ["", "/v1"])
@pytest.mark.parametrize("source", ["malformed_hcl", "non_utf8_hcl", "non_utf8_yaml"])
def test_unreadable_pattern_files_fail_safely_before_admission(
    agent_client, pattern_repo, recorded_dispatcher, prefix, source
):
    if source == "malformed_hcl":
        (pattern_repo / "broken.tf").write_text(f'variable "bad" {{\n  type = "{MARKER}\n')
    elif source == "non_utf8_hcl":
        (pattern_repo / "broken.tf").write_bytes(b"\xff" + MARKER.encode())
    else:
        (pattern_repo / "config.yaml").write_bytes(b"\xff" + MARKER.encode())
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "unreadable pattern source")
    git(pattern_repo, "tag", "v1.2.0")
    body = {**INTENT, "version": "v1.2.0"}
    responses = [
        agent_client.get(f"{prefix}/patterns/demo?version=v1.2.0"),
        agent_client.post(f"{prefix}/intents/validate", json=body),
        agent_client.post(
            f"{prefix}/operations", json=body, headers={"Idempotency-Key": "bad-source"}
        ),
    ]
    for response in responses:
        assert response.status_code == 502, response.text
        assert response.json()["error"]["code"] == "catalog_unavailable"
        assert MARKER not in response.text and str(pattern_repo) not in response.text
    with ledger.connect() as con:
        assert con.execute("SELECT count(*) FROM operations").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM resources").fetchone()[0] == 0
        assert [row[0] for row in con.execute("SELECT outcome FROM events")] == ["refused"]
    assert not recorded_dispatcher.pending


@pytest.mark.parametrize("contents", [b'terraform { backend "azurerm" {', b"\xffbad"])
def test_backend_hcl_reader_has_same_safe_parse_boundary(tmp_path, contents):
    (tmp_path / "backend.tf").write_bytes(contents)
    with pytest.raises(catalog.CatalogError):
        catalog.uses_azurerm_backend(tmp_path)
