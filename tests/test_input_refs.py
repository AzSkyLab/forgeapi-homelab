"""Composition: an input taken from another resource's output at acceptance."""

import json

import pytest

from app import ledger
from app.contracts import Intent, OperationError, canonical_intent
from app.main import caller_context
from app.settings import settings
from app.tenants import Caller
from tests.test_operation_policy import placed as placed
from tests.test_operation_policy import request as placed_request
from tests.test_operations import INTENT, submit
from tests.test_operations import agent_client as agent_client
from tests.test_resource_labels import ledger_count, run_to_ready


def ready_source(client, dispatcher, key="a", **updates):
    op = submit(client, key, **updates).json()
    run_to_ready(client, dispatcher, op)
    return op["resource_id"]


def ref(rid, output="content_sha256"):
    return {"content": {"resource_id": rid, "output": output}}


def consumer(client, key, refs, **updates):
    inputs = {"filename": "b.txt"}
    return submit(client, key, inputs=inputs, input_refs=refs, **updates)


@pytest.mark.parametrize("prefix", ["", "/v1"])
def test_ref_value_reaches_terraform_and_resource_shows_refs(
    agent_client, recorded_dispatcher, prefix
):
    rid = ready_source(agent_client, recorded_dispatcher)
    sha = agent_client.get(f"{prefix}/resources/{rid}").json()["outputs"]["content_sha256"]
    body = {"pattern": "demo", "version": "v1.0.0", "inputs": {"filename": "b.txt"}}
    refs = ref(rid)
    op = agent_client.post(
        f"{prefix}/operations",
        json={**body, "input_refs": refs},
        headers={"Idempotency-Key": "b"},
    )
    assert op.status_code == 202, op.text
    op = op.json()
    replay = agent_client.post(
        f"{prefix}/operations",
        json={**body, "input_refs": refs},
        headers={"Idempotency-Key": "b"},
    )
    assert replay.status_code == 202 and replay.json()["id"] == op["id"]
    url = f"{prefix}/resources/{op['resource_id']}"
    assert agent_client.get(url).json()["input_refs"] == refs
    assert recorded_dispatcher.run_next()
    work = settings.data_dir / "deployments" / op["resource_id"] / "work"
    tfvars = json.loads((work / "terraform.tfvars.json").read_text())
    assert tfvars["content"] == sha
    assert agent_client.get(f"{prefix}/resources/{rid}").json()["input_refs"] == {}


def test_reference_errors(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    pending = submit(agent_client, "p").json()["resource_id"]
    before = ledger_count()
    busy = consumer(agent_client, "x1", ref(pending))
    assert busy.status_code == 409
    assert busy.json()["error"]["reason"] == "reference_not_ready"
    missing = consumer(agent_client, "x2", ref(rid, "nope"))
    assert missing.status_code == 422 and "nope" in missing.text
    both = submit(agent_client, "x3", input_refs=ref(rid))
    assert both.status_code == 422
    unknown = consumer(agent_client, "x4", ref("res_" + "0" * 32))
    assert unknown.status_code == 404
    assert ledger_count() == before


def test_other_callers_resource_is_404(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    agent_client.app.dependency_overrides[caller_context] = lambda: Caller("someone-else")
    try:
        assert consumer(agent_client, "x", ref(rid)).status_code == 404
    finally:
        agent_client.app.dependency_overrides.clear()


def test_destroy_rejects_refs(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    before = ledger_count()
    bad = submit(agent_client, "d", action="destroy", resource_id=rid, input_refs=ref(rid))
    assert bad.status_code == 422 and ledger_count() == before


@pytest.mark.parametrize(
    "refs",
    [
        {"content": {"resource_id": "res_x", "output": "path"}},
        {"content": {"resource_id": "res_" + "0" * 32, "output": "1bad"}},
        {"content": {"resource_id": "res_" + "0" * 32, "output": "path", "extra": 1}},
        {f"k{i}": {"resource_id": "res_" + "0" * 32, "output": "path"} for i in range(17)},
    ],
)
def test_invalid_refs_are_refused(agent_client, refs):
    before = ledger_count()
    assert consumer(agent_client, "bad", refs).status_code == 422
    assert ledger_count() == before


def test_fingerprint_of_ref_less_intent_is_unchanged():
    plain = canonical_intent(Intent.model_validate(INTENT))
    assert "input_refs" not in plain and "labels" not in plain
    refs = Intent.model_validate({**INTENT, "input_refs": ref("res_" + "0" * 32)})
    assert ledger.fingerprint(canonical_intent(refs)) != ledger.fingerprint(plain)


def test_cross_environment_reference_is_refused(placed, tmp_path, monkeypatch, recorded_dispatcher):
    import yaml

    units = yaml.safe_load(settings.tenants_yaml)["business_units"]
    envs = units["finance"]["environments"]
    envs["dev"]["budget_monthly"] = 1000
    envs["test"] = {"subscription_id": "hidden-test", "budget_monthly": 1000}
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    op = placed_request(placed, "a").json()
    run_to_ready(placed, recorded_dispatcher, op)
    rid = op["resource_id"]
    same = placed_request(placed, "b", inputs={"filename": "b.txt"}, input_refs=ref(rid))
    assert same.status_code == 202, same.text
    other = placed_request(
        placed, "c", environment="test", inputs={"filename": "c.txt"}, input_refs=ref(rid)
    )
    assert other.status_code == 422
    assert "same business unit" in other.text or "one business unit" in other.text


def test_referenced_location_is_not_overridden_by_region_default(
    placed, pattern_repo, monkeypatch, recorded_dispatcher
):
    import yaml

    from tests.test_operation_policy import git

    source = pattern_repo / "main.tf"
    source.write_text(
        source.read_text()
        + '\nvariable "location" { type = string }\noutput "where" { value = var.location }\n'
    )
    git(pattern_repo, "commit", "-qam", "location")
    git(pattern_repo, "tag", "v1.3.0")
    units = yaml.safe_load(settings.tenants_yaml)["business_units"]
    units["finance"]["regions"] = {"default": "westus"}
    units["finance"]["environments"]["dev"]["budget_monthly"] = 1000
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    first = placed_request(
        placed,
        "a",
        version="v1.3.0",
        inputs={"filename": "a.txt", "content": "a", "location": "eastus"},
    )
    assert first.status_code == 202, first.text
    first = first.json()
    run_to_ready(placed, recorded_dispatcher, first)
    second = placed_request(
        placed,
        "b",
        version="v1.3.0",
        inputs={"filename": "b.txt", "content": "b"},
        input_refs={"location": {"resource_id": first["resource_id"], "output": "where"}},
    )
    assert second.status_code == 202, second.text
    stored = ledger.get(second.json()["id"])
    assert stored["inputs"]["location"] == "eastus"
    assert "location" not in stored["injected"]


def refused_count():
    with ledger.connect() as con:
        return con.execute("SELECT count(*) FROM events WHERE outcome='refused'").fetchone()[0]


def destroy(client, key, rid):
    return submit(client, key, action="destroy", resource_id=rid)


def ready_consumer(client, dispatcher, rid, key="c"):
    op = consumer(client, key, ref(rid)).json()
    run_to_ready(client, dispatcher, op)
    return op["resource_id"]


def test_referenced_source_cannot_be_destroyed_until_consumer_changes(
    agent_client, recorded_dispatcher
):
    rid = ready_source(agent_client, recorded_dispatcher)
    cid = ready_consumer(agent_client, recorded_dispatcher, rid)
    before, events = ledger_count(), refused_count()
    blocked = destroy(agent_client, "d1", rid)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["reason"] == "resource_referenced"
    assert ledger_count() == before and refused_count() == events + 1
    update = submit(
        agent_client, "u", resource_id=cid, inputs={"filename": "b.txt", "content": "x"}
    ).json()
    run_to_ready(agent_client, recorded_dispatcher, update)
    assert agent_client.get(f"/resources/{cid}").json()["input_refs"] == {}
    assert destroy(agent_client, "d2", rid).status_code == 202


def test_source_destroy_allowed_after_consumer_destroyed(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    cid = ready_consumer(agent_client, recorded_dispatcher, rid)
    op = destroy(agent_client, "dc", cid).json()
    run_to_ready(agent_client, recorded_dispatcher, op)
    assert agent_client.get(f"/resources/{cid}").json()["state"] == "destroyed"
    assert destroy(agent_client, "ds", rid).status_code == 202


def test_unapplied_consumer_does_not_block_destroy(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    assert consumer(agent_client, "pending", ref(rid)).status_code == 202
    assert destroy(agent_client, "d", rid).status_code == 202


def test_reference_to_source_being_destroyed_is_refused(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    assert destroy(agent_client, "d", rid).status_code == 202
    before = ledger_count()
    late = consumer(agent_client, "late", ref(rid))
    assert late.status_code == 409
    assert late.json()["error"]["reason"] == "reference_not_ready"
    assert ledger_count() == before


def test_self_reference_does_not_block_own_destroy(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    update = consumer(agent_client, "self", ref(rid), resource_id=rid).json()
    run_to_ready(agent_client, recorded_dispatcher, update)
    assert agent_client.get(f"/resources/{rid}").json()["input_refs"] == ref(rid)
    assert destroy(agent_client, "gone", rid).status_code == 202


def planned_consumer(client, dispatcher, rid, key="pc"):
    op = consumer(client, key, ref(rid)).json()
    assert dispatcher.run_next()
    return client.get(op["links"]["self"]).json()


def execute_plan(client, planned):
    return client.post(planned["links"]["execute"], json={"plan_digest": planned["plan_digest"]})


def test_execute_refused_when_source_destroyed_after_planning(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    planned = planned_consumer(agent_client, recorded_dispatcher, rid)
    gone = destroy(agent_client, "d", rid).json()
    run_to_ready(agent_client, recorded_dispatcher, gone)
    assert agent_client.get(f"/resources/{rid}").json()["state"] == "destroyed"
    blocked = execute_plan(agent_client, planned)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["reason"] == "reference_not_ready"
    assert agent_client.get(planned["links"]["self"]).json()["state"] == "planned"


def test_execute_refused_while_source_destroy_unfinished(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    planned = planned_consumer(agent_client, recorded_dispatcher, rid)
    assert destroy(agent_client, "d", rid).status_code == 202
    blocked = execute_plan(agent_client, planned)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["reason"] == "reference_not_ready"


def test_execute_allowed_when_source_untouched(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    planned = planned_consumer(agent_client, recorded_dispatcher, rid)
    assert execute_plan(agent_client, planned).status_code == 202


def test_accept_rechecks_references_in_its_transaction(agent_client, recorded_dispatcher):
    rid = ready_source(agent_client, recorded_dispatcher)
    ready_consumer(agent_client, recorded_dispatcher, rid)
    source = ledger.resource(rid)
    with pytest.raises(OperationError) as refused:  # a consumer became ready after validation
        ledger.accept(
            "a",
            "race-destroy",
            {"action": "destroy", "resource_id": rid},
            {**source, "previous_operation_id": source["operation_id"]},
            None,
        )
    assert refused.value.reason == "resource_referenced"
    other = ready_source(agent_client, recorded_dispatcher, key="o")
    assert destroy(agent_client, "dd", other).status_code == 202  # destroy now unfinished
    with pytest.raises(OperationError) as refused:
        ledger.accept("a", "race-ref", {"action": "deploy"}, {"input_refs": ref(other)}, None)
    assert refused.value.reason == "reference_not_ready"
