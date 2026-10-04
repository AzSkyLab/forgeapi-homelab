"""A stopped single-host runtime restores its ledger, Temporal history and saved plan."""

import asyncio
import hashlib
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from temporalio.testing import WorkflowEnvironment

from app import ledger, operation_activities, terraform
from app.dispatch import OperationDispatcher, get_dispatcher, workflow_id
from app.main import app
from app.settings import settings
from app.worker import build_worker
from tests.operation_support import phase_done
from tests.test_operations import INTENT


@asynccontextmanager
async def running_host():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with await WorkflowEnvironment.start_local(
        dev_server_database_filename=str(settings.data_dir / "temporal.db"), ui=False
    ) as env:
        previous = app.dependency_overrides.copy()
        app.dependency_overrides[get_dispatcher] = lambda: OperationDispatcher(env.client)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api, build_worker(env.client):
                yield api, env.client
        finally:
            app.dependency_overrides = previous


def files_under(root: Path) -> dict[str, str]:
    """Include regular files and symlink targets, including symlinked directories."""
    found = {}
    for base, directories, files in os.walk(root):
        for name in directories + files:
            path = Path(base) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                found[relative] = f"link:{os.readlink(path)}"
            elif path.is_file():
                found[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def ledger_rows():
    with ledger.connect() as con:
        return {
            table: [tuple(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in ("operations", "resources", "events")
        }


async def history_proof(client, operation_id, phase):
    handle = client.get_workflow_handle(workflow_id(operation_id, phase))
    description = await handle.describe()
    history = await handle.fetch_history()
    return (
        description.run_id,
        len(history.events),
        hashlib.sha256(
            b"".join(event.SerializeToString(deterministic=True) for event in history.events)
        ).hexdigest(),
        await asyncio.wait_for(handle.result(), timeout=30),
    )


def test_quiesced_single_host_backup_restores_pending_exact_plan(tmp_path, monkeypatch):
    async def scenario():
        initial = {**INTENT, "pattern": "local-file", "version": None}
        changed = {
            **initial,
            "resource_id": None,
            "inputs": {**INTENT["inputs"], "content": "restored update"},
        }

        def headers(key):
            return {"Idempotency-Key": key}

        async with running_host() as (api, client):
            accepted = await api.post("/v1/operations", json=initial, headers=headers("create"))
            assert accepted.status_code == 202
            created = accepted.json()
            await phase_done(client, created["id"], "plan")
            created = (await api.get(created["links"]["self"])).json()
            assert created["state"] == "planned"
            executed = await api.post(
                created["links"]["execute"], json={"plan_digest": created["plan_digest"]}
            )
            assert executed.status_code == 202
            await phase_done(client, created["id"], "apply")
            created = (await api.get(created["links"]["self"])).json()
            assert created["state"] == "succeeded"
            output = Path(created["outputs"]["path"])
            assert output.is_relative_to(settings.data_dir)
            assert output.read_text() == INTENT["inputs"]["content"]

            changed["resource_id"] = created["resource_id"]
            accepted = await api.post("/v1/operations", json=changed, headers=headers("update"))
            assert accepted.status_code == 202
            pending = accepted.json()
            await phase_done(client, pending["id"], "plan")
            pending = (await api.get(pending["links"]["self"])).json()
            assert pending["state"] == "planned"
            assert pending["changes"]
            plan = operation_activities.plan_path(ledger.get(pending["id"]))
            assert plan.is_file()
            assert operation_activities.digest(ledger.get(pending["id"])) == pending["plan_digest"]
            workflow_history = {
                (operation_id, phase): await history_proof(client, operation_id, phase)
                for operation_id, phase in (
                    (created["id"], "plan"),
                    (created["id"], "apply"),
                    (pending["id"], "plan"),
                )
            }
            rows = ledger_rows()
            assert [row[4] for row in rows["events"]].count("accepted") == 3

        # Both the worker and Temporal have exited; every writer is quiesced.
        before = files_under(settings.data_dir)
        assert "temporal.db" in before and "operations.sqlite" in before
        assert (
            str((terraform.deployment_dir(created["resource_id"]) / "work" / "terraform.tfstate")
            .relative_to(settings.data_dir)) in before
        )
        assert str(plan.relative_to(settings.data_dir)) in before
        snapshot = tmp_path / "snapshot"
        shutil.copytree(settings.data_dir, snapshot, symlinks=True)
        assert files_under(snapshot) == before
        settings.data_dir.rename(tmp_path / "original-offline")
        shutil.copytree(snapshot, settings.data_dir, symlinks=True)
        assert files_under(settings.data_dir) == before
        assert ledger_rows() == rows
        assert operation_activities.digest(ledger.get(pending["id"])) == pending["plan_digest"]

        calls = []
        real_run = terraform._run

        def recording_run(deployment_id, *args, **kwargs):
            calls.append(args[0])
            return real_run(deployment_id, *args, **kwargs)

        monkeypatch.setattr(terraform, "_run", recording_run)
        async with running_host() as (api, client):
            for (operation_id, phase), proof in workflow_history.items():
                assert await history_proof(client, operation_id, phase) == proof
            assert ledger_rows() == rows
            for key, intent, expected in (
                ("create", initial, created),
                ("update", changed, pending),
            ):
                replay = await api.post("/v1/operations", json=intent, headers=headers(key))
                assert replay.status_code == 202
                assert replay.json()["id"] == expected["id"]
            assert ledger_rows() == rows
            competing = await api.post(
                "/v1/operations", json=changed, headers=headers("competing")
            )
            assert competing.status_code == 409
            assert output.read_text() == INTENT["inputs"]["content"]
            assert calls == []

            exact = {"plan_digest": pending["plan_digest"]}
            execute = await api.post(pending["links"]["execute"], json=exact)
            assert execute.status_code == 202
            await phase_done(client, pending["id"], "apply")
            done = (await api.get(pending["links"]["self"])).json()
            assert done["state"] == "succeeded"
            assert done["resource_id"] == created["resource_id"]
            assert output.read_text() == "restored update"
            assert calls == ["apply", "output"]
            duplicate = await api.post(pending["links"]["execute"], json=exact)
            assert duplicate.status_code == 202 and duplicate.json()["state"] == "succeeded"
            assert calls == ["apply", "output"]
            assert not plan.exists()

    asyncio.run(scenario())
