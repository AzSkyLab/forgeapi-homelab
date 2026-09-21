"""A worker can be stopped mid-run (scale-in, restart). The deployment must not stay stuck, and
the dead run's Terraform state lock must not block the retry."""

import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app import audit, db, recovery, terraform
from app.models import State

LOCK_ERROR = """
Error: Error acquiring the state lock

Error message: state blob is already locked
Lock Info:
  ID:        7f3c1c52-9a1e-4d5b-8f0a-2b6e0d9c4a11
  Path:      tfstate/deployments/dep_x.tfstate
  Operation: OperationTypeApply
  Who:       forgeapi@worker
  Version:   1.15.9
  Created:   2026-09-21 15:00:00 +0000 UTC
"""


def test_lock_id_is_read_only_from_a_lock_error():
    assert terraform.lock_id(LOCK_ERROR) == "7f3c1c52-9a1e-4d5b-8f0a-2b6e0d9c4a11"
    assert terraform.lock_id("Error: something else\n  ID:        abcdef12-0000") is None
    assert terraform.lock_id("") is None


def test_a_dead_runs_lock_is_released_once_and_the_command_is_repeated(monkeypatch, tmp_path):
    monkeypatch.setattr(terraform, "deployment_dir", lambda d: tmp_path / d)
    (tmp_path / "dep_x" / "work").mkdir(parents=True)
    calls = []

    def fake_run(command, **_kwargs):
        args = command[1:]
        calls.append(args[0])
        locked = args[0] == "plan" and calls.count("plan") == 1
        return SimpleNamespace(
            returncode=1 if locked else 0, stdout="ok", stderr=LOCK_ERROR if locked else ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert terraform._run("dep_x", "plan", "-no-color") == "ok"
    assert calls == ["plan", "force-unlock", "plan"]
    assert "belongs to an interrupted run" in terraform.log_path("dep_x").read_text()


def test_a_lock_that_survives_the_unlock_is_reported_not_looped(monkeypatch, tmp_path):
    monkeypatch.setattr(terraform, "deployment_dir", lambda d: tmp_path / d)
    (tmp_path / "dep_y" / "work").mkdir(parents=True)
    calls = []

    def always_locked(command, **_kwargs):
        calls.append(command[1])
        failing = command[1] == "plan"
        return SimpleNamespace(
            returncode=1 if failing else 0, stdout="", stderr=LOCK_ERROR if failing else ""
        )

    monkeypatch.setattr(subprocess, "run", always_locked)
    try:
        terraform._run("dep_y", "plan")
        raise AssertionError("expected TerraformError")
    except terraform.TerraformError as err:
        assert "state lock" in str(err)
    assert calls == ["plan", "force-unlock", "plan"]  # exactly one attempt to unlock


def test_sweep_marks_only_stale_in_flight_deployments_as_interrupted():
    now = datetime.now(UTC)
    stuck = db.create("demo", {}, business_unit="finance", environment="dev")
    db.update(stuck.id, State.applying)
    done = db.create("demo", {})
    db.update(done.id, State.succeeded, outputs={})

    assert recovery.sweep(now) == []  # nothing is stale yet
    assert recovery.sweep(now + timedelta(minutes=6)) == [stuck.id]

    marked = db.get(stuck.id)
    assert marked.state == State.failed and marked.error.startswith("interrupted:")
    assert db.get(done.id).state == State.succeeded  # finished work is never touched
    [event] = audit.for_deployment(stuck.id, "finance")
    assert (event.action, event.outcome, event.actor) == (
        "deployment.state",
        "interrupted",
        "recovery",
    )
    assert event.detail == {"was": "applying"}


def test_a_beating_job_is_not_swept(monkeypatch):
    monkeypatch.setattr(recovery, "BEAT_EVERY", 0.05)
    deployment = db.create("demo", {})
    db.update(deployment.id, State.applying)
    before = db.get(deployment.id).updated_at
    with recovery.alive(deployment.id):
        import time

        time.sleep(0.3)
    assert db.get(deployment.id).updated_at > before  # the heartbeat moved it


def test_an_interrupted_deployment_can_be_retried_and_destroyed(client, dispatched):
    created = client.post(
        "/deployments",
        json={"pattern": "demo", "inputs": {"filename": "a.txt", "content": "x"}},
    ).json()
    db.update(created["id"], State.applying)
    recovery.sweep(datetime.now(UTC) + timedelta(minutes=6))

    shown = client.get(f"/deployments/{created['id']}").json()
    assert shown["state"] == "failed" and "Retry to continue" in shown["error"]
    assert client.post(f"/deployments/{created['id']}/retry").status_code == 202
    db.update(created["id"], State.failed, error=recovery.INTERRUPTED)
    assert client.delete(f"/deployments/{created['id']}").status_code == 202
