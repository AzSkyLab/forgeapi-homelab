"""Bounded, idempotent Temporal dispatch after the ledger commits acceptance."""

import asyncio
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import TLSConfig

from app.operation_workflow import AppRolloutWorkflow, AppTeardownWorkflow, OperationPhaseWorkflow
from app.settings import settings


def workflow_id(operation_id: str, phase: str) -> str:
    return f"forgeapi-{operation_id}-{phase}"


DRIFT_SWEEP_WORKFLOW_ID = "forgeapi-drift-sweep"


def app_workflow_id(app_id: str) -> str:
    return f"forgeapi-{app_id}-rollout"


def app_teardown_workflow_id(app_id: str) -> str:
    return f"forgeapi-{app_id}-teardown"


def _read_pem(path: Path | None) -> bytes | None:
    return path.read_bytes() if path else None


def _tls_config() -> bool | TLSConfig:
    """False when TLS is off; otherwise a TLSConfig built from the configured PEM files (read as
    bytes, never logged). The cert/key pair is validated as both-or-neither at startup."""
    if not settings.temporal_tls:
        return False
    return TLSConfig(
        server_root_ca_cert=_read_pem(settings.temporal_tls_ca_path),
        client_cert=_read_pem(settings.temporal_tls_cert_path),
        client_private_key=_read_pem(settings.temporal_tls_key_path),
        domain=settings.temporal_tls_server_name,
    )


async def connect_temporal() -> Client:
    """The one place that opens a Temporal connection; every call site uses it so TLS settings
    apply everywhere, not just where someone remembered to add them."""
    return await Client.connect(
        settings.temporal_address, namespace=settings.temporal_namespace, tls=_tls_config()
    )


class OperationDispatcher:
    def __init__(self, client: Client | None = None):
        self.client = client

    async def ready(self) -> None:
        """Raise if Temporal cannot be reached within 2s. Used only by /readyz; never dispatches."""
        async with asyncio.timeout(2):
            client = self.client or await connect_temporal()
            await client.service_client.check_health()

    async def dispatch(self, operation_id: str, phase: str) -> None:
        # Startup and read-only API use don't require a live Temporal connection.
        async with asyncio.timeout(5):
            client = self.client or await connect_temporal()
            # Duplicate running or completed phases are successful dispatches.
            with suppress(WorkflowAlreadyStartedError):
                await client.start_workflow(
                    OperationPhaseWorkflow.run,
                    args=[operation_id, phase],
                    id=workflow_id(operation_id, phase),
                    task_queue=settings.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    rpc_timeout=timedelta(seconds=4),
                )

    async def dispatch_app(self, app_id: str) -> None:
        """Start the app's rollout workflow. A running or completed one is a successful dispatch;
        one that failed, timed out or was terminated is started again, so a retried request
        revives an app whose workflow died."""
        async with asyncio.timeout(5):
            client = self.client or await connect_temporal()
            with suppress(WorkflowAlreadyStartedError):
                await client.start_workflow(
                    AppRolloutWorkflow.run,
                    args=[app_id],
                    id=app_workflow_id(app_id),
                    task_queue=settings.task_queue,
                    id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
                    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                    rpc_timeout=timedelta(seconds=4),
                )

    async def dispatch_teardown(self, app_id: str) -> None:
        """Start the app's teardown workflow. A running one is reused; a finished one (for
        example after a failed teardown) does not block a later teardown request."""
        async with asyncio.timeout(5):
            client = self.client or await connect_temporal()
            await client.start_workflow(
                AppTeardownWorkflow.run,
                args=[app_id],
                id=app_teardown_workflow_id(app_id),
                task_queue=settings.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                rpc_timeout=timedelta(seconds=4),
            )


def get_dispatcher() -> OperationDispatcher:
    return OperationDispatcher()
