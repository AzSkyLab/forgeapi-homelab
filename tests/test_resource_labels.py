"""Caller labels: metadata on resources, filterable, never part of Terraform input."""

import json

import pytest

from app import ledger
from app.contracts import Intent, canonical_intent
from app.main import app, caller_context
from app.settings import settings
from app.tenants import Caller
from tests.test_operations import INTENT, submit
from tests.test_operations import agent_client as agent_client

LABELS = {"team": "platform", "cost.center": "cc-42"}


def run_to_ready(client, dispatcher, op):
    assert dispatcher.run_next()
    planned = client.get(op["links"]["self"]).json()
    client.post(op["links"]["execute"], json={"plan_digest": planned["plan_digest"]})
    assert dispatcher.run_next()


def ledger_count():
    with ledger.connect() as con:
        return con.execute("SELECT count(*) FROM operations").fetchone()[0]


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_labels_on_pending_and_ready_resource(agent_client, recorded_dispatcher, prefix):
    op = agent_client.post(
        f"{prefix}/operations",
        json={**INTENT, "labels": LABELS},
        headers={"Idempotency-Key": "create"},
    ).json()
    url = f"{prefix}/resources/{op['resource_id']}"
    assert agent_client.get(url).json()["labels"] == LABELS
    run_to_ready(agent_client, recorded_dispatcher, op)
    ready = agent_client.get(url).json()
    assert ready["state"] == "ready" and ready["labels"] == LABELS
    tfvars = json.loads(
        (
            settings.data_dir / "deployments" / op["resource_id"] / "work" / "terraform.tfvars.json"
        ).read_text()
    )
    assert "labels" not in tfvars and "platform" not in json.dumps(tfvars)


def test_unlabelled_resource_shows_empty_labels(agent_client):
    op = submit(agent_client, "none").json()
    assert agent_client.get(f"/resources/{op['resource_id']}").json()["labels"] == {}


def test_update_keeps_or_replaces_labels(agent_client, recorded_dispatcher):
    op = submit(agent_client, "c", labels=LABELS).json()
    run_to_ready(agent_client, recorded_dispatcher, op)
    rid = op["resource_id"]
    keep = submit(agent_client, "u1", resource_id=rid).json()
    assert agent_client.get(f"/resources/{rid}").json()["labels"] == LABELS
    run_to_ready(agent_client, recorded_dispatcher, keep)
    assert agent_client.get(f"/resources/{rid}").json()["labels"] == LABELS
    new = submit(agent_client, "u2", resource_id=rid, labels={"team": "other"}).json()
    run_to_ready(agent_client, recorded_dispatcher, new)
    assert agent_client.get(f"/resources/{rid}").json()["labels"] == {"team": "other"}
    clear = submit(agent_client, "u3", resource_id=rid, labels={}).json()
    run_to_ready(agent_client, recorded_dispatcher, clear)
    assert agent_client.get(f"/resources/{rid}").json()["labels"] == {}


def test_destroy_rejects_labels_but_keeps_them(agent_client, recorded_dispatcher):
    op = submit(agent_client, "c", labels=LABELS).json()
    run_to_ready(agent_client, recorded_dispatcher, op)
    rid = op["resource_id"]
    before = ledger_count()
    bad = submit(agent_client, "d", action="destroy", resource_id=rid, labels=LABELS)
    assert bad.status_code == 422 and ledger_count() == before
    destroy = submit(agent_client, "d2", action="destroy", resource_id=rid).json()
    run_to_ready(agent_client, recorded_dispatcher, destroy)
    gone = agent_client.get(f"/resources/{rid}").json()
    assert gone["state"] == "destroyed" and gone["labels"] == LABELS


@pytest.mark.parametrize(
    "labels",
    [
        {"Bad": "x"},
        {"1a": "x"},
        {"a" * 64: "x"},
        {"": "x"},
        {"k": "x" * 129},
        {"k": "line\nbreak"},
        {"k": "tab\there"},
        {f"k{i}": "v" for i in range(17)},
        {"k": 1},
        ["k=v"],
    ],
)
def test_invalid_labels_are_refused_before_acceptance(agent_client, labels):
    before = ledger_count()
    assert submit(agent_client, "bad", labels=labels).status_code == 422
    assert ledger_count() == before
    with ledger.connect() as con:
        accepted = "SELECT count(*) FROM events WHERE outcome='accepted'"
        assert con.execute(accepted).fetchone()[0] == 0


def test_limits_are_inclusive(agent_client):
    ok = {"a" * 63: "x" * 128, "b": "", **{f"k{i}": "v" for i in range(14)}}
    assert len(ok) == 16
    assert submit(agent_client, "edge", labels=ok).status_code == 202


def test_fingerprint_of_labelless_intent_is_unchanged():
    material = canonical_intent(Intent.model_validate(INTENT))
    assert "labels" not in material
    assert (
        ledger.fingerprint(material)
        == "80b9798f9068c08419c659a8965a9d245f3f736ca49796b57e5f52d254b78c54"
    )
    labelled = canonical_intent(Intent.model_validate({**INTENT, "labels": LABELS}))
    assert labelled["labels"] == LABELS
    assert ledger.fingerprint(labelled) != ledger.fingerprint(material)


def test_replay_and_conflict_include_labels(agent_client):
    first = submit(agent_client, "k", labels=LABELS)
    assert first.status_code == 202
    again = submit(agent_client, "k", labels=dict(reversed(LABELS.items())))
    assert again.json()["id"] == first.json()["id"]
    assert submit(agent_client, "k", labels={"team": "other"}).status_code == 409
    assert submit(agent_client, "k").status_code == 409


def test_label_filter_composes_and_pages(agent_client, recorded_dispatcher):
    a = submit(agent_client, "a", labels=LABELS).json()
    run_to_ready(agent_client, recorded_dispatcher, a)
    b = submit(agent_client, "b", labels={"team": "platform"}).json()
    c = submit(agent_client, "c", labels={"team": "other"}).json()
    submit(agent_client, "d")

    def ids(query):
        page = agent_client.get(f"/resources?{query}").json()
        return [r["id"] for r in page["items"]], page["next_after"]

    both, _ = ids("label=team=platform")
    assert sorted(both) == sorted([a["resource_id"], b["resource_id"]])
    assert ids("label=team=other")[0] == [c["resource_id"]]
    assert ids("label=team=nobody")[0] == []
    assert ids("label=cost.center=cc-42")[0] == [a["resource_id"]]
    assert ids("label=team=platform&state=ready")[0] == [a["resource_id"]]
    assert ids("label=team=platform&state=destroyed")[0] == []
    first, cursor = ids("label=team=platform&limit=1")
    assert cursor == first[0]
    rest, last = ids(f"label=team=platform&limit=1&after={cursor}")
    assert sorted(first + rest) == sorted(both) and last is None
    assert ids("label=team=platform&pattern=nope")[0] == []
    assert ids("label=team%3D")[0] == []


def test_label_filter_matches_value_containing_equals(agent_client):
    op = submit(agent_client, "eq", labels={"expr": "a=b"}).json()
    page = agent_client.get("/resources?label=expr=a=b").json()
    assert [r["id"] for r in page["items"]] == [op["resource_id"]]


@pytest.mark.parametrize("query", ["label=novalue", "label="])
def test_malformed_label_filter_is_422(agent_client, query):
    assert agent_client.get(f"/resources?{query}").status_code == 422


def test_label_filter_respects_visibility(agent_client):
    submit(agent_client, "mine", labels=LABELS)
    assert len(agent_client.get("/resources?label=team=platform").json()["items"]) == 1
    app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    assert agent_client.get("/resources?label=team=platform").json()["items"] == []
