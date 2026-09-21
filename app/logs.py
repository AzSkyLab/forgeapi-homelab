"""Terraform logs, readable by the API even when the worker is a different container.

Local (SQLite) mode: a file next to the deployment's workspace. Table mode: chunks in a second
table of the same storage account, so no extra storage or role assignment is needed (the
account-level Table Data role covers it)."""

import time

from app.settings import settings

_CHUNK = 30_000  # a Table Storage string property holds 32K UTF-16 characters


def _path(deployment_id: str):
    from app import terraform

    return terraform.log_path(deployment_id)


def append(deployment_id: str, text: str) -> None:
    path = _path(deployment_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as log:
        log.write(text)
    if settings.db_backend != "table":
        return
    from app import db_table

    with db_table.table(f"{settings.table_name}logs") as table:
        for offset in range(0, len(text), _CHUNK):
            table.create_entity(
                {
                    "PartitionKey": deployment_id,
                    # Time-ordered and unique within a worker; chunks of one write stay in order.
                    "RowKey": f"{time.time_ns():020d}-{offset:08d}",
                    "text": text[offset : offset + _CHUNK],
                }
            )


def read(deployment_id: str) -> str:
    if settings.db_backend != "table":
        path = _path(deployment_id)
        return path.read_text() if path.exists() else ""
    from app import db_table

    with db_table.table(f"{settings.table_name}logs") as table:
        chunks = table.query_entities("PartitionKey eq @id", parameters={"id": deployment_id})
        return "".join(e["text"] for e in sorted(chunks, key=lambda e: e["RowKey"]))
