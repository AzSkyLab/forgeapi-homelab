"""A killed worker must never replay a Terraform apply with an unknown outcome."""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import yaml
from temporalio.api.enums.v1 import EventType, TimeoutType
from temporalio.testing import WorkflowEnvironment

from app import ledger
from app.dispatch import OperationDispatcher, get_dispatcher, workflow_id
from app.main import app
from app.settings import settings
from tests.operation_support import phase_done


async def appeared(path: Path, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        await asyncio.sleep(0.05)


@pytest.mark.parametrize("kill_worker", [True, False], ids=["worker-killed", "late-completion"])
def test_worker_killed_during_real_apply_becomes_uncertain(tmp_path, monkeypatch, kill_worker):
    example = Path(__file__).resolve().parents[1] / "examples" / "interrupted-apply"
    catalog = tmp_path / "patterns.yaml"
    catalog.write_text(yaml.safe_dump({"patterns": {"interrupted-apply": {"local": str(example)}}}))
    calls = tmp_path / "terraform-calls"
    terraform_bin = shutil.which("terraform")
    assert terraform_bin
    wrapper = tmp_path / "terraform"
    wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        f"with open({str(calls)!r}, 'a') as out:\n"
        "    out.write(f'{sys.argv[1]} {os.getpid()}\\n')\n"
        f"os.execv({terraform_bin!r}, [{terraform_bin!r}, *sys.argv[1:]])\n"
    )
    wrapper.chmod(0o755)
    monkeypatch.setattr(settings, "catalog_path", catalog)
    monkeypatch.setattr(settings, "terraform_bin", str(wrapper))
    monkeypatch.setattr(settings, "task_queue", f"interruption-{tmp_path.name}")

    async def scenario():
        worker = None
        groups = []
        async with await WorkflowEnvironment.start_local() as env:
            address = env.client.service_client.config.target_host
            monkeypatch.setattr(settings, "temporal_address", address)
            child_env = {
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                "FORGEAPI_DATA_DIR": str(settings.data_dir),
                "FORGEAPI_CATALOG_PATH": str(catalog),
                "FORGEAPI_TERRAFORM_BIN": str(wrapper),
                "FORGEAPI_TEMPORAL_ADDRESS": address,
                "FORGEAPI_TASK_QUEUE": settings.task_queue,
            }
            receipt = tmp_path / "late-receipt.json"
            if not kill_worker:
                child_env["FORGEAPI_TEST_LATE_RECEIPT"] = str(receipt)
            previous = app.dependency_overrides.copy()
            app.dependency_overrides[get_dispatcher] = lambda: OperationDispatcher(env.client)
            try:
                worker_log = (tmp_path / "worker.log").open("ab")
                worker = subprocess.Popen(
                    [sys.executable, "-m", "tests.interruption_worker"],
                    cwd=tmp_path,
                    env=child_env,
                    stdout=worker_log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                groups.append(worker.pid)
                worker_log.close()
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as api:
                    intent = {
                        "pattern": "interrupted-apply",
                        "inputs": {"gate_root": str(tmp_path)},
                    }
                    accepted = await api.post(
                        "/operations", json=intent, headers={"Idempotency-Key": "interrupted"}
                    )
                    assert accepted.status_code == 202, accepted.text
                    op = accepted.json()
                    await phase_done(env.client, op["id"], "plan")
                    planned = (await api.get(op["links"]["self"])).json()
                    assert planned["state"] == "planned", planned
                    receipt.unlink(missing_ok=True)
                    digest = planned["plan_digest"]
                    started = await api.post(op["links"]["execute"], json={"plan_digest": digest})
                    assert started.status_code == 202, started.text
                    await appeared(tmp_path / "started")
                    assert (tmp_path / "effect").read_text() == "applied\n"
                    assert ledger.get(op["id"])["state"] == "applying"
                    apply_pid = int(
                        next(line.split()[1] for line in calls.read_text().splitlines()
                             if line.startswith("apply "))
                    )

                    if kill_worker:
                        # Kill only the worker: Terraform has already begun an actual apply.
                        worker.kill()
                        await asyncio.to_thread(worker.wait, 5)
                        assert worker.returncode == -signal.SIGKILL
                        os.kill(apply_pid, 0)
                        replacement_log = (tmp_path / "replacement.log").open("ab")
                        replacement = subprocess.Popen(
                            [sys.executable, "-m", "tests.interruption_worker"],
                            cwd=tmp_path,
                            env=child_env,
                            stdout=replacement_log,
                            stderr=subprocess.STDOUT,
                            start_new_session=True,
                        )
                        groups.append(replacement.pid)
                        replacement_log.close()
                        worker = replacement
                    try:
                        await asyncio.wait_for(
                            phase_done(env.client, op["id"], "apply"), timeout=20
                        )
                    finally:
                        (tmp_path / "release").touch()
                    await appeared(tmp_path / "finished")
                    if not kill_worker:
                        await appeared(receipt)
                        assert json.loads(receipt.read_text()) == {
                            "operation_id": op["id"],
                            "requested_state": "succeeded",
                            "resulting_state": "uncertain",
                        }

                    final = (await api.get(op["links"]["self"])).json()
                    assert final["state"] == "uncertain", final
                    assert final["next_action"] == "reconcile_with_operator"
                    assert final["id"] == op["id"] and final["resource_id"] == op["resource_id"]
                    replay = await api.post(
                        op["links"]["execute"], json={"plan_digest": digest}
                    )
                    assert replay.status_code == 409
                    refused = await api.post(
                        "/operations",
                        json={**intent, "resource_id": op["resource_id"]},
                        headers={"Idempotency-Key": "replacement"},
                    )
                    assert refused.status_code == 409
                    history = await env.client.get_workflow_handle(
                        workflow_id(op["id"], "apply")
                    ).fetch_history()
                    scheduled = [
                        event.activity_task_scheduled_event_attributes
                        for event in history.events
                        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                    ]
                    assert len(scheduled) == 2  # one apply, one uncertainty status write
                    assert [item.activity_type.name for item in scheduled] == [
                        "run_operation_phase", "mark_operation_uncertain"
                    ]
                    assert scheduled[0].start_to_close_timeout.seconds == 5
                    assert scheduled[0].retry_policy.maximum_attempts == 1
                    timed_out = [
                        event.activity_task_timed_out_event_attributes
                        for event in history.events
                        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_TIMED_OUT
                    ]
                    assert len(timed_out) == 1
                    timeout_type = timed_out[0].failure.timeout_failure_info.timeout_type
                    assert timeout_type == TimeoutType.TIMEOUT_TYPE_START_TO_CLOSE
                    verbs = [line.split()[0] for line in calls.read_text().splitlines()]
                    assert verbs.count("plan") == verbs.count("apply") == 1
                    assert "force-unlock" not in calls.read_text()
                    assert ledger.get(op["id"])["state"] == "uncertain"
                    outcomes = [event["outcome"] for event in ledger.events(op["id"], 0, 100)]
                    assert outcomes.count("uncertain") == 1
                    assert "succeeded" not in outcomes
            finally:
                (tmp_path / "release").touch()
                app.dependency_overrides = previous
                if worker and worker.poll() is None:
                    worker.terminate()
                    try:
                        await asyncio.to_thread(worker.wait, 5)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        await asyncio.to_thread(worker.wait, 5)
                for group in groups:
                    with suppress(ProcessLookupError):
                        os.killpg(group, signal.SIGKILL)

    asyncio.run(scenario())
