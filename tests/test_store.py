"""One contract, run against both stores. The Table store runs against Azurite, Microsoft's
storage emulator, in Docker; it is skipped with a visible reason when Docker is unavailable."""

import shutil
import subprocess
import time
import uuid

import pytest

from app import db, db_table
from app.models import State
from app.settings import settings

# Azurite's published, fixed development credentials. Not a secret; only valid for the emulator.
_AZURITE_KEY = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)


@pytest.fixture(scope="session")
def azurite() -> str:
    if not shutil.which("docker"):
        pytest.skip("Table Storage store NOT tested: docker is required to run Azurite")
    name = f"forgeapi-azurite-{uuid.uuid4().hex[:8]}"
    image = "mcr.microsoft.com/azure-storage/azurite"
    command = ["azurite-table", "--tableHost", "0.0.0.0", "--skipApiVersionCheck"]
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::10002", image, *command],
        check=True, capture_output=True,
    )  # fmt: skip
    try:
        port = subprocess.run(
            ["docker", "port", name, "10002"], check=True, capture_output=True, text=True
        ).stdout.strip().rsplit(":", 1)[1]  # fmt: skip
        endpoint = f"http://127.0.0.1:{port}/devstoreaccount1"
        yield (
            f"DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
            f"AccountKey={_AZURITE_KEY};TableEndpoint={endpoint};"
        )
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


@pytest.fixture(params=["sqlite", "table"])
def store(request, monkeypatch):
    if request.param == "table":
        monkeypatch.setattr(settings, "db_backend", "table")
        monkeypatch.setattr(settings, "table_connection_string", request.getfixturevalue("azurite"))
        monkeypatch.setattr(settings, "table_name", f"t{uuid.uuid4().hex[:12]}")
        for _ in range(40):  # wait for the emulator to accept connections
            try:
                db_table.get("dep_probe")
                break
            except Exception:
                time.sleep(0.25)
    return request.param


def test_create_then_get_round_trips_every_field(store):
    created = db.create("demo", {"name": "x", "tags": {"a": "b"}, "n": 3}, "v1.2.3", "abc123")
    loaded = db.get(created.id)
    assert loaded == created
    assert loaded.state == State.accepted
    assert loaded.version == "v1.2.3" and loaded.commit == "abc123"
    assert loaded.outputs is None and loaded.error is None


def test_unversioned_pattern_has_no_version_or_commit(store):
    loaded = db.get(db.create("local-file", {}).id)
    assert loaded.version is None and loaded.commit is None


def test_missing_record(store):
    assert db.get("dep_missing") is None
    db.update("dep_missing", State.failed, error="x")  # no error, nothing created
    assert db.get("dep_missing") is None


def test_update_keeps_outputs_and_replaces_error(store):
    deployment_id = db.create("demo", {}).id

    db.update(deployment_id, State.failed, error="boom")
    assert db.get(deployment_id).error == "boom"

    db.update(deployment_id, State.succeeded, outputs={"uri": "https://x", "list": [1, 2]})
    done = db.get(deployment_id)
    assert done.state == State.succeeded and done.error is None
    assert done.outputs == {"uri": "https://x", "list": [1, 2]}

    db.update(deployment_id, State.destroying)  # a later state change must not drop outputs
    assert db.get(deployment_id).outputs == {"uri": "https://x", "list": [1, 2]}
    assert db.get(deployment_id).updated_at > done.created_at


def test_respec_changes_what_the_deployment_should_be(store):
    deployment_id = db.create("demo", {"a": 1}, "v1.0.0", "aaa").id
    db.update(deployment_id, State.succeeded, outputs={"kept": True})

    db.respec(deployment_id, {"a": 2, "b": "new"}, "v1.1.0", "bbb")

    changed = db.get(deployment_id)
    assert changed.inputs == {"a": 2, "b": "new"}
    assert (changed.version, changed.commit) == ("v1.1.0", "bbb")
    assert changed.state == State.succeeded and changed.outputs == {"kept": True}
