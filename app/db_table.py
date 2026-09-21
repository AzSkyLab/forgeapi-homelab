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
_PLACEMENT = ("business_unit", "environment", "subscription_id", "size", "requested_by")
_ensured: set[str] = set()  # tables this process has already made sure exist


def _table() -> TableClient:
    return table(settings.table_name)


def table(name: str) -> TableClient:
    if settings.table_connection_string:  # the Azurite emulator, tests only
        client = TableClient.from_connection_string(settings.table_connection_string, name)
    else:
        from app.azure_identity import credential

        client = TableClient(
            endpoint=f"https://{settings.table_storage_account}.table.core.windows.net",
            table_name=name,
            credential=credential(),
        )
    key = f"{client.url}/{name}"
    if key not in _ensured:
        with contextlib.suppress(ResourceExistsError):
            client.create_table()
        _ensured.add(key)
    return client


def _number(value: float | None) -> float | str:
    return "" if value is None else float(value)  # "" because a merge cannot store null


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
                **{name: getattr(d, name) or "" for name in _PLACEMENT},
                "injected": json.dumps(d.injected) if d.injected is not None else "",
                "estimated_monthly_cost": _number(d.estimated_monthly_cost),
                "created_at": d.created_at.isoformat(),
                "updated_at": d.updated_at.isoformat(),
            }
        )


def get(deployment_id: str) -> Deployment | None:
    with _table() as table:
        try:
            return _to_deployment(table.get_entity(_PARTITION, deployment_id))
        except ResourceNotFoundError:
            return None


def list_for(business_units: list[str] | None) -> list[Deployment]:
    if business_units is not None and not business_units:
        return []
    names = business_units or []
    parameters = {"pk": _PARTITION, **{f"bu{i}": name for i, name in enumerate(names)}}
    query = "PartitionKey eq @pk"
    if names:
        # Spaces around the parentheses matter: the SDK reads a parameter name up to the next space.
        any_unit = " or ".join(f"business_unit eq @bu{i}" for i in range(len(names)))
        query += f" and ( {any_unit} )"
    with _table() as table:
        found = [_to_deployment(e) for e in table.query_entities(query, parameters=parameters)]
    return sorted(found, key=lambda d: d.created_at, reverse=True)


def _to_deployment(e) -> Deployment:
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
        **{name: e.get(name) or None for name in _PLACEMENT},
        injected=json.loads(e["injected"]) if e.get("injected") else None,
        withheld_outputs=json.loads(e["withheld_outputs"]) if e.get("withheld_outputs") else None,
        estimated_monthly_cost=None
        if e.get("estimated_monthly_cost") in (None, "")
        else float(e["estimated_monthly_cost"]),
    )


def update(
    deployment_id: str,
    state: State,
    outputs: dict[str, Any] | None,
    error: str | None,
    withheld: list[str] | None,
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
        changes["withheld_outputs"] = json.dumps(withheld or [])
    # Same as SQLite: updating a missing record changes nothing.
    with _table() as table, contextlib.suppress(ResourceNotFoundError):
        table.update_entity(changes, mode=UpdateMode.MERGE)


def respec(
    deployment_id: str,
    inputs: dict[str, Any],
    version: str | None,
    commit: str | None,
    size: str | None,
    injected: dict[str, Any] | None,
    cost: float | None,
    now: datetime,
) -> None:
    changes = {
        "estimated_monthly_cost": _number(cost),
        "PartitionKey": _PARTITION,
        "RowKey": deployment_id,
        "inputs": json.dumps(inputs),
        "version": version or "",
        "commit_sha": commit or "",
        "size": size or "",
        "injected": json.dumps(injected) if injected is not None else "",
        "updated_at": now.isoformat(),
    }
    with _table() as table, contextlib.suppress(ResourceNotFoundError):
        table.update_entity(changes, mode=UpdateMode.MERGE)


def touch(deployment_id: str, now: datetime) -> None:
    changes = {"PartitionKey": _PARTITION, "RowKey": deployment_id, "updated_at": now.isoformat()}
    with _table() as table, contextlib.suppress(ResourceNotFoundError):
        table.update_entity(changes, mode=UpdateMode.MERGE)
