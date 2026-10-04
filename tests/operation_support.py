"""Test clients: recording dispatcher for focused tests; real Temporal for end-to-end tests."""

import asyncio
import shutil
import subprocess
import tempfile
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock

import httpx
from temporalio.testing import WorkflowEnvironment

from app import terraform
from app.dispatch import OperationDispatcher, get_dispatcher, workflow_id
from app.main import app
from app.operation_activities import run_operation_phase
from app.settings import settings


def destroy_leftovers(subscription_id: str | None = None) -> list[str]:
    """Destroy what failed Floci tests left behind; returns workspaces that still fail.

    A workspace whose `work/.terraform` survives was initialised and not cleanly destroyed
    (the production destroy removes it). Its terraform.tfvars.json is auto-loaded. Two passes
    let a consumer (an object) go before the bucket it references."""
    data = settings.data_dir.resolve()
    # Never touch real state (.local/data, a lab vault): only a test's temporary data dir.
    if not data.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise RuntimeError(f"refusing to destroy outside the temp dir: {data}")
    found = [
        d / "work"
        for d in sorted((data / "deployments").glob("*"))
        if (d / "work" / ".terraform").is_dir()
    ]
    for _ in range(2):
        failing = []
        for work in found:
            env = terraform._env(subscription_id)
            listed = subprocess.run(
                [settings.terraform_bin, "state", "list", "-no-color"],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=300,
            )
            if listed.returncode == 0 and not listed.stdout.strip():
                # Nothing in state (e.g. a run refused before creating anything): no leak.
                shutil.rmtree(work / ".terraform", ignore_errors=True)
                continue
            done = subprocess.run(
                [settings.terraform_bin, "destroy", "-auto-approve", "-input=false", "-no-color"],
                cwd=work,
                env=env,
                capture_output=True,
                timeout=300,
            )
            if done.returncode:
                failing.append(work)
            else:
                shutil.rmtree(work / ".terraform", ignore_errors=True)
        found = failing
    return [str(w.parent) for w in found]


class RecordingDispatcher:
    def __init__(self):
        self.pending = deque()
        self.seen = set()
        self.lock = Lock()

    async def dispatch(self, operation_id, phase):
        with self.lock:
            if (operation_id, phase) not in self.seen:
                self.seen.add((operation_id, phase))
                self.pending.append((operation_id, phase))

    async def dispatch_app(self, app_id):
        with self.lock:
            self.seen.add((app_id, "app"))

    async def dispatch_teardown(self, app_id):
        with self.lock:
            self.seen.add((app_id, "teardown"))

    def run_next(self):
        if not self.pending:
            return False
        return run_operation_phase(*self.pending.popleft())


@asynccontextmanager
async def temporal_api():
    async with await WorkflowEnvironment.start_local() as env:
        previous = app.dependency_overrides.copy()
        app.dependency_overrides[get_dispatcher] = lambda: OperationDispatcher(env.client)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as api:
                yield api, env.client
        finally:
            app.dependency_overrides = previous


async def phase_done(client, operation_id, phase):
    await asyncio.wait_for(
        client.get_workflow_handle(workflow_id(operation_id, phase)).result(), timeout=120
    )
