"""Azure Table Storage store for deployment records: one entity per deployment, Entra auth.

Chosen for hosting because it scales to zero cost, lives in the state storage account and needs
no connection string. It only supports lookup by ID, which is all the API does today."""

import contextlib
import json
from datetime import datetime
from typing import Any

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode

from app.models import Deployment, State
from app.settings import settings

_PARTITION = "deployment"
_ensured: set[str] = set()  # tables this process has already made sure exist


def _table() -> TableClient:
    if settings.table_connection_string:  # the Azurite emulator, tests only
        client = TableClient.from_connection_string(
            settings.table_connection_string, settings.table_name
        )
    else:
        from app.azure_identity import credential

        client = TableClient(
            endpoint=f"https://{settings.table_storage_account}.table.core.windows.net",
            table_name=settings.table_name,
            credential=credential(),
        )
    key = f"{client.url}/{settings.table_name}"
    if key not in _ensured:
        with contextlib.suppress(ResourceExistsError):
            client.create_table()
        _ensured.add(key)
    return client


def insert(d: Deployment) -> None:
    with _table() as table:
        table.create_entity(
            {
                "PartitionKey": _PARTITION,
                "RowKey": d.id,
                "pattern": d.pattern,
                "version": d.version or "",
                "commit_sha": d.commit or "",
                "inputs": json.dumps(d.inputs),
                "state": str(d.state),
                "outputs": "",
                "error": "",
                "created_at": d.created_at.isoformat(),
                "updated_at": d.updated_at.isoformat(),
            }
        )


def get(deployment_id: str) -> Deployment | None:
    with _table() as table:
        try:
            e = table.get_entity(_PARTITION, deployment_id)
        except ResourceNotFoundError:
            return None
    return Deployment(
        id=e["RowKey"],
        pattern=e["pattern"],
        version=e["version"] or None,
        commit=e["commit_sha"] or None,
        inputs=json.loads(e["inputs"]),
        state=State(e["state"]),
        outputs=json.loads(e["outputs"]) if e["outputs"] else None,
        error=e["error"] or None,
        created_at=e["created_at"],
        updated_at=e["updated_at"],
    )


def update(
    deployment_id: str,
    state: State,
    outputs: dict[str, Any] | None,
    error: str | None,
    now: datetime,
) -> None:
    changes: dict[str, Any] = {
        "PartitionKey": _PARTITION,
        "RowKey": deployment_id,
        "state": str(state),
        "error": error or "",
        "updated_at": now.isoformat(),
    }
    if outputs is not None:  # merge leaves the stored outputs alone otherwise
        changes["outputs"] = json.dumps(outputs)
    # Same as SQLite: updating a missing record changes nothing.
    with _table() as table, contextlib.suppress(ResourceNotFoundError):
        table.update_entity(changes, mode=UpdateMode.MERGE)
