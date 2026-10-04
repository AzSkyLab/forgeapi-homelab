"""Terraform subprocess bounds: the init-only cache lock, per-call deadlines, and apply refusals
that must never be retried, unlocked or replanned.

A hung or slow Terraform must not be able to starve other deployments (the init/plan/apply gate)
or stall a worker slot forever (deadlines). A saved plan that another process already applied, or
a locked state, must fail once with no automatic unlock, retry or replan."""

import os
import threading
import time
from pathlib import Path

import pytest

from app import ledger, operation_activities, terraform
from app.settings import settings
from tests.test_operations import agent_client as agent_client
from tests.test_operations import submit


def _fake_bin(tmp_path, body, name="terraform"):
    path = tmp_path / name
    path.write_text(f"#!/usr/bin/env python3\n{body}")
    path.chmod(0o755)
    return path


def _workdir(dep_id):
    work = terraform.deployment_dir(dep_id) / "work"
    work.mkdir(parents=True, exist_ok=True)
    return work


# ---------------------------------------------------------------------------
# Gate: init no longer waits behind a concurrent plan/apply reader.
# ---------------------------------------------------------------------------


def test_init_is_not_blocked_behind_a_long_running_plan(monkeypatch, tmp_path):
    bin_ = _fake_bin(tmp_path, "import sys, time\nif sys.argv[1] == 'plan':\n    time.sleep(1.2)\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    _workdir("dep-a")
    _workdir("dep-b")

    started = threading.Event()

    def run_plan():
        started.set()
        terraform._run("dep-a", "plan")

    thread = threading.Thread(target=run_plan)
    thread.start()
    started.wait()
    time.sleep(0.2)  # let the plan's subprocess actually start before racing init against it
    before = time.monotonic()
    terraform._run("dep-b", "init")
    elapsed = time.monotonic() - before
    thread.join(5)
    assert elapsed < 0.6  # init did not wait for the plan's 1.2s (it would have, pre-fix)


# ---------------------------------------------------------------------------
# Deadline: SIGINT first, SIGKILL after GRACE, always raises TerraformError.
# ---------------------------------------------------------------------------


def test_deadline_interrupts_a_hanging_terraform_with_sigint(monkeypatch, tmp_path):
    bin_ = _fake_bin(tmp_path, "import time\ntime.sleep(5)\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    _workdir("dep-sigint")
    start = time.monotonic()
    with pytest.raises(terraform.TerraformError, match="exceeded its deadline"):
        terraform._run("dep-sigint", "plan", deadline=time.monotonic() + 0.2)
    elapsed = time.monotonic() - start
    assert elapsed < 2  # died to plain SIGINT; never needed GRACE (60s) at all


def test_deadline_sigkills_a_terraform_that_ignores_sigint(monkeypatch, tmp_path):
    pid_file = tmp_path / "pid"
    bin_ = _fake_bin(
        tmp_path,
        "import os, signal, time\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "time.sleep(10)\n",
    )
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    monkeypatch.setattr(terraform, "GRACE", 0.3)
    _workdir("dep-sigkill")
    start = time.monotonic()
    with pytest.raises(terraform.TerraformError, match="exceeded its deadline"):
        terraform._run("dep-sigkill", "apply", deadline=time.monotonic() + 0.2)
    elapsed = time.monotonic() - start
    assert elapsed < 3  # bounded by the (shrunk) GRACE, not the fake's 10s sleep

    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.killpg(pid, 0)  # the whole child process group is gone


def test_plan_phase_deadline_is_failed(monkeypatch, tmp_path, agent_client, recorded_dispatcher):
    op = submit(agent_client, "deadline-plan").json()
    bin_ = _fake_bin(tmp_path, "import time\ntime.sleep(5)\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    monkeypatch.setattr(operation_activities, "PLAN_DEADLINE", 0.2)
    monkeypatch.setattr(terraform, "GRACE", 0.3)

    assert recorded_dispatcher.run_next()

    assert ledger.get(op["id"])["state"] == "failed"


def test_apply_phase_deadline_becomes_uncertain(
    monkeypatch, tmp_path, agent_client, recorded_dispatcher
):
    op = submit(agent_client, "deadline-apply").json()
    assert recorded_dispatcher.run_next()  # plans with the real terraform binary
    planned = agent_client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202

    bin_ = _fake_bin(tmp_path, "import time\ntime.sleep(5)\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    monkeypatch.setattr(operation_activities, "APPLY_DEADLINE", 0.2)
    monkeypatch.setattr(terraform, "GRACE", 0.3)

    assert recorded_dispatcher.run_next()

    assert ledger.get(op["id"])["state"] == "uncertain"


# ---------------------------------------------------------------------------
# Refusals: a stale saved plan or a locked state fail once, with no unlock or retry.
# ---------------------------------------------------------------------------


def test_stale_saved_plan_is_failed_without_retry_and_releases_the_resource(
    agent_client, recorded_dispatcher
):
    op = submit(agent_client, "stale-plan").json()
    assert recorded_dispatcher.run_next()  # real terraform plan
    planned = agent_client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    resource_id = op["resource_id"]

    # Out-of-band: apply the very same saved plan directly, bypassing the ledger entirely. This
    # is what makes the ledger's own later apply of the identical tfplan "stale" to real
    # Terraform: the plan's recorded prior state no longer matches the state on disk.
    terraform._run(resource_id, "apply", "-no-color", "tfplan")

    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202
    assert recorded_dispatcher.run_next()

    result = ledger.get(op["id"])
    assert result["state"] == "failed"
    assert result["error"] == "saved plan is stale; submit a new intent"

    receipts = terraform.log_path(resource_id).read_text()
    # Exactly one apply attempt came from the operation's own (safe_logs) apply call; the other
    # "apply -no-color tfplan" receipt is the out-of-band one, logged with full raw output.
    assert receipts.count("$ terraform apply -no-color tfplan\nexit_code=") == 1
    assert "force-unlock" not in receipts

    freed = submit(agent_client, "stale-plan-after", resource_id=resource_id)
    assert freed.status_code == 202  # the busy reservation was released on failure


def test_apply_refusal_on_state_lock_is_failed_without_unlock(
    monkeypatch, tmp_path, agent_client, recorded_dispatcher
):
    op = submit(agent_client, "lock-refusal").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    assert planned["state"] == "planned"
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202

    calls = tmp_path / "calls"
    lock_error = (
        "Error: Error acquiring the state lock\n\n"
        "Error message: state blob is already locked\n"
        "Lock Info:\n"
        "  ID:        7f3c1c52-9a1e-4d5b-8f0a-2b6e0d9c4a11\n"
    )
    bin_ = _fake_bin(
        tmp_path,
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(sys.argv[1] + chr(10))\n"
        f"sys.stderr.write({lock_error!r})\n"
        "sys.exit(1)\n",
    )
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))

    assert recorded_dispatcher.run_next()

    result = ledger.get(op["id"])
    assert result["state"] == "failed"
    assert result["error"] == (
        "Terraform state is locked by another run; an operator must inspect the lock"
    )
    assert calls.read_text().splitlines() == ["apply"]  # exactly one attempt, never force-unlock


def test_apply_refusal_for_other_terraform_version_is_failed(
    monkeypatch, tmp_path, agent_client, recorded_dispatcher
):
    # Text observed from Terraform 1.16.5 applying a 1.15.9 plan; it wrote no state.
    op = submit(agent_client, "version-refusal").json()
    assert recorded_dispatcher.run_next()
    planned = agent_client.get(op["links"]["self"]).json()
    payload = {"plan_digest": planned["plan_digest"]}
    assert agent_client.post(planned["links"]["execute"], json=payload).status_code == 202

    calls = tmp_path / "calls"
    refusal = (
        "\nError: Failed to read plan from plan file\n\n"
        "Cannot read the plan from the given plan file: plan file was created by\n"
        "Terraform 1.15.9, but this is 1.16.5; plan files cannot be transferred\n"
        "between different Terraform versions.\n"
    )
    bin_ = _fake_bin(
        tmp_path,
        "import sys\n"
        f"open({str(calls)!r}, 'a').write(sys.argv[1] + chr(10))\n"
        f"sys.stderr.write({refusal!r})\n"
        "sys.exit(1)\n",
    )
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))

    assert recorded_dispatcher.run_next()

    result = ledger.get(op["id"])
    assert result["state"] == "failed"
    assert result["error"] == (
        "saved plan was made by a different Terraform version; submit a new intent"
    )
    assert calls.read_text().splitlines() == ["apply"]
    freed = submit(agent_client, "version-refusal-after", resource_id=op["resource_id"])
    assert freed.status_code == 202


def test_background_launch_children_get_default_sigint_and_parent_is_untouched(tmp_path):
    """Shells start background jobs with SIGINT ignored, and children inherit that. Terraform
    children are reset to the default so a deadline's SIGINT reaches them, while importing the
    wrapper leaves the worker's own SIGINT disposition alone."""
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[1]
    probe = (
        "import signal, sys; signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "import app.terraform as t; "
        "print(signal.getsignal(signal.SIGINT) == signal.SIG_IGN); "
        "child = 'import signal; "
        "print(signal.getsignal(signal.SIGINT) is signal.default_int_handler)'; "
        "done = t._invoke([sys.executable, '-c', child], '.', {}, None); "
        "print(done.stdout.strip())"
    )
    env = {"PATH": os.environ["PATH"], "PYTHONPATH": str(repo), "FORGEAPI_DATA_DIR": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, "-c", probe], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert done.stdout.split() == ["True", "True"], done.stderr


def test_init_waiting_for_the_provider_cache_honours_the_deadline(monkeypatch, tmp_path):
    bin_ = _fake_bin(tmp_path, "open(__file__ + '.ran', 'w').write('x')\n")
    monkeypatch.setattr(settings, "terraform_bin", str(bin_))
    _workdir("dep-lock")
    assert terraform._INIT_LOCK.acquire(timeout=1)
    try:
        started = time.monotonic()
        with pytest.raises(terraform.TerraformError, match="waiting for the provider cache"):
            terraform._run("dep-lock", "init", deadline=time.monotonic() + 0.3)
        assert time.monotonic() - started < 2
    finally:
        terraform._INIT_LOCK.release()
    assert not (tmp_path / "terraform.ran").exists()  # no process started
    # The lock is free again afterwards.
    assert terraform._INIT_LOCK.acquire(timeout=1)
    terraform._INIT_LOCK.release()
    assert operation_activities.diagnostic(
        {},
        terraform.TerraformError(
            "terraform init exceeded its deadline waiting for the provider cache"
        ),
    )


def test_second_workspace_init_does_not_rewrite_the_shared_provider_cache(tmp_path, monkeypatch):
    """A fresh workspace's init used to re-extract providers into the shared plugin cache in
    place; any plan/apply executing that binary made the init fail with "text file busy". Real
    Terraform: the cached binary must not be rewritten by a later workspace's init."""
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    example = Path(__file__).resolve().parents[1] / "examples" / "local-file"
    for name in ("cache-a", "cache-b"):
        work = terraform.deployment_dir(name) / "work"
        work.mkdir(parents=True)
        (work / "main.tf").write_text((example / "main.tf").read_text())
    terraform._run("cache-a", "init", "-no-color", "-input=false")
    cached = next((tmp_path / "data" / "plugin-cache").rglob("terraform-provider-local*"))
    before = cached.stat().st_mtime_ns
    time.sleep(1.1)
    terraform._run("cache-b", "init", "-no-color", "-input=false")
    assert cached.stat().st_mtime_ns == before
