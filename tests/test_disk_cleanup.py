"""A failed operation leaves no saved plan behind; a destroyed deployment drops its provider and
module download. State, tfvars and logs are never removed; an uncertain apply keeps its plan."""

from app import ledger, operation_activities, terraform
from app.settings import settings
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit
from tests.test_terraform_bounds import _fake_bin


def _planned(client, dispatcher, key, **updates):
    op = submit(client, key, **updates).json()
    assert dispatcher.run_next()
    planned = client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned", planned
    return planned


def _execute(client, planned):
    payload = {"plan_digest": planned["plan_digest"]}
    assert client.post(planned["links"]["execute"], json=payload).status_code == 202


def test_planning_failure_leaves_no_plan(monkeypatch, tmp_path, agent_client, recorded_dispatcher):
    op = submit(agent_client, "plan-fails").json()
    work = terraform.deployment_dir(op["resource_id"]) / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "tfplan").write_bytes(b"left over")  # e.g. written, then the show step failed
    bin_ = _fake_bin(tmp_path, "import sys\nsys.exit(1)\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"
    assert not (work / "tfplan").exists()


def test_tampered_plan_is_removed(agent_client, recorded_dispatcher):
    planned = _planned(agent_client, recorded_dispatcher, "tampered")
    op = ledger.get(planned["id"])
    operation_activities.plan_path(op).write_bytes(b"tampered")
    _execute(agent_client, planned)
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"
    assert not operation_activities.plan_path(op).exists()


def test_stale_plan_is_removed_and_state_kept(agent_client, recorded_dispatcher):
    planned = _planned(agent_client, recorded_dispatcher, "stale")
    op = ledger.get(planned["id"])
    terraform._run(op["resource_id"], "apply", "-no-color", "tfplan")  # makes the plan stale
    _execute(agent_client, planned)
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"
    work = operation_activities.plan_path(op).parent
    assert not (work / "tfplan").exists()
    assert (work / "terraform.tfstate").is_file()


def test_lock_refusal_removes_plan_and_keeps_state(
    monkeypatch, tmp_path, agent_client, recorded_dispatcher
):
    planned = _planned(agent_client, recorded_dispatcher, "locked")
    op = ledger.get(planned["id"])
    _execute(agent_client, planned)
    bin_ = _fake_bin(
        tmp_path,
        "import sys\nsys.stderr.write('Error: Error acquiring the state lock')\nsys.exit(1)\n",
    )
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    work = operation_activities.plan_path(op).parent
    (work / "terraform.tfstate").write_text("{}")
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "failed"
    assert not (work / "tfplan").exists()
    assert (work / "terraform.tfstate").read_text() == "{}"


def test_uncertain_apply_keeps_the_plan(monkeypatch, tmp_path, agent_client, recorded_dispatcher):
    planned = _planned(agent_client, recorded_dispatcher, "uncertain")
    op = ledger.get(planned["id"])
    _execute(agent_client, planned)
    bin_ = _fake_bin(tmp_path, "import sys\nsys.stderr.write('boom')\nsys.exit(1)\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    assert recorded_dispatcher.run_next()
    assert ledger.get(op["id"])["state"] == "uncertain"
    assert operation_activities.plan_path(op).is_file()


def test_destroy_drops_terraform_dir_but_keeps_state_and_create_keeps_it(
    agent_client, recorded_dispatcher
):
    planned = _planned(agent_client, recorded_dispatcher, "create")
    _execute(agent_client, planned)
    assert recorded_dispatcher.run_next()
    op = ledger.get(planned["id"])
    assert op["state"] == "succeeded"
    work = operation_activities.plan_path(op).parent
    assert (work / ".terraform").is_dir()

    cleanup = _planned(
        agent_client, recorded_dispatcher, "cleanup", action="destroy",
        resource_id=op["resource_id"],
    )
    _execute(agent_client, cleanup)
    assert recorded_dispatcher.run_next()
    assert ledger.get(cleanup["id"])["state"] == "succeeded"
    assert not (work / ".terraform").exists()
    assert (work / "terraform.tfstate").is_file()
    assert (work / "terraform.tfvars.json").is_file()


def test_late_failure_leaves_a_newer_plan_and_terraform_dir(
    agent_client, recorded_dispatcher
):
    planned = _planned(agent_client, recorded_dispatcher, "late")
    op = ledger.get(planned["id"])
    _execute(agent_client, planned)
    running = ledger.claim(op["id"], "apply")
    ledger.interrupted(op["id"], "apply")  # timeout recorded: stored state is now uncertain
    plan = operation_activities.plan_path(op)
    plan.write_bytes(b"a newer operation's plan")  # as if a new op on the resource planned
    (plan.parent / ".terraform").mkdir(exist_ok=True)
    operation_activities.fail(running, "late failure")
    assert plan.read_bytes() == b"a newer operation's plan"
    assert ledger.get(op["id"])["state"] == "uncertain"
    operation_activities.fail({**running, "action": "destroy"}, "late destroy")
    assert (plan.parent / ".terraform").is_dir()
