"""A real API process crash between ledger commit and Temporal dispatch."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import httpx
import yaml
from temporalio.api.enums.v1 import EventType
from temporalio.service import RPCError, RPCStatusCode
from temporalio.testing import WorkflowEnvironment

from app import ledger
from app.dispatch import workflow_id
from app.settings import settings
from tests.operation_support import phase_done


async def until_ready(client, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if (await client.get("/healthz")).status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.05)
    raise AssertionError("API did not become ready")


async def until_file(path, timeout=15):
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path.name}"
        await asyncio.sleep(0.05)


def test_api_killed_after_acceptance_recovers_on_exact_retry(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    catalog = tmp_path / "patterns.yaml"
    catalog.write_text(yaml.safe_dump({
        "patterns": {"local-file": {"local": str(repo / "examples" / "local-file")}}
    }))
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]

    async def scenario():
        processes = []
        first = None
        async with await WorkflowEnvironment.start_local() as env:
            child_env = {
                "PATH": os.environ["PATH"],
                "HOME": os.environ["HOME"],
                "PYTHONPATH": str(repo),
                "FORGEAPI_DATA_DIR": str(settings.data_dir),
                "FORGEAPI_CATALOG_PATH": str(catalog),
                "FORGEAPI_TEMPORAL_ADDRESS": env.client.service_client.config.target_host,
                "FORGEAPI_TASK_QUEUE": f"dispatch-crash-{tmp_path.name}",
            }
            marker = tmp_path / "dispatch-entered"

            def spawn(module, name, extra_env=None, *args):
                output = (tmp_path / f"{name}.log").open("ab")
                proc = subprocess.Popen(
                    [sys.executable, "-m", module, *args],
                    cwd=tmp_path,
                    env={**child_env, **(extra_env or {})},
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                output.close()
                processes.append(proc)
                return proc

            try:
                spawn("app.worker", "worker")
                original = spawn(
                    "tests.dispatch_crash_api", "api-before",
                    {"FORGEAPI_TEST_DISPATCH_MARKER": str(marker)}, str(port),
                )
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as api:
                    await until_ready(api)
                    body = {
                        "pattern": "local-file",
                        "inputs": {"filename": "crash.txt", "content": "durable"},
                    }
                    headers = {"Idempotency-Key": "crash-window"}
                    first = asyncio.create_task(api.post("/operations", json=body, headers=headers))
                    await until_file(marker)
                    op_id, phase = marker.read_text().split()
                    assert phase == "plan"
                    op = ledger.get(op_id)
                    assert op["state"] == "queued"
                    resource_id = op["resource_id"]
                    with ledger.connect() as con:
                        reservation = con.execute(
                            "SELECT body FROM resources WHERE id=?", (resource_id,)
                        ).fetchone()
                    assert reservation is not None
                    assert json.loads(reservation[0])["operation_id"] == op_id
                    assert [event["outcome"] for event in ledger.events(op_id, 0, 100)] == [
                        "accepted"
                    ]

                    original.kill()
                    await asyncio.to_thread(original.wait, 5)
                    assert original.returncode == -signal.SIGKILL
                    with suppress(httpx.TransportError):
                        assert await asyncio.wait_for(first, 5) is None
                    try:
                        await env.client.get_workflow_handle(workflow_id(op_id, "plan")).describe()
                    except RPCError as exc:
                        assert exc.status == RPCStatusCode.NOT_FOUND
                    else:
                        raise AssertionError("workflow started before retry")

                    spawn("tests.dispatch_crash_api", "api-after", None, str(port))
                    await until_ready(api)
                    blocked = await api.post(
                        "/operations",
                        json={**body, "resource_id": resource_id},
                        headers={"Idempotency-Key": "reserved-resource"},
                    )
                    assert blocked.status_code == 409
                    assert ledger.get(op_id)["state"] == "queued"
                    try:
                        await env.client.get_workflow_handle(
                            workflow_id(op_id, "plan")
                        ).describe()
                    except RPCError as exc:
                        assert exc.status == RPCStatusCode.NOT_FOUND
                    else:
                        raise AssertionError("restart dispatched accepted intent automatically")
                    retry = await api.post("/operations", json=body, headers=headers)
                    assert retry.status_code == 202, retry.text
                    assert retry.json()["id"] == op_id
                    assert retry.json()["resource_id"] == resource_id
                    await phase_done(env.client, op_id, "plan")
                    planned = (await api.get(retry.json()["links"]["self"])).json()
                    assert planned["state"] == "planned" and planned["plan_digest"]
                    assert (await api.post("/operations", json={**body, "inputs": {
                        "filename": "changed.txt", "content": "durable"
                    }}, headers=headers)).status_code == 409
                    history = await env.client.get_workflow_handle(
                        workflow_id(op_id, "plan")
                    ).fetch_history()
                    scheduled = [
                        event.activity_task_scheduled_event_attributes
                        for event in history.events
                        if event.event_type == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
                    ]
                    assert len(scheduled) == 1
                    assert scheduled[0].activity_type.name == "run_operation_phase"
                    assert scheduled[0].retry_policy.maximum_attempts == 1
                    outcomes = [event["outcome"] for event in ledger.events(op_id, 0, 100)]
                    assert outcomes.count("accepted") == 1
                    assert outcomes.count("planned") == 1
                    assert ledger.get(op_id)["resource_id"] == resource_id
                    try:
                        await env.client.get_workflow_handle(
                            workflow_id(op_id, "apply")
                        ).describe()
                    except RPCError as exc:
                        assert exc.status == RPCStatusCode.NOT_FOUND
                    else:
                        raise AssertionError("planning unexpectedly dispatched apply")
            finally:
                for process in reversed(processes):
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    if process.poll() is None:
                        try:
                            await asyncio.to_thread(process.wait, 5)
                        except subprocess.TimeoutExpired:
                            with suppress(ProcessLookupError):
                                os.killpg(process.pid, signal.SIGKILL)
                            await asyncio.to_thread(process.wait, 5)
                if first is not None:
                    first.cancel()
                    with suppress(asyncio.CancelledError, httpx.TransportError):
                        await first

    asyncio.run(scenario())
