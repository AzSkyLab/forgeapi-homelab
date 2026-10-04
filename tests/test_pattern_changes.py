"""Pattern changelog and per-environment deployability, over real git repos."""

import pytest
import yaml
from fastapi.testclient import TestClient

from app import ledger
from app.main import app
from app.settings import settings
from tests.conftest import git
from tests.test_resource_labels import run_to_ready

SECRET = "secret-default-value"


@pytest.fixture
def client(recorded_dispatcher):
    return TestClient(app)


@pytest.fixture
def released(pattern_repo):
    """v1.2.0 changes variables; v1.3.0 drops `suffix`."""
    source = pattern_repo / "main.tf"
    text = source.read_text()
    text = text.replace(
        'variable "content" {\n  description = "File content"\n  type        = string\n}',
        'variable "content" {\n  description = "File content"\n  type        = string\n'
        '  default     = "x"\n}',
    )
    assert 'default     = "x"' in text
    text = text.replace(
        'type    = string\n  default = ""', f'type    = string\n  default = "{SECRET}"'
    )
    text += '\nvariable "owner" {\n  description = "Who owns it"\n  type = string\n}\n'
    source.write_text(
        text.replace("type        = string\n\n  validation", "type        = number\n\n  validation")
    )
    git(pattern_repo, "commit", "-qam", "Make content optional; add owner")
    git(pattern_repo, "tag", "v1.2.0")
    source.write_text(
        source.read_text().replace(
            f'variable "suffix" {{\n  type    = string\n  default = "{SECRET}"\n}}', ""
        )
    )
    git(pattern_repo, "commit", "-qam", "Drop suffix")
    git(pattern_repo, "tag", "v1.3.0")
    return pattern_repo


def changes(client, prefix="", **params):
    params = {"from": "v1.0.0", "to": "v1.1.0", **params}
    return client.get(f"{prefix}/patterns/demo/changes", params=params)


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_added_optional_input_and_commit_subjects(client, prefix):
    body = changes(client, prefix).json()
    assert body["name"] == "demo" and body["from"] == "v1.0.0" and body["to"] == "v1.1.0"
    assert body["inputs"]["added"] == [{"name": "suffix", "required": False, "type": "string"}]
    assert body["inputs"]["removed"] == [] and body["inputs"]["changed"] == []
    assert body["new_required_inputs"] == []
    assert [c["subject"] for c in body["commits"]] == ["v1.1.0"]
    assert body["commits"][0]["commit"] == body["to_commit"] or body["to_commit"]
    assert set(body["commits"][0]) == {"commit", "subject"}
    assert "example.invalid" not in str(body)


def test_changed_added_required_and_default_never_exposed(client, released):
    response = changes(client, **{"from": "v1.1.0", "to": "v1.2.0"})
    assert SECRET not in response.text
    body = response.json()
    assert body["inputs"]["added"] == [
        {"name": "owner", "required": True, "type": "string", "description": "Who owns it"}
    ]
    changed = {c["name"]: c["fields"] for c in body["inputs"]["changed"]}
    assert changed["content"] == {"required": [True, False]}
    assert changed["suffix"] == {"default_changed": True}
    assert changed["filename"] == {"type": ["string", "number"]}
    assert body["new_required_inputs"] == ["owner"]


def test_removed_and_optional_to_required(client, released):
    body = changes(client, **{"from": "v1.2.0", "to": "v1.3.0"}).json()
    assert body["inputs"]["removed"] == ["suffix"]
    wide = changes(client, **{"from": "v1.0.0", "to": "v1.3.0"}).json()
    assert [c["subject"] for c in wide["commits"]] == [
        "Drop suffix",
        "Make content optional; add owner",
        "v1.1.0",
    ]
    assert "suffix" not in [a["name"] for a in wide["inputs"]["added"]]
    assert wide["new_required_inputs"] == ["owner"]


def test_optional_becoming_required_is_new_required(client, released):
    source = released / "main.tf"
    source.write_text(source.read_text().replace('  default     = "x"\n', ""))
    git(released, "commit", "-qam", "Content required again")
    git(released, "tag", "v1.4.0")
    body = changes(client, **{"from": "v1.3.0", "to": "v1.4.0"}).json()
    assert body["new_required_inputs"] == ["content"]
    assert body["inputs"]["changed"] == [{"name": "content", "fields": {"required": [False, True]}}]


@pytest.mark.parametrize(
    "params",
    [
        {"to": "v9.9.9"},
        {"from": "v0.0.1"},
        {"from": "not-a-version"},
        {"from": "v1.1.0", "to": "v1.0.0"},
        {"from": "v1.1.0", "to": "v1.1.0"},
    ],
)
def test_bad_tags_and_reversed_range_are_422(client, params):
    assert changes(client, **params).status_code == 422


def test_missing_parameter_is_422_and_unknown_pattern_404(client):
    assert client.get("/patterns/demo/changes", params={"from": "v1.0.0"}).status_code == 422
    assert (
        client.get("/patterns/nope/changes", params={"from": "v1.0.0", "to": "v1.1.0"}).status_code
        == 404
    )


def test_local_pattern_has_no_tags(client):
    response = client.get("/patterns/local-file/changes", params={"from": "v1.0.0", "to": "v1.1.0"})
    assert response.status_code == 422


def tenancy(monkeypatch, units, groups="finance"):
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    monkeypatch.setattr(settings, "dev_groups", groups)


def test_invisible_pattern_is_404(client, monkeypatch):
    tenancy(
        monkeypatch,
        {
            "finance": {
                "groups": ["finance"],
                "patterns": ["local-file"],
                "environments": {"dev": {"subscription_id": "s"}},
            }
        },
    )
    assert changes(client).status_code == 404


def test_injected_inputs_hidden_for_environment(client, monkeypatch, pattern_repo):
    tenancy(
        monkeypatch,
        {
            "finance": {
                "groups": ["finance"],
                "inject": {"suffix": "-fin"},
                "patterns": ["demo"],
                "environments": {"dev": {"subscription_id": "s"}},
            }
        },
    )
    shown = changes(client).json()
    assert [a["name"] for a in shown["inputs"]["added"]] == ["suffix"]
    hidden = changes(client, environment="dev").json()
    assert hidden["inputs"]["added"] == []
    assert changes(client, environment="nope").status_code == 422


CLOUD_CONTRACT = (
    'variable "aws_account_id" { type = string }\nvariable "region" { type = string }\n'
)


@pytest.fixture
def clouds(monkeypatch, pattern_repo):
    source = pattern_repo / "main.tf"
    source.write_text(source.read_text() + "\n" + CLOUD_CONTRACT)
    git(pattern_repo, "commit", "-qam", "cloud contract")
    git(pattern_repo, "tag", "v1.2.0")
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"]["demo"]["cloud"] = "aws"
    catalog["patterns"]["plain"] = {"local": catalog["patterns"]["local-file"]["local"]}
    settings.catalog_path.write_text(yaml.safe_dump(catalog))
    target = {"aws_account_id": "111122223333", "region": "us-east-1"}
    tenancy(
        monkeypatch,
        {
            "finance": {
                "groups": ["finance"],
                "patterns": ["demo", "local-file", "plain"],
                "environments": {
                    "dev": {"targets": {"aws": target}},
                    "prod": {
                        "targets": {
                            "azure": {"subscription_id": "hidden-azure", "region": "westus"}
                        }
                    },
                    "bare": {"subscription_id": "legacy"},
                },
            }
        },
    )


def test_discovery_and_listing_by_environment(client, clouds):
    unit = client.get("/agent").json()["business_units"][0]
    assert unit["patterns"] == ["demo", "local-file", "plain"]
    assert unit["deployable_patterns"] == {
        "dev": ["demo"],
        "prod": [],
        "bare": ["local-file", "plain"],
    }
    assert "111122223333" not in client.get("/agent").text
    names = lambda r: [i["name"] for i in r.json()["items"]]  # noqa: E731
    assert names(client.get("/patterns")) == ["demo", "local-file", "plain"]
    assert names(client.get("/patterns", params={"environment": "dev"})) == ["demo"]
    assert names(client.get("/v1/patterns", params={"environment": "bare"})) == [
        "local-file",
        "plain",
    ]
    assert names(client.get("/patterns", params={"environment": "prod"})) == []
    assert client.get("/v1/agent").json()["capabilities"]["deployable_patterns"] is True


def refused():
    with ledger.connect() as con:
        return con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0]


def intent(environment, **extra):
    return {
        "pattern": "demo",
        "version": "v1.2.0",
        "environment": environment,
        "inputs": {"filename": "a.txt", "content": "c"},
        **extra,
    }


def test_environment_without_cloud_target_is_422_everywhere(client, clouds, recorded_dispatcher):
    validate = client.post("/intents/validate", json=intent("prod"))
    assert validate.status_code == 422
    error = validate.json()["error"]
    assert error["reason"] == "cloud_not_available"
    assert error["detail"] == "environment prod has no aws target"
    before = refused()
    submitted = client.post("/operations", json=intent("prod"), headers={"Idempotency-Key": "k1"})
    assert submitted.status_code == 422
    assert submitted.json()["error"]["reason"] == "cloud_not_available"
    assert refused() == before + 1
    assert not recorded_dispatcher.pending
    # promote: a ready dev resource cannot move to an environment lacking its cloud
    op = client.post("/operations", json=intent("dev"), headers={"Idempotency-Key": "k2"}).json()
    run_to_ready(client, recorded_dispatcher, op)
    before = refused()
    promoted = client.post(
        f"/resources/{op['resource_id']}/promote",
        json={"environment": "prod"},
        headers={"Idempotency-Key": "k3"},
    )
    assert promoted.status_code == 422
    assert promoted.json()["error"]["reason"] == "cloud_not_available"
    assert refused() == before + 1


def test_malformed_target_remains_503(client, clouds, monkeypatch):
    raw = yaml.safe_load(settings.tenants_yaml)
    raw["business_units"]["finance"]["environments"]["dev"]["targets"]["aws"] = {"region": "x"}
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(raw))
    assert client.post("/intents/validate", json=intent("dev")).status_code == 503


def test_half_initialised_history_repo_is_repaired(client, pattern_repo, tmp_path):
    from app import catalog

    cache = settings.data_dir.resolve() / "pattern-cache" / "demo"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "history.git").mkdir()  # a crash between mkdir and git init
    (cache / "history.git" / "junk").write_text("x")
    response = client.get("/patterns/demo/changes", params={"from": "v1.0.0", "to": "v1.1.0"})
    assert response.status_code == 200, response.text
    assert catalog._usable_repo(cache / "history.git")
    assert not [p for p in cache.iterdir() if p.name.startswith("history-")]  # no scratch left
