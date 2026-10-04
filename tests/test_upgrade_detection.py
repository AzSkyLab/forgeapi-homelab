"""Resources report the newest pattern tag the caller may use, so agents can offer upgrades."""

import time

import pytest

from app import catalog, policy
from app.settings import settings
from tests.conftest import git
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit
from tests.test_pattern_upgrade import upgradable as upgradable  # noqa: F401


def _apply(client, dispatcher, op):
    assert dispatcher.run_next()
    planned = client.get(op["links"]["self"]).json()
    assert client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert dispatcher.run_next()
    return client.get(op["links"]["self"]).json()


def _deploy(client, dispatcher, key="up", **updates):
    op = submit(client, key, **updates).json()
    assert _apply(client, dispatcher, op)["state"] == "succeeded"
    return op["resource_id"]


def _view(client, resource_id, prefix=""):
    return client.get(f"{prefix}/resources/{resource_id}").json()


def test_upgrade_reported_then_cleared_after_upgrading(
    agent_client,
    recorded_dispatcher,
    upgradable,  # noqa: F811
):
    rid = _deploy(agent_client, recorded_dispatcher, pattern="kept")
    for prefix in ("", "/v1"):
        got = _view(agent_client, rid, prefix)
        assert (got["version"], got["latest_version"], got["upgrade_available"]) == (
            "v1.0.0",
            "v1.1.0",
            True,
        )
    listed = agent_client.get("/v1/resources").json()["items"][0]
    assert listed["latest_version"] == "v1.1.0" and listed["upgrade_available"] is True
    up = submit(agent_client, "up2", pattern="kept", version="v1.1.0", resource_id=rid).json()
    assert _apply(agent_client, recorded_dispatcher, up)["state"] == "succeeded"
    got = _view(agent_client, rid)
    assert (got["version"], got["latest_version"], got["upgrade_available"]) == (
        "v1.1.0",
        "v1.1.0",
        False,
    )


def test_new_tag_appears_only_after_cache_expiry(
    agent_client, recorded_dispatcher, pattern_repo, monkeypatch
):
    rid = _deploy(agent_client, recorded_dispatcher)
    assert _view(agent_client, rid)["latest_version"] == "v1.1.0"
    git(pattern_repo, "tag", "v1.2.0")
    assert _view(agent_client, rid)["latest_version"] == "v1.1.0"  # cached
    now = time.monotonic()
    monkeypatch.setattr(catalog.time, "monotonic", lambda: now + settings.version_cache_seconds + 1)
    assert _view(agent_client, rid)["latest_version"] == "v1.2.0"


def test_cache_disabled_lists_every_time(
    agent_client, recorded_dispatcher, pattern_repo, monkeypatch
):
    rid = _deploy(agent_client, recorded_dispatcher)
    monkeypatch.setattr(settings, "version_cache_seconds", 0)
    assert _view(agent_client, rid)["latest_version"] == "v1.1.0"
    git(pattern_repo, "tag", "v1.2.0")
    assert _view(agent_client, rid)["latest_version"] == "v1.2.0"


def test_destroyed_and_local_patterns_are_unknown(agent_client, recorded_dispatcher):
    local = _deploy(agent_client, recorded_dispatcher, "local", pattern="local-file", version=None)
    got = _view(agent_client, local)
    assert got["latest_version"] is None and got["upgrade_available"] is None
    rid = _deploy(agent_client, recorded_dispatcher, "gone")
    destroy = submit(agent_client, "gone-destroy", action="destroy", resource_id=rid).json()
    assert _apply(agent_client, recorded_dispatcher, destroy)["state"] == "succeeded"
    got = _view(agent_client, rid)
    assert got["state"] == "destroyed"
    assert got["latest_version"] is None and got["upgrade_available"] is None


def test_pending_resource_is_unknown(agent_client, recorded_dispatcher):
    rid = submit(agent_client, "pending").json()["resource_id"]
    got = _view(agent_client, rid)
    assert got["state"] == "pending" and got["upgrade_available"] is None


@pytest.mark.parametrize("failure", ["unreachable", "timeout"])
def test_git_failure_degrades_to_null_and_request_succeeds(
    agent_client, recorded_dispatcher, failure, monkeypatch
):
    rid = _deploy(agent_client, recorded_dispatcher)
    monkeypatch.setattr(settings, "version_cache_seconds", 0)
    if failure == "unreachable":
        catalog_file = settings.catalog_path
        catalog_file.write_text(
            catalog_file.read_text().replace("file://", "file:///nonexistent-upgrade-test")
        )
    else:
        monkeypatch.setattr(catalog, "UPGRADE_GIT_TIMEOUT", 0.001)
        monkeypatch.setattr(catalog, "git_env", lambda token=True: {"PATH": "/nonexistent"})
    response = agent_client.get(f"/resources/{rid}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["latest_version"] is None and body["upgrade_available"] is None
    assert agent_client.get("/resources").status_code == 200


def test_filter_true_and_false(agent_client, recorded_dispatcher, upgradable):  # noqa: F811
    def inputs(name):
        return {"filename": f"{name}.txt", "content": name}

    old = _deploy(agent_client, recorded_dispatcher, "old", pattern="kept", inputs=inputs("a"))
    new = _deploy(
        agent_client,
        recorded_dispatcher,
        "new",
        pattern="kept",
        version="v1.1.0",
        inputs=inputs("b"),
    )
    pending = submit(agent_client, "pend", inputs=inputs("c")).json()["resource_id"]

    def ids(**query):
        return [i["id"] for i in agent_client.get("/v1/resources", params=query).json()["items"]]

    assert ids(upgrade_available="true") == [old]
    assert ids(upgrade_available="false") == [new]
    assert set(ids()) == {old, new, pending}  # unknown never matches a filter


def test_filter_paging_keeps_next_after_valid(agent_client, recorded_dispatcher):
    made = [
        _deploy(
            agent_client,
            recorded_dispatcher,
            f"k{n}",
            inputs={"filename": f"{n}.txt", "content": "x"},
        )
        for n in range(3)
    ]
    seen, after = [], None
    while True:
        params = {"upgrade_available": "true", "limit": 1}
        if after:
            params["after"] = after
        page = agent_client.get("/resources", params=params).json()
        seen += [i["id"] for i in page["items"]]
        after = page["next_after"]
        if not after:
            break
    assert seen == sorted(made)


def test_pattern_the_caller_cannot_use_reveals_no_versions(
    agent_client, recorded_dispatcher, monkeypatch
):
    rid = _deploy(agent_client, recorded_dispatcher)
    assert _view(agent_client, rid)["latest_version"] == "v1.1.0"  # warms the cache
    monkeypatch.setattr(policy, "available", lambda caller: ["local-file"])
    got = _view(agent_client, rid)
    assert got["latest_version"] is None and got["upgrade_available"] is None
    assert (
        agent_client.get("/resources", params={"upgrade_available": "true"}).json()["items"] == []
    )
