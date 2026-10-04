"""App rollouts: replicas, then a router, behind two exact-plan approvals.

Real Terraform (local files) and, for the rollout itself, a real Temporal dev server and the
production worker. Focused tests without Temporal use the recording dispatcher and run plan
phases by hand."""

import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from temporalio.client import WorkflowHistory
from temporalio.worker import Replayer

from app import app_activities, apps, ledger
from app.dispatch import app_teardown_workflow_id, app_workflow_id, workflow_id
from app.main import app
from app.operation_workflow import (
    AppRolloutWorkflow,
    AppTeardownWorkflow,
    OperationPhaseWorkflow,
)
from app.settings import settings
from app.worker import build_worker
from tests.conftest import EXAMPLE, git
from tests.operation_support import phase_done, temporal_api

ROUTER_TF = (
    (EXAMPLE / "main.tf").read_text().split("# No cloud")[0]
    + """
variable "filename" {
  type = string
}

variable "primary" {
  type = string
}

variable "secondary" {
  type = string
}

resource "local_file" "this" {
  filename = "${path.module}/out/${var.filename}"
  content  = "primary=${var.primary}\\nsecondary=${var.secondary}\\n"
}

output "path" { value = abspath(local_file.this.filename) }
"""
)


@pytest.fixture
def router_pattern(tmp_path):
    """A third git-repo pattern whose inputs come from the replicas' outputs."""
    repo = tmp_path / "terraform-pattern-router"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "main.tf").write_text(ROUTER_TF)
    git(repo, "add", "."), git(repo, "commit", "-qm", "v1.0.0"), git(repo, "tag", "v1.0.0")
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"]["router"] = {"repo": f"file://{repo}"}
    settings.catalog_path.write_text(yaml.safe_dump(catalog))


def app_body(name="storefront", **updates):
    body = {
        "name": name,
        "labels": {"team": "web"},
        "replicas": [
            {
                "pattern": "demo",
                "version": "v1.0.0",
                "inputs": {"filename": f"{name}-a.txt", "content": "replica a"},
                "labels": {"cloud": "a"},
            },
            {
                "pattern": "demo",
                "version": "v1.0.0",
                "inputs": {"filename": f"{name}-b.txt", "content": "replica b"},
            },
        ],
        "router": {
            "pattern": "router",
            "version": "v1.0.0",
            "inputs": {"filename": f"{name}-router.txt"},
            "replica_refs": {
                "primary": {"replica": 0, "output": "path"},
                "secondary": {"replica": 1, "output": "path"},
            },
        },
    }
    return {**body, **updates}


def key(value="app-1"):
    return {"Idempotency-Key": value}


def counts():
    with ledger.connect(write=False) as con:
        return tuple(
            con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("operations", "resources", "apps")
        )


def events(where=""):
    with ledger.connect(write=False) as con:
        return [
            (r["action"], r["outcome"])
            for r in con.execute(f"SELECT * FROM events {where} ORDER BY seq")
        ]


def digests(body, state="planned"):
    return {m["operation_id"]: m["plan_digest"] for m in body["members"]}


async def wait_for(api, app_id, state, timeout=90):
    async def poll():
        while True:
            body = (await api.get(f"/apps/{app_id}")).json()
            if body["state"] == state:
                return body
            assert body["state"] not in ("failed", "uncertain") or state == body["state"], body
            await asyncio.sleep(0.3)

    return await asyncio.wait_for(poll(), timeout)


def test_app_rollout_end_to_end_on_real_temporal(router_pattern):
    async def scenario():
        async with temporal_api() as (api, temporal), build_worker(temporal):
            created = await api.post("/apps", json=app_body(), headers=key())
            assert created.status_code == 202, created.text
            started = created.json()
            app_id = started["id"]
            assert app_id.startswith("app_")
            assert started["notice"] and "router's budget" in started["notice"]
            assert started["router"] is None
            assert started["links"] == {
                "self": f"/apps/{app_id}",
                "approve": f"/apps/{app_id}/approve",
            }
            assert [m["role"] for m in started["members"]] == ["replica", "replica"]
            assert created.headers["location"] == f"/apps/{app_id}"
            for member in started["members"]:
                await phase_done(temporal, member["operation_id"], "plan")
            planned = await wait_for(api, app_id, "awaiting_replica_approval")
            assert planned["next_action"] == "approve_with_plan_digests"
            assert all(m["state"] == "planned" and m["plan_digest"] for m in planned["members"])

            # Labels reach the resources through the ordinary acceptance path.
            labels = (await api.get(f"/resources/{planned['members'][0]['resource_id']}")).json()[
                "labels"
            ]
            assert labels == {
                "team": "web",
                "cloud": "a",
                "app": "storefront",
                "app_role": "replica",
            }

            # A wrong digest and a partial set are both refused; nothing is applied.
            good = digests(planned)
            first, second = list(good)
            before = counts()
            swapped = {first: good[second], second: good[first]}
            wrong = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": swapped})
            assert wrong.status_code == 409
            assert wrong.json()["error"]["reason"] == "plan_digest_mismatch"
            partial = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {first: good[first]}}
            )
            assert partial.status_code == 409
            assert partial.json()["error"]["reason"] == "app_gate_mismatch"
            assert counts() == before
            assert [e for e in events() if e[1] == "refused"] == [
                ("POST /apps/{app_id}/approve", "refused")
            ] * 2
            assert not any(action == "operation.execute" for action, _ in events()), (
                "a refused approval executed something"
            )
            for member in planned["members"]:
                assert ledger.get(member["operation_id"])["state"] == "planned"
            assert (await api.get(f"/apps/{app_id}")).json()["state"] == "awaiting_replica_approval"

            # The right digests apply both replicas; the workflow then accepts and plans the router.
            approved = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": good})
            assert approved.status_code == 202, approved.text
            gate = await wait_for(api, app_id, "awaiting_router_approval")
            assert [m["state"] for m in gate["members"]] == ["succeeded", "succeeded"]
            router = gate["router"]
            assert router["role"] == "router" and router["state"] == "planned"
            replica_files = []
            for member in gate["members"]:
                path = Path(
                    (await api.get(f"/resources/{member['resource_id']}")).json()["outputs"]["path"]
                )
                assert path.is_file()
                replica_files.append(path)
            assert replica_files[0].read_text() == "replica a"
            resource = (await api.get(f"/resources/{router['resource_id']}")).json()
            assert resource["input_refs"] == {
                "primary": {"resource_id": gate["members"][0]["resource_id"], "output": "path"},
                "secondary": {"resource_id": gate["members"][1]["resource_id"], "output": "path"},
            }
            assert resource["labels"] == {
                "team": "web",
                "app": "storefront",
                "app_role": "router",
            }

            # Replica operations cannot be approved at gate two.
            stale = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": good})
            assert stale.status_code == 409
            assert stale.json()["error"]["reason"] == "app_gate_mismatch"
            router_digest = {router["operation_id"]: router["plan_digest"]}
            ready = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": router_digest})
            assert ready.status_code == 202, ready.text
            done = await wait_for(api, app_id, "ready")
            assert done["next_action"] == "done" and done["error"] is None
            written = Path(
                (await api.get(f"/resources/{router['resource_id']}")).json()["outputs"]["path"]
            )
            assert written.read_text() == (
                f"primary={replica_files[0]}\nsecondary={replica_files[1]}\n"
            )
            assert [e for e in events(f"WHERE operation_id='{app_id}'")] == [
                ("app.create", "accepted"),
                ("app.approve", "accepted"),
                ("app.approve", "accepted"),
            ]
            # Placement never leaks: no subscription or cloud target anywhere in the App.
            assert "subscription" not in json.dumps(done) and "cloud_target" not in json.dumps(done)
            listed = (await api.get("/apps")).json()
            assert [a["id"] for a in listed["items"]] == [app_id]
            # The rollout workflow ran only read-only activities and never applied anything.
            handle = temporal.get_workflow_handle(app_workflow_id(app_id))
            assert await handle.result() == "ready"

    asyncio.run(scenario())


def test_idempotent_app_submission_and_key_reuse(recorded_dispatcher, router_pattern):
    client = TestClient(app)
    first = client.post("/apps", json=app_body(), headers=key("same"))
    again = client.post("/v1/apps", json=app_body(), headers=key("same"))
    assert first.status_code == again.status_code == 202
    assert first.json()["id"] == again.json()["id"]
    assert again.json()["links"]["self"] == f"/v1/apps/{first.json()['id']}"
    assert counts() == (2, 2, 1)
    different = client.post("/apps", json=app_body(name="other"), headers=key("same"))
    assert different.status_code == 409
    assert different.json()["error"]["reason"] == "idempotency_key_reused"
    assert counts() == (2, 2, 1)
    assert ("app.create", "accepted") in events()
    assert events().count(("app.create", "accepted")) == 1
    # Both replica plans were dispatched exactly once, and the rollout workflow was requested.
    assert len([p for p in recorded_dispatcher.pending]) == 2
    assert (first.json()["id"], "app") in recorded_dispatcher.seen
    # A key is also required, like POST /operations.
    assert client.post("/apps", json=app_body()).status_code == 422


def test_refused_replica_refuses_the_whole_app(recorded_dispatcher, router_pattern):
    client = TestClient(app)
    body = app_body()
    body["replicas"][1]["pattern"] = "no-such-pattern"
    refused = client.post("/apps", json=body, headers=key("bad-pattern"))
    assert refused.status_code == 404
    assert counts() == (0, 0, 0)
    assert events() == [("POST /apps", "refused")]
    assert not recorded_dispatcher.pending
    # Same for an invalid shape: one replica, a dangling reference.
    assert (
        client.post("/apps", json={**app_body(), "replicas": []}, headers=key("k")).status_code
        == 422
    )
    body = app_body()
    body["router"]["replica_refs"]["primary"]["replica"] = 3
    assert client.post("/apps", json=body, headers=key("k2")).status_code == 422
    assert counts() == (0, 0, 0)


def placed(monkeypatch, pattern_repo, budget):
    source = pattern_repo / "main.tf"
    source.write_text(source.read_text() + '\nvariable "cost_center" { type = string }\n')
    (pattern_repo / "config.yaml").write_text("estimated_costs: 60\n")
    git(pattern_repo, "add", ".")
    git(pattern_repo, "commit", "-qm", "placement")
    git(pattern_repo, "tag", "v1.2.0")
    units = {
        name: {
            "groups": [name],
            "inject": {"cost_center": name},
            "patterns": ["demo"],
            "environments": {
                "dev": {"subscription_id": f"hidden-{name}", "budget_monthly": budget}
            },
        }
        for name in ("finance", "hr")
    }
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump({"business_units": units}))
    monkeypatch.setattr(settings, "dev_groups", "finance")


def placed_body():
    body = app_body(environment="dev")
    for replica in body["replicas"]:
        replica["version"] = "v1.2.0"
    body["router"] = {
        "pattern": "demo",
        "version": "v1.2.0",
        "inputs": {"filename": "router.txt"},
        "replica_refs": {"content": {"replica": 0, "output": "path"}},
    }
    return body


def test_budget_refusal_rolls_back_every_replica(recorded_dispatcher, monkeypatch, pattern_repo):
    # Each replica costs 60 of a 100 budget: the first fits, the second does not. Because the
    # replicas are accepted in one transaction, the first one's operation and reservation are
    # rolled back too.
    placed(monkeypatch, pattern_repo, budget=100)
    client = TestClient(app)
    refused = client.post("/apps", json=placed_body(), headers=key("budget"))
    assert refused.status_code == 403
    assert refused.json()["error"]["detail"] == "intent would exceed the estimated monthly budget"
    assert counts() == (0, 0, 0)
    assert events() == [("POST /apps", "refused")]
    assert not recorded_dispatcher.pending
    # With room for both the same body is accepted; no hidden placement in the response.
    monkeypatch.setattr(
        settings,
        "tenants_yaml",
        settings.tenants_yaml.replace("budget_monthly: 100", "budget_monthly: 500"),
    )
    accepted = client.post("/apps", json=placed_body(), headers=key("budget"))
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["business_unit"] == "finance"
    assert "hidden-finance" not in accepted.text


def test_other_callers_cannot_see_an_app(recorded_dispatcher, monkeypatch, pattern_repo):
    placed(monkeypatch, pattern_repo, budget=500)
    client = TestClient(app)
    created = client.post("/apps", json=placed_body(), headers=key("mine")).json()
    assert client.get(f"/apps/{created['id']}").status_code == 200
    assert [a["id"] for a in client.get("/apps").json()["items"]] == [created["id"]]
    monkeypatch.setattr(settings, "dev_groups", "hr")
    assert client.get(f"/apps/{created['id']}").status_code == 404
    assert client.get("/apps").json()["items"] == []
    digests_ = {m["operation_id"]: "0" * 64 for m in created["members"]}
    hidden = client.post(f"/apps/{created['id']}/approve", json={"plan_digests": digests_})
    assert hidden.status_code == 404
    assert client.get("/apps/app_" + "0" * 32).status_code == 404


def run_plans(recorded_dispatcher):
    while recorded_dispatcher.run_next():
        pass


def test_discarded_replica_fails_the_app(recorded_dispatcher, router_pattern):
    client = TestClient(app)
    created = client.post("/apps", json=app_body(), headers=key()).json()
    assert created["state"] == "planning_replicas"
    run_plans(recorded_dispatcher)
    planned = client.get(created["links"]["self"]).json()
    assert planned["state"] == "awaiting_replica_approval"
    discarded = client.post(f"/operations/{planned['members'][0]['operation_id']}/discard")
    assert discarded.status_code == 200
    failed = client.get(created["links"]["self"]).json()
    # The other replica's plan still reserves budget: the response names it and the way out.
    assert failed["state"] == "failed" and failed["next_action"] == "discard_planned_operations"
    assert planned["members"][1]["operation_id"] in failed["error"]
    assert failed["links"]["discard"] == f"/apps/{created['id']}/discard"
    refused = client.post(f"/apps/{created['id']}/approve", json={"plan_digests": digests(planned)})
    assert refused.status_code == 409
    assert refused.json()["error"]["reason"] == "app_gate_mismatch"
    assert ledger.get(planned["members"][1]["operation_id"])["state"] == "planned"


def test_router_activities_accept_idempotently_and_refuse_visibly(
    recorded_dispatcher, router_pattern
):
    client = TestClient(app)

    def applied(name, refs_output="path"):
        body = app_body(name=name)
        body["router"]["replica_refs"]["primary"]["output"] = refs_output
        created = client.post("/apps", json=body, headers=key(name)).json()
        run_plans(recorded_dispatcher)
        planned = client.get(created["links"]["self"]).json()
        approved = client.post(created["links"]["approve"], json={"plan_digests": digests(planned)})
        assert approved.status_code == 202
        run_plans(recorded_dispatcher)
        return created["id"]

    good = applied("good")
    assert app_activities.check_app(good) == "router_needed"
    router_id = app_activities.accept_app_router(good)
    assert router_id.startswith("op_")
    assert app_activities.accept_app_router(good) == router_id  # an activity retry
    assert app_activities.check_app(good) == "planning_router"
    with ledger.connect(write=False) as con:
        assert con.execute("SELECT count(*) FROM apps").fetchone()[0] == 1
    assert [o for o in ledger.get(router_id)["labels"].items()] == [
        ("team", "web"),
        ("app", "good"),
        ("app_role", "router"),
    ]

    bad = applied("bad", refs_output="missing")
    assert app_activities.accept_app_router(bad) == ""
    failed = client.get(f"/apps/{bad}").json()
    assert failed["state"] == "failed" and failed["router"] is None
    assert "router refused" in failed["error"] and "missing" in failed["error"]
    assert app_activities.check_app(bad) == "failed"
    assert ("app.router", "refused") in events(f"WHERE operation_id='{bad}'")


@pytest.mark.parametrize(
    ("replicas", "router", "error", "expected"),
    [
        (["queued", "planning"], None, None, "planning_replicas"),
        (["planned", "planning"], None, None, "planning_replicas"),
        (["planned", "planned"], None, None, "awaiting_replica_approval"),
        (["apply_queued", "planned"], None, None, "applying_replicas"),
        (["applying", "applying"], None, None, "applying_replicas"),
        (["succeeded", "applying"], None, None, "applying_replicas"),
        (["succeeded", "succeeded"], None, None, "planning_router"),
        (["succeeded", "succeeded"], "queued", None, "planning_router"),
        (["succeeded", "succeeded"], "planning", None, "planning_router"),
        (["succeeded", "succeeded"], "planned", None, "awaiting_router_approval"),
        (["succeeded", "succeeded"], "apply_queued", None, "applying_router"),
        (["succeeded", "succeeded"], "applying", None, "applying_router"),
        (["succeeded", "succeeded"], "succeeded", None, "ready"),
        (["failed", "planned"], None, None, "failed"),
        (["succeeded", "succeeded"], "failed", None, "failed"),
        (["succeeded", "succeeded"], None, "router refused", "failed"),
        (["succeeded", "uncertain"], None, None, "uncertain"),
        (["failed", "uncertain"], None, None, "uncertain"),
        (["succeeded", "succeeded"], "uncertain", None, "uncertain"),
    ],
)
def test_state_is_derived_from_member_operations(replicas, router, error, expected):
    record = {
        "members": [{"index": i, "operation_id": f"op{i}"} for i in range(2)],
        "router_operation_id": "router" if router else None,
        "error": error,
    }
    ops = {f"op{i}": {"state": s} for i, s in enumerate(replicas)}
    if router:
        ops["router"] = {"state": router}
    assert apps.derive_state(record, ops) == expected


@pytest.mark.parametrize(
    ("router", "failovers", "expected", "primary"),
    [
        # A failover is a newer router operation: the app walks the router states again.
        ("succeeded", ["queued"], "planning_router", 0),
        ("succeeded", ["planning"], "planning_router", 0),
        ("succeeded", ["planned"], "awaiting_router_approval", 0),
        ("succeeded", ["apply_queued"], "applying_router", 0),
        ("succeeded", ["applying"], "applying_router", 0),
        ("succeeded", ["succeeded"], "ready", 1),
        ("succeeded", ["succeeded", "succeeded"], "ready", 0),
        ("succeeded", ["succeeded", "planned"], "awaiting_router_approval", 1),
        ("succeeded", ["uncertain"], "uncertain", 0),
        # A failed failover plan (discarded, expired) changes nothing that is deployed.
        ("succeeded", ["failed"], "ready", 0),
        ("succeeded", ["succeeded", "failed"], "ready", 1),
        # The rollout's own router failing is still a failed app.
        ("failed", [], "failed", 0),
    ],
)
def test_failover_walks_the_router_states_again(router, failovers, expected, primary):
    ids = ["router", *[f"fo{i}" for i in range(len(failovers))]]
    record = {
        "members": [{"index": i, "operation_id": f"op{i}"} for i in range(2)],
        "router_operation_id": ids[-1],
        "router_operations": ids,
        "router_spec": {"replica_refs": {"primary_x": {"replica": 0, "output": "x"}}},
        # fo0 moves primary to 1, fo1 back to 0
        "failovers": [
            {"operation_id": f"fo{i}", "primary": (i + 1) % 2} for i in range(len(failovers))
        ],
        "error": None,
    }
    ops = {
        "op0": {"state": "succeeded"},
        "op1": {"state": "succeeded"},
        "router": {"state": router},
    }
    ops.update({f"fo{i}": {"state": state} for i, state in enumerate(failovers)})
    assert apps.derive_state(record, ops) == expected
    assert apps.primary_of(record, ops) == primary


@pytest.mark.parametrize(
    ("router", "replicas", "error", "expected"),
    [
        # no router to destroy: straight to the replicas
        (None, None, None, "planning_replica_destroy"),
        ("queued", None, None, "planning_router_destroy"),
        ("planning", None, None, "planning_router_destroy"),
        ("planned", None, None, "awaiting_router_destroy_approval"),
        ("apply_queued", None, None, "destroying_router"),
        ("applying", None, None, "destroying_router"),
        # router gone, replicas not accepted yet / queued / planned / applying / done
        ("succeeded", None, None, "planning_replica_destroy"),
        ("succeeded", ["queued", "planning"], None, "planning_replica_destroy"),
        ("succeeded", ["planned", "planning"], None, "planning_replica_destroy"),
        ("succeeded", ["planned", "planned"], None, "awaiting_replica_destroy_approval"),
        ("succeeded", ["apply_queued", "planned"], None, "destroying_replicas"),
        ("succeeded", ["succeeded", "applying"], None, "destroying_replicas"),
        ("succeeded", ["succeeded", "succeeded"], None, "destroyed"),
        (None, ["succeeded"], None, "destroyed"),
        (None, [], None, "destroyed"),  # nothing was deployed
        # failures and uncertainty stop the teardown
        ("failed", None, None, "failed"),
        ("succeeded", ["failed", "planned"], None, "failed"),
        ("succeeded", None, "replica destroy refused", "failed"),
        ("uncertain", None, None, "uncertain"),
        ("succeeded", ["succeeded", "uncertain"], None, "uncertain"),
        ("succeeded", ["failed", "uncertain"], None, "uncertain"),
    ],
)
def test_teardown_state_is_derived_from_destroy_operations(router, replicas, error, expected):
    record = {
        "members": [{"index": i, "operation_id": f"op{i}"} for i in range(2)],
        "router_operation_id": "router0",
        "error": "an old rollout error that a teardown outlives",
        "teardown": {
            "router": "rd" if router else None,
            "replicas": [f"rp{i}" for i in range(len(replicas))] if replicas is not None else None,
            "error": error,
        },
    }
    ops = {f"op{i}": {"state": "succeeded"} for i in range(2)}
    ops["router0"] = {"state": "succeeded"}
    if router:
        ops["rd"] = {"state": router}
    for i, state in enumerate(replicas or []):
        ops[f"rp{i}"] = {"state": state}
    assert apps.derive_state(record, ops) == expected


def test_old_rows_without_failover_or_teardown_keys_derive_as_before():
    record = {
        "members": [{"index": 0, "operation_id": "a"}, {"index": 1, "operation_id": "b"}],
        "router_operation_id": "r",
        "router_spec": {"replica_refs": {"x": {"replica": 1, "output": "o"}}},
        "error": None,
    }
    ops = {"a": {"state": "succeeded"}, "b": {"state": "succeeded"}, "r": {"state": "succeeded"}}
    assert apps.derive_state(record, ops) == "ready"
    assert apps.router_ops(record) == ["r"] and apps.teardown_ids(record) == []
    assert apps.op_ids(record) == ["a", "b", "r"]
    assert apps.primary_of(record, ops) == 0


def test_workflow_ids_are_deterministic():
    assert app_workflow_id("app_x") == "forgeapi-app_x-rollout"
    assert app_teardown_workflow_id("app_x") == "forgeapi-app_x-teardown"
    # The workflow starts the router's plan child under the id POST /operations would use.
    assert workflow_id("op_x", "plan") == "forgeapi-op_x-plan"


def test_app_rollout_history_replays():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/recovery/app_rollout_history.json").read_text()
    )
    events_ = fixture["history"]["events"]
    assert events_[0]["workflowExecutionStartedEventAttributes"]["workflowType"]["name"] == (
        "AppRolloutWorkflow"
    )
    assert events_[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"
    scheduled = [
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events_
        if e["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
    ]
    # Read-only checks and one idempotent router acceptance; no Terraform activity, no apply.
    assert set(scheduled) == {"check_app", "accept_app_router"}
    assert scheduled.count("accept_app_router") == 1
    assert any(e["eventType"] == "EVENT_TYPE_TIMER_FIRED" for e in events_)
    assert any(
        e["eventType"] == "EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED" for e in events_
    )
    history = WorkflowHistory.from_json(fixture["workflow_id"], fixture["history"])
    asyncio.run(
        Replayer(workflows=[AppRolloutWorkflow, OperationPhaseWorkflow]).replay_workflow(history)
    )


# --- Failover and teardown ---------------------------------------------------------------------

HA_ROUTER_TF = (
    ROUTER_TF.replace('variable "primary"', 'variable "primary_path"')
    .replace('variable "secondary"', 'variable "secondary_path"')
    .replace("${var.primary}", "${var.primary_path}")
    .replace("${var.secondary}", "${var.secondary_path}")
)


@pytest.fixture
def ha_router_pattern(tmp_path):
    """A router whose inputs follow the `primary_*` / `secondary_*` convention."""
    repo = tmp_path / "terraform-pattern-ha-router"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "main.tf").write_text(HA_ROUTER_TF)
    git(repo, "add", "."), git(repo, "commit", "-qm", "v1.0.0"), git(repo, "tag", "v1.0.0")
    catalog = yaml.safe_load(settings.catalog_path.read_text())
    catalog["patterns"]["ha-router"] = {"repo": f"file://{repo}"}
    settings.catalog_path.write_text(yaml.safe_dump(catalog))


def ha_body(name="storefront"):
    body = app_body(name=name)
    body["router"]["pattern"] = "ha-router"
    body["router"]["replica_refs"] = {
        "primary_path": {"replica": 0, "output": "path"},
        "secondary_path": {"replica": 1, "output": "path"},
    }
    return body


async def rolled_out(api, temporal, name="storefront"):
    """Roll an app out to `ready` through the real API, Temporal and Terraform."""
    created = (await api.post("/apps", json=ha_body(name), headers=key(f"roll-{name}"))).json()
    for member in created["members"]:
        await phase_done(temporal, member["operation_id"], "plan")
    planned = await wait_for(api, created["id"], "awaiting_replica_approval")
    approved = await api.post(
        f"/apps/{created['id']}/approve", json={"plan_digests": digests(planned)}
    )
    assert approved.status_code == 202, approved.text
    gate = await wait_for(api, created["id"], "awaiting_router_approval")
    router = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
    assert (
        await api.post(f"/apps/{created['id']}/approve", json={"plan_digests": router})
    ).status_code == 202
    return await wait_for(api, created["id"], "ready")


async def output_path(api, resource_id):
    return Path((await api.get(f"/resources/{resource_id}")).json()["outputs"]["path"])


def test_failover_swaps_primary_and_secondary_behind_an_approval(ha_router_pattern):
    async def scenario():
        async with temporal_api() as (api, temporal), build_worker(temporal):
            ready = await rolled_out(api, temporal)
            app_id, router_resource = ready["id"], ready["router"]["resource_id"]
            first_router_op = ready["router"]["operation_id"]
            replica_ids = [m["resource_id"] for m in ready["members"]]
            files = [await output_path(api, r) for r in replica_ids]
            assert ready["primary"] == 0
            written = await output_path(api, router_resource)
            assert written.read_text() == f"primary={files[0]}\nsecondary={files[1]}\n"

            # Refusals accept nothing: already primary, no such replica, bad shape.
            before = counts()
            noop = await api.post(f"/apps/{app_id}/failover", json={"primary": 0}, headers=key("n"))
            assert noop.status_code == 409
            assert noop.json()["error"]["reason"] == "app_failover_noop"
            beyond = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 2}, headers=key("o")
            )
            assert beyond.status_code == 422
            shape = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 9}, headers=key("s")
            )
            assert shape.status_code == 422
            assert (
                await api.post(f"/apps/{app_id}/failover", json={"primary": 1})
            ).status_code == 422  # Idempotency-Key is required
            assert counts() == before
            assert [e for e in events() if e[1] == "refused"] == [
                ("POST /apps/{app_id}/failover", "refused")
            ] * 4

            # The failover is a router update plan awaiting the ordinary gate.
            started = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo-1")
            )
            assert started.status_code == 202, started.text
            body = started.json()
            assert started.headers["location"] == f"/apps/{app_id}"
            assert body["state"] == "planning_router" and body["primary"] == 0
            router_op = body["router"]["operation_id"]
            assert router_op != first_router_op
            assert counts() == (before[0] + 1, before[1], before[2])
            await phase_done(temporal, router_op, "plan")
            gate = await wait_for(api, app_id, "awaiting_router_approval")
            assert gate["router"]["operation_id"] == router_op
            assert gate["next_action"] == "approve_with_plan_digests"
            assert gate["primary"] == 0  # nothing is deployed yet
            update = ledger.get(router_op)
            assert update["action"] == "deploy"
            assert update["resource_id"] == router_resource
            assert [c["actions"] for c in update["changes"]] == [
                ["delete", "create"]
            ]  # local_file replaces
            assert update["inputs"] == {
                "filename": "storefront-router.txt",
                "primary_path": str(files[1]),
                "secondary_path": str(files[0]),
            }
            assert (await api.get(f"/resources/{router_resource}")).json()["input_refs"][
                "primary_path"
            ]["resource_id"] == replica_ids[0]  # the deployed definition is still the old one
            assert written.read_text() == f"primary={files[0]}\nsecondary={files[1]}\n"

            # While the failover is pending: another failover and a teardown are refused; the
            # same request replays; the same key with another body is a reused key.
            other = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 0}, headers=key("fo-x")
            )
            assert other.status_code == 409 and other.json()["error"]["reason"] == "app_not_ready"
            gated = await api.post(f"/apps/{app_id}/destroy", headers=key("td-x"))
            assert gated.status_code == 409
            assert gated.json()["error"]["reason"] == "app_destroy_unavailable"
            again = await api.post(
                f"/v1/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo-1")
            )
            assert again.status_code == 202 and again.json()["router"]["operation_id"] == router_op
            assert again.headers["location"] == f"/v1/apps/{app_id}"
            reused = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 0}, headers=key("fo-1")
            )
            assert reused.status_code == 409
            assert reused.json()["error"]["reason"] == "idempotency_key_reused"
            assert counts() == (before[0] + 1, before[1], before[2])

            # The existing approve endpoint is the gate; a wrong digest applies nothing.
            wrong = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {router_op: "0" * 64}}
            )
            assert wrong.status_code == 409
            assert wrong.json()["error"]["reason"] == "plan_digest_mismatch"
            digest = {router_op: gate["router"]["plan_digest"]}
            assert (
                await api.post(f"/apps/{app_id}/approve", json={"plan_digests": digest})
            ).status_code == 202
            done = await wait_for(api, app_id, "ready")
            assert done["primary"] == 1 and done["router"]["operation_id"] == router_op
            resource = (await api.get(f"/resources/{router_resource}")).json()
            assert resource["input_refs"] == {
                "primary_path": {"resource_id": replica_ids[1], "output": "path"},
                "secondary_path": {"resource_id": replica_ids[0], "output": "path"},
            }
            assert written.read_text() == f"primary={files[1]}\nsecondary={files[0]}\n"
            # A finished failover still replays instead of being refused as a no-op.
            replay = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo-1")
            )
            assert replay.status_code == 202
            assert replay.json()["router"]["operation_id"] == router_op

            # And back again.
            back = await api.post(
                f"/apps/{app_id}/failover", json={"primary": 0}, headers=key("fo-2")
            )
            assert back.status_code == 202, back.text
            await phase_done(temporal, back.json()["router"]["operation_id"], "plan")
            gate = await wait_for(api, app_id, "awaiting_router_approval")
            digest = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
            assert (
                await api.post(f"/apps/{app_id}/approve", json={"plan_digests": digest})
            ).status_code == 202
            done = await wait_for(api, app_id, "ready")
            assert done["primary"] == 0
            assert written.read_text() == f"primary={files[0]}\nsecondary={files[1]}\n"
            assert [e for e in events(f"WHERE operation_id='{app_id}'")] == [
                ("app.create", "accepted"),
                ("app.approve", "accepted"),
                ("app.approve", "accepted"),
                ("app.failover", "accepted"),
                ("app.approve", "accepted"),
                ("app.failover", "accepted"),
                ("app.approve", "accepted"),
            ]
            assert "subscription" not in json.dumps(done) and "cloud_target" not in json.dumps(done)

    asyncio.run(scenario())


def test_discarded_failover_leaves_the_app_ready_on_the_old_primary(
    recorded_dispatcher, ha_router_pattern
):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    started = client.post(f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo"))
    assert started.status_code == 202
    assert started.json()["state"] == "planning_router"
    run_plans(recorded_dispatcher)
    planned = client.get(f"/apps/{app_id}").json()
    assert planned["state"] == "awaiting_router_approval"
    assert (
        client.post(f"/operations/{planned['router']['operation_id']}/discard").status_code == 200
    )
    back = client.get(f"/apps/{app_id}").json()
    assert back["state"] == "ready" and back["primary"] == 0
    assert back["router"]["state"] == "succeeded"
    # The same key still replays the discarded request (accepting nothing); a new key retries.
    before = counts()
    replay = client.post(f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo"))
    assert replay.status_code == 202 and counts() == before
    retry = client.post(f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo-2"))
    assert retry.status_code == 202 and retry.json()["state"] == "planning_router"


def test_failover_needs_a_primary_ref_and_ready_replicas(recorded_dispatcher, router_pattern):
    client = TestClient(app)
    # The router here uses `primary`/`secondary` (no prefix): there is nothing to swap.
    app_id = ready_app(client, recorded_dispatcher, body=app_body())
    unsupported = client.post(f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("u"))
    assert unsupported.status_code == 409
    assert unsupported.json()["error"]["reason"] == "app_failover_unsupported"
    assert client.get(f"/apps/{app_id}").json()["state"] == "ready"


def ready_app(client, recorder, body=None, name="storefront"):
    """Drive an app to `ready` with the recording dispatcher and the activities by hand."""
    created = client.post("/apps", json=body or ha_body(name), headers=key(f"r-{name}")).json()
    app_id = created["id"]
    run_plans(recorder)
    planned = client.get(f"/apps/{app_id}").json()
    assert (
        client.post(f"/apps/{app_id}/approve", json={"plan_digests": digests(planned)}).status_code
        == 202
    )
    run_plans(recorder)
    # What the rollout workflow does: accept the router, then start its plan phase.
    recorder.pending.append((app_activities.accept_app_router(app_id), "plan"))
    run_plans(recorder)
    gate = client.get(f"/apps/{app_id}").json()
    assert gate["state"] == "awaiting_router_approval"
    router = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
    assert client.post(f"/apps/{app_id}/approve", json={"plan_digests": router}).status_code == 202
    run_plans(recorder)
    assert client.get(f"/apps/{app_id}").json()["state"] == "ready"
    return app_id


def test_app_teardown_end_to_end_on_real_temporal(ha_router_pattern):
    async def scenario():
        async with temporal_api() as (api, temporal), build_worker(temporal):
            ready = await rolled_out(api, temporal)
            app_id, router_resource = ready["id"], ready["router"]["resource_id"]
            replica_ids = [m["resource_id"] for m in ready["members"]]
            paths = [await output_path(api, r) for r in [router_resource, *replica_ids]]
            assert all(p.is_file() for p in paths)

            # A direct destroy of a replica is refused: the app owns its members.
            direct = await api.post(
                "/operations",
                json={"action": "destroy", "resource_id": replica_ids[0], "pattern": "demo"},
                headers=key("direct"),
            )
            assert direct.status_code == 409
            assert direct.json()["error"]["reason"] == "app_member"

            started = await api.post(f"/apps/{app_id}/destroy", headers=key("td-1"))
            assert started.status_code == 202, started.text
            body = started.json()
            assert started.headers["location"] == f"/apps/{app_id}"
            assert body["state"] == "planning_router_destroy"
            assert [m["role"] for m in body["teardown"]] == ["router_destroy"]
            router_op = body["teardown"][0]["operation_id"]
            assert ledger.get(router_op)["action"] == "destroy"
            assert ledger.get(router_op)["resource_id"] == router_resource
            await phase_done(temporal, router_op, "plan")
            gate = await wait_for(api, app_id, "awaiting_router_destroy_approval")
            assert gate["next_action"] == "approve_with_plan_digests"
            router_digest = gate["teardown"][0]["plan_digest"]
            assert router_digest

            # Replays and a second teardown request accept nothing more.
            before = counts()
            replay = await api.post(f"/v1/apps/{app_id}/destroy", headers=key("td-1"))
            assert replay.status_code == 202
            assert replay.json()["teardown"][0]["operation_id"] == router_op
            other = await api.post(f"/apps/{app_id}/destroy", headers=key("td-2"))
            # An unfinished teardown is resumed (its workflow is re-dispatched), not duplicated.
            assert other.status_code == 202
            assert other.json()["teardown"][0]["operation_id"] == router_op
            assert counts() == before

            # The gate takes exactly the router's destroy: not the replicas, not a wrong digest.
            members = {m["operation_id"]: "0" * 64 for m in gate["members"]}
            wrong_set = await api.post(f"/apps/{app_id}/approve", json={"plan_digests": members})
            assert wrong_set.status_code == 409
            assert wrong_set.json()["error"]["reason"] == "app_gate_mismatch"
            wrong = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {router_op: "0" * 64}}
            )
            assert wrong.status_code == 409
            assert wrong.json()["error"]["reason"] == "plan_digest_mismatch"
            assert all(p.is_file() for p in paths)
            assert ledger.get(router_op)["state"] == "planned"

            approved = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {router_op: router_digest}}
            )
            assert approved.status_code == 202, approved.text
            gate = await wait_for(api, app_id, "awaiting_replica_destroy_approval")
            assert not paths[0].exists(), "the router goes first"
            assert paths[1].is_file() and paths[2].is_file()
            assert (await api.get(f"/resources/{router_resource}")).json()["state"] == "destroyed"
            assert [m["role"] for m in gate["teardown"]] == [
                "router_destroy",
                "replica_destroy",
                "replica_destroy",
            ]
            replicas = {m["operation_id"]: m["plan_digest"] for m in gate["teardown"][1:]}
            assert [m["index"] for m in gate["teardown"][1:]] == [0, 1]
            assert all(replicas.values())

            # The router's destroy cannot be approved again as the replicas' gate.
            stale = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {router_op: router_digest}}
            )
            assert stale.status_code == 409
            assert stale.json()["error"]["reason"] == "app_gate_mismatch"
            first = next(iter(replicas))
            partial = await api.post(
                f"/apps/{app_id}/approve", json={"plan_digests": {first: replicas[first]}}
            )
            assert partial.status_code == 409
            assert partial.json()["error"]["reason"] == "app_gate_mismatch"
            assert paths[1].is_file() and paths[2].is_file()

            assert (
                await api.post(f"/apps/{app_id}/approve", json={"plan_digests": replicas})
            ).status_code == 202
            done = await wait_for(api, app_id, "destroyed")
            assert done["next_action"] == "done" and done["error"] is None
            assert not any(p.exists() for p in paths)
            for resource_id in [router_resource, *replica_ids]:
                assert (await api.get(f"/resources/{resource_id}")).json()["state"] == "destroyed"
            assert (
                await api.post(f"/apps/{app_id}/destroy", headers=key("td-3"))
            ).status_code == 409
            assert (
                await api.post(f"/apps/{app_id}/destroy", headers=key("td-1"))
            ).status_code == 202
            handle = temporal.get_workflow_handle(app_teardown_workflow_id(app_id))
            assert await handle.result() == "destroyed"
            assert [e for e in events(f"WHERE operation_id='{app_id}'")] == [
                ("app.create", "accepted"),
                ("app.approve", "accepted"),
                ("app.approve", "accepted"),
                ("app.destroy", "accepted"),
                ("app.approve", "accepted"),
                ("app.approve", "accepted"),
            ]
            assert "subscription" not in json.dumps(done) and "cloud_target" not in json.dumps(done)
            assert (await api.get("/apps")).json()["items"][0]["state"] == "destroyed"
            if os.environ.get("FORGEAPI_CAPTURE_TEARDOWN"):  # how the replay fixture was made
                history = await handle.fetch_history()
                Path(os.environ["FORGEAPI_CAPTURE_TEARDOWN"]).write_text(
                    json.dumps(
                        {"workflow_id": handle.id, "history": history.to_json_dict()}, indent=2
                    )
                )

    asyncio.run(scenario())


def test_teardown_in_lock_step_and_guardrails(recorded_dispatcher, monkeypatch, pattern_repo):
    placed(monkeypatch, pattern_repo, budget=500)
    client = TestClient(app)
    created = client.post("/apps", json=placed_body(), headers=key("p")).json()
    app_id = created["id"]
    run_plans(recorded_dispatcher)
    planned = client.get(f"/apps/{app_id}").json()
    client.post(f"/apps/{app_id}/approve", json={"plan_digests": digests(planned)})
    run_plans(recorded_dispatcher)
    recorded_dispatcher.pending.append((app_activities.accept_app_router(app_id), "plan"))
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    router = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
    client.post(f"/apps/{app_id}/approve", json={"plan_digests": router})
    run_plans(recorded_dispatcher)
    assert client.get(f"/apps/{app_id}").json()["state"] == "ready"

    # An environment that forbids destroy refuses: audited, nothing accepted, nothing dispatched.
    allowing = settings.tenants_yaml
    units = yaml.safe_load(allowing)
    for unit in units["business_units"].values():
        unit["environments"]["dev"]["allow_destroy"] = False
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))
    before, queued = counts(), len(recorded_dispatcher.pending)
    denied = client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    assert denied.status_code == 403
    assert denied.json()["error"]["reason"] == "policy_denied"
    assert counts() == before and len(recorded_dispatcher.pending) == queued
    assert ("POST /apps/{app_id}/destroy", "refused") in events()
    assert ("app.destroy", "accepted") not in events()
    assert client.get(f"/apps/{app_id}").json()["state"] == "ready"

    # Allowed again: router first; the replicas are not accepted until it has succeeded.
    monkeypatch.setattr(settings, "tenants_yaml", allowing)
    started = client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    assert started.status_code == 202, started.text
    assert (app_id, "teardown") in recorded_dispatcher.seen
    assert counts()[0] == before[0] + 1
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    assert gate["state"] == "awaiting_router_destroy_approval"
    assert app_activities.check_teardown(app_id) == "awaiting_router_destroy_approval"
    router_destroy = gate["teardown"][0]
    approve = client.post(
        f"/apps/{app_id}/approve",
        json={"plan_digests": {router_destroy["operation_id"]: router_destroy["plan_digest"]}},
    )
    assert approve.status_code == 202
    run_plans(recorded_dispatcher)
    assert app_activities.check_teardown(app_id) == "replicas_needed"
    accepted = app_activities.accept_teardown_replica_destroys(app_id)
    assert accepted["refused"] is False and len(accepted["operations"]) == 2
    assert app_activities.accept_teardown_replica_destroys(app_id) == accepted  # a retry
    assert counts()[0] == before[0] + 3
    for operation_id in accepted["operations"]:
        recorded_dispatcher.pending.append((operation_id, "plan"))
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    assert gate["state"] == "awaiting_replica_destroy_approval"
    replicas = {m["operation_id"]: m["plan_digest"] for m in gate["teardown"][1:]}
    assert set(replicas) == set(accepted["operations"])

    # Guardrails apply again at the gate: the environment now forbids destroy.
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))
    blocked = client.post(f"/apps/{app_id}/approve", json={"plan_digests": replicas})
    assert blocked.status_code == 403
    assert blocked.json()["error"]["reason"] == "policy_denied"
    assert all(ledger.get(i)["state"] == "planned" for i in replicas)
    monkeypatch.setattr(settings, "tenants_yaml", allowing)
    assert (
        client.post(f"/apps/{app_id}/approve", json={"plan_digests": replicas}).status_code == 202
    )
    run_plans(recorded_dispatcher)
    done = client.get(f"/apps/{app_id}").json()
    assert done["state"] == "destroyed" and done["next_action"] == "done"
    assert app_activities.check_teardown(app_id) == "destroyed"


def test_failed_destroy_fails_the_teardown_and_can_be_retried(
    recorded_dispatcher, ha_router_pattern
):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    client.post(f"/apps/{app_id}/destroy", headers=key("td-1"))
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    router_op = gate["teardown"][0]["operation_id"]
    assert client.post(f"/operations/{router_op}/discard").status_code == 200
    failed = client.get(f"/apps/{app_id}").json()
    assert failed["state"] == "failed" and failed["next_action"] == "inspect_failure"
    assert app_activities.check_teardown(app_id) == "failed"
    # Nothing is in flight, so a new teardown (new key) starts over on the still-deployed router.
    retry = client.post(f"/apps/{app_id}/destroy", headers=key("td-2"))
    assert retry.status_code == 202
    assert retry.json()["state"] == "planning_router_destroy"
    assert retry.json()["teardown"][0]["operation_id"] != router_op


def test_replica_destroy_refusal_is_recorded_on_the_teardown(
    recorded_dispatcher, ha_router_pattern, monkeypatch
):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    op = gate["teardown"][0]
    client.post(
        f"/apps/{app_id}/approve", json={"plan_digests": {op["operation_id"]: op["plan_digest"]}}
    )
    run_plans(recorded_dispatcher)
    monkeypatch.setattr(
        apps.policy,
        "validate",
        lambda *_: (_ for _ in ()).throw(apps.HTTPException(409, "resource changed")),
    )
    assert app_activities.accept_teardown_replica_destroys(app_id) == {
        "operations": [],
        "refused": True,
    }
    failed = client.get(f"/apps/{app_id}").json()
    assert failed["state"] == "failed"
    assert "replica destroy refused" in failed["error"]
    assert ("app.teardown", "refused") in events(f"WHERE operation_id='{app_id}'")


def test_app_teardown_history_replays():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/recovery/app_teardown_history.json").read_text()
    )
    events_ = fixture["history"]["events"]
    assert events_[0]["workflowExecutionStartedEventAttributes"]["workflowType"]["name"] == (
        "AppTeardownWorkflow"
    )
    assert events_[-1]["eventType"] == "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED"
    scheduled = [
        e["activityTaskScheduledEventAttributes"]["activityType"]["name"]
        for e in events_
        if e["eventType"] == "EVENT_TYPE_ACTIVITY_TASK_SCHEDULED"
    ]
    # Read-only checks and one idempotent acceptance of the replica destroys: no Terraform
    # activity, no apply; the applies happen only through the approval gates.
    assert set(scheduled) == {"check_teardown", "accept_teardown_replica_destroys"}
    assert scheduled.count("accept_teardown_replica_destroys") == 1
    assert any(e["eventType"] == "EVENT_TYPE_TIMER_FIRED" for e in events_)
    assert (
        sum(
            e["eventType"] == "EVENT_TYPE_START_CHILD_WORKFLOW_EXECUTION_INITIATED" for e in events_
        )
        == 2
    )
    history = WorkflowHistory.from_json(fixture["workflow_id"], fixture["history"])
    asyncio.run(
        Replayer(workflows=[AppTeardownWorkflow, OperationPhaseWorkflow]).replay_workflow(history)
    )


# --- Review fixes: liveness, recovery, authorization, namespaces -------------------------------


def placed_ready(client, recorder, monkeypatch, pattern_repo, budget=500):
    """A tenancy-enabled app driven to `ready` by hand."""
    placed(monkeypatch, pattern_repo, budget=budget)
    app_id = client.post("/apps", json=placed_body(), headers=key("p")).json()["id"]
    run_plans(recorder)
    planned = client.get(f"/apps/{app_id}").json()
    client.post(f"/apps/{app_id}/approve", json={"plan_digests": digests(planned)})
    run_plans(recorder)
    recorder.pending.append((app_activities.accept_app_router(app_id), "plan"))
    run_plans(recorder)
    gate = client.get(f"/apps/{app_id}").json()
    router = {gate["router"]["operation_id"]: gate["router"]["plan_digest"]}
    client.post(f"/apps/{app_id}/approve", json={"plan_digests": router})
    run_plans(recorder)
    assert client.get(f"/apps/{app_id}").json()["state"] == "ready"
    return app_id


def revoke_requester(monkeypatch):
    units = yaml.safe_load(settings.tenants_yaml)
    units["business_units"]["finance"]["groups"] = ["somebody-else"]
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))


def at_router_needed(client, recorder, name="router-wait"):
    created = client.post("/apps", json=app_body(name=name), headers=key(name)).json()
    run_plans(recorder)
    planned = client.get(created["links"]["self"]).json()
    client.post(created["links"]["approve"], json={"plan_digests": digests(planned)})
    run_plans(recorder)
    assert app_activities.check_app(created["id"]) == "router_needed"
    return created["id"]


def test_rollout_check_reports_ready_once_its_router_succeeded_and_ignores_later_work(
    recorded_dispatcher, ha_router_pattern
):
    """The rollout workflow must stop once the app is ready, even if a failover or teardown has
    already moved the derived state on; and a late failure write cannot fail a healthy app."""
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    assert app_activities.check_app(app_id) == "ready"
    failover = client.post(f"/apps/{app_id}/failover", json={"primary": 1}, headers=key("fo"))
    assert failover.json()["state"] == "planning_router"
    assert app_activities.check_app(app_id) == "ready"  # the rollout's own router is deployed
    app_activities.fail_app(app_id, "rollout timed out waiting for the members to finish")
    assert ledger.get_app(app_id)["error"] is None
    assert client.get(f"/apps/{app_id}").json()["state"] == "planning_router"
    run_plans(recorded_dispatcher)
    gate = client.get(f"/apps/{app_id}").json()
    client.post(f"/operations/{gate['router']['operation_id']}/discard")  # failover abandoned
    assert client.get(f"/apps/{app_id}").json()["state"] == "ready"
    assert client.post(f"/apps/{app_id}/destroy", headers=key("td")).status_code == 202
    assert app_activities.check_app(app_id) == "superseded"


def test_a_user_key_cannot_collide_with_a_system_key(recorded_dispatcher, router_pattern):
    client = TestClient(app)
    app_id = at_router_needed(client, recorded_dispatcher)
    # The key the router used to be accepted under, now taken by the same caller's own request.
    squatter = {
        "pattern": "demo",
        "version": "v1.0.0",
        "inputs": {"filename": "x.txt", "content": "squat"},
    }
    taken = client.post("/operations", json=squatter, headers=key(f"{app_id}:router"))
    assert taken.status_code == 202
    assert app_activities.accept_app_router(app_id).startswith("op_")
    assert ledger.get_app(app_id)["error"] is None
    # System keys use a separator `Idempotency-Key` cannot carry.
    assert "|" in ledger.system_key(app_id, "router")
    assert client.post("/operations", json=squatter, headers=key("a|b")).status_code == 422


def test_members_cannot_be_changed_outside_the_app(recorded_dispatcher, ha_router_pattern):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    body = client.get(f"/apps/{app_id}").json()
    replica, router_resource = body["members"][0]["resource_id"], body["router"]["resource_id"]
    before = counts()
    for index, resource_id in enumerate((replica, router_resource)):
        for name, intent in (
            ("destroy", {"action": "destroy", "resource_id": resource_id, "pattern": "demo"}),
            ("update", {"resource_id": resource_id, "pattern": "demo", "inputs": {}}),
        ):
            refused = client.post("/operations", json=intent, headers=key(f"{name}{index}"))
            assert refused.status_code == 409, (name, refused.text)
            error = refused.json()["error"]
            assert error["reason"] == "app_member" and app_id in error["detail"]
            assert error["next_action"] == "use_app_endpoints"
    upgrade = client.post(
        f"/resources/{replica}/upgrade", json={"version": "v1.0.0"}, headers=key("up")
    )
    assert upgrade.json()["error"]["reason"] == "app_member"
    assert counts() == before
    # A drift check is not a change and stays allowed.
    assert client.post(f"/resources/{replica}/drift-check", headers=key("dc")).status_code == 202
    # An unrelated resource is unaffected.
    free = client.post(
        "/operations",
        json={"pattern": "demo", "inputs": {"filename": "free.txt", "content": "c"}},
        headers=key("free"),
    )
    assert free.status_code == 202


def test_destroy_replays_any_earlier_key_without_starting_a_new_teardown(
    recorded_dispatcher, ha_router_pattern
):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    first = client.post(f"/apps/{app_id}/destroy", headers=key("td-1")).json()
    first_router = first["teardown"][0]["operation_id"]
    run_plans(recorded_dispatcher)
    assert client.post(f"/operations/{first_router}/discard").status_code == 200
    second = client.post(f"/apps/{app_id}/destroy", headers=key("td-2")).json()
    second_router = second["teardown"][0]["operation_id"]
    assert second_router != first_router
    before = counts()
    replay = client.post(f"/apps/{app_id}/destroy", headers=key("td-1"))
    assert replay.status_code == 202
    assert counts() == before  # nothing new, however many teardowns came since
    assert client.get(f"/apps/{app_id}").json()["teardown"][0]["operation_id"] == second_router
    assert events(f"WHERE operation_id='{app_id}'").count(("app.destroy", "accepted")) == 2
    assert ledger.get_app(app_id)["teardown"]["router"] == second_router
    assert apps.known_request(
        ledger.get_app(app_id)["teardown_history"],
        "local",
        "td-1",
        {"app_id": app_id, "action": "destroy"},
    )["router"] == first_router


def test_destroy_resumes_an_unfinished_teardown_and_rejects_started_ones_politely(
    recorded_dispatcher, ha_router_pattern
):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    started = client.post(f"/apps/{app_id}/destroy", headers=key("td-1")).json()
    router_op = started["teardown"][0]["operation_id"]
    run_plans(recorded_dispatcher)
    recorded_dispatcher.seen.clear()
    before = counts()
    # The workflow is gone: any key re-dispatches it and accepts nothing new.
    again = client.post(f"/apps/{app_id}/destroy", headers=key("recover"))
    assert again.status_code == 202, again.text
    assert (app_id, "teardown") in recorded_dispatcher.seen
    assert counts() == before
    # Approve the router destroy, then make a replica destroy uncertain and reconcile it.
    gate = client.get(f"/apps/{app_id}").json()
    op = gate["teardown"][0]
    client.post(
        f"/apps/{app_id}/approve", json={"plan_digests": {op["operation_id"]: op["plan_digest"]}}
    )
    run_plans(recorded_dispatcher)
    accepted = app_activities.accept_teardown_replica_destroys(app_id)
    victim = accepted["operations"][0]
    with ledger.connect() as con:
        found = con.execute("SELECT body FROM operations WHERE id=?", (victim,)).fetchone()
        row = json.loads(found[0])
        row["state"], row["plan_digest"] = "uncertain", "digest"
        ledger._save(con, row)
    assert client.get(f"/apps/{app_id}").json()["state"] == "uncertain"
    recorded_dispatcher.seen.clear()
    resumed = client.post(f"/apps/{app_id}/destroy", headers=key("recover-2"))
    assert resumed.status_code == 202
    assert (app_id, "teardown") in recorded_dispatcher.seen  # even while uncertain
    assert router_op == ledger.get_app(app_id)["teardown"]["router"]


def test_busy_replica_is_retried_not_refused(recorded_dispatcher, ha_router_pattern, monkeypatch):
    client = TestClient(app)
    app_id = ready_app(client, recorded_dispatcher)
    client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    run_plans(recorded_dispatcher)
    op = client.get(f"/apps/{app_id}").json()["teardown"][0]
    client.post(
        f"/apps/{app_id}/approve", json={"plan_digests": {op["operation_id"]: op["plan_digest"]}}
    )
    run_plans(recorded_dispatcher)
    real = ledger.accept_teardown_replicas

    def busy(*_):
        raise apps.OperationError(409, "resource has an active operation", "resource_busy")

    monkeypatch.setattr(ledger, "accept_teardown_replicas", busy)
    assert app_activities.accept_teardown_replica_destroys(app_id) == {
        "operations": [],
        "refused": False,
        "retry": True,
    }
    assert ledger.get_app(app_id)["teardown"]["error"] is None
    assert app_activities.check_teardown(app_id) == "replicas_needed"
    monkeypatch.setattr(ledger, "accept_teardown_replicas", real)  # the drift check finished
    done = app_activities.accept_teardown_replica_destroys(app_id)
    assert done["refused"] is False and len(done["operations"]) == 2


def test_busy_router_is_retried_not_refused(recorded_dispatcher, router_pattern, monkeypatch):
    client = TestClient(app)
    app_id = at_router_needed(client, recorded_dispatcher)
    real = apps.policy.validate

    def busy(*_):
        raise apps.OperationError(409, "resource has an active operation", "resource_busy")

    monkeypatch.setattr(apps.policy, "validate", busy)
    assert app_activities.accept_app_router(app_id) == "retry"
    assert ledger.get_app(app_id)["error"] is None
    monkeypatch.setattr(apps.policy, "validate", real)
    assert app_activities.accept_app_router(app_id).startswith("op_")


def test_queued_operations_of_an_app_are_found_for_replanning(
    recorded_dispatcher, router_pattern
):
    client = TestClient(app)
    created = client.post("/apps", json=app_body(), headers=key("q")).json()
    queued = [m["operation_id"] for m in created["members"]]
    assert app_activities.queued_app_operations(created["id"]) == queued
    run_plans(recorded_dispatcher)
    assert app_activities.queued_app_operations(created["id"]) == []
    # A retried POST /apps plans whatever is still queued and asks for the workflow again.
    recorded_dispatcher.seen.clear()
    again = client.post("/apps", json=app_body(), headers=key("q"))
    assert again.status_code == 202 and (created["id"], "app") in recorded_dispatcher.seen


def test_view_only_member_cannot_destroy_or_discard(
    recorded_dispatcher, monkeypatch, pattern_repo
):
    client = TestClient(app)
    app_id = placed_ready(client, recorded_dispatcher, monkeypatch, pattern_repo)
    units = yaml.safe_load(settings.tenants_yaml)
    unit = units["business_units"]["finance"]
    unit["groups"] = ["finance", "viewer"]
    unit["environments"]["dev"]["groups"] = ["finance"]  # only `finance` may change dev
    monkeypatch.setattr(settings, "tenants_yaml", yaml.safe_dump(units))
    monkeypatch.setattr(settings, "dev_groups", "viewer")
    assert client.get(f"/apps/{app_id}").status_code == 200
    before = counts()
    denied = client.post(f"/apps/{app_id}/destroy", headers=key("view"))
    assert denied.status_code == 403
    assert counts() == before and ledger.get_app(app_id).get("teardown") is None
    assert ("POST /apps/{app_id}/destroy", "refused") in events()
    assert ("app.destroy", "accepted") not in events()
    assert client.post(f"/apps/{app_id}/discard").status_code == 403
    monkeypatch.setattr(settings, "dev_groups", "finance")
    assert client.post(f"/apps/{app_id}/destroy", headers=key("view")).status_code == 202


def test_requester_whose_access_was_revoked_cannot_get_the_router_accepted(
    recorded_dispatcher, monkeypatch, pattern_repo
):
    client = TestClient(app)
    placed(monkeypatch, pattern_repo, budget=500)
    app_id = client.post("/apps", json=placed_body(), headers=key("p")).json()["id"]
    run_plans(recorded_dispatcher)
    planned = client.get(f"/apps/{app_id}").json()
    client.post(f"/apps/{app_id}/approve", json={"plan_digests": digests(planned)})
    run_plans(recorded_dispatcher)
    revoke_requester(monkeypatch)
    before = counts()
    assert app_activities.accept_app_router(app_id) == ""
    failed = ledger.get_app(app_id)
    assert "requester_access_revoked" in failed["error"] and failed["router_operation_id"] is None
    assert counts()[0] == before[0]
    assert ("app.router", "refused") in events(f"WHERE operation_id='{app_id}'")


def test_requester_whose_access_was_revoked_cannot_get_replica_destroys_accepted(
    recorded_dispatcher, monkeypatch, pattern_repo
):
    client = TestClient(app)
    app_id = placed_ready(client, recorded_dispatcher, monkeypatch, pattern_repo)
    client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    run_plans(recorded_dispatcher)
    op = client.get(f"/apps/{app_id}").json()["teardown"][0]
    client.post(
        f"/apps/{app_id}/approve", json={"plan_digests": {op["operation_id"]: op["plan_digest"]}}
    )
    run_plans(recorded_dispatcher)
    revoke_requester(monkeypatch)
    before = counts()
    assert app_activities.accept_teardown_replica_destroys(app_id) == {
        "operations": [],
        "refused": True,
    }
    assert counts() == before
    teardown = ledger.get_app(app_id)["teardown"]
    assert "requester_access_revoked" in teardown["error"] and teardown["replicas"] is None
    assert ("app.teardown", "refused") in events(f"WHERE operation_id='{app_id}'")


def test_discarding_a_failed_apps_plans_releases_the_budget(
    recorded_dispatcher, monkeypatch, pattern_repo
):
    client = TestClient(app)
    placed(monkeypatch, pattern_repo, budget=500)
    created = client.post("/apps", json=placed_body(), headers=key("p")).json()
    app_id = created["id"]
    run_plans(recorded_dispatcher)
    planned = client.get(f"/apps/{app_id}").json()
    assert ledger.reserved("finance", "dev") == 120
    # Not failed yet: nothing to discard as a group.
    early = client.post(f"/apps/{app_id}/discard")
    assert early.status_code == 409 and early.json()["error"]["reason"] == "app_discard_unavailable"
    first, second = (m["operation_id"] for m in planned["members"])
    assert client.post(f"/operations/{first}/discard").status_code == 200
    failed = client.get(f"/apps/{app_id}").json()
    assert failed["next_action"] == "discard_planned_operations" and second in failed["error"]
    assert ledger.reserved("finance", "dev") == 60  # the other replica still holds its share
    # Teardown is not possible until the plan is discarded, and says so.
    blocked = client.post(f"/apps/{app_id}/destroy", headers=key("td"))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["next_action"] == "discard_planned_operations"
    discarded = client.post(f"/apps/{app_id}/discard")
    assert discarded.status_code == 200
    assert ledger.get(second)["state"] == "failed"
    assert ledger.reserved("finance", "dev") == 0
    body = discarded.json()
    assert body["state"] == "failed" and body["next_action"] == "inspect_failure"
    assert "discard" not in body["links"] and body["error"] is None
    again = client.post(f"/apps/{app_id}/discard")  # idempotent: nothing left, no second event
    assert again.status_code == 200
    assert events(f"WHERE operation_id='{app_id}'").count(("app.discard", "accepted")) == 1
