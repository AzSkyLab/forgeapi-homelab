"""Append-only audit trail: who did what, when, where, with what, and how it ended.

There is no API to change or delete an event. Accepted actions are recorded *before* they are
dispatched and the request fails if that write fails ("no audit, no action"); refusals and
worker outcomes are recorded best-effort, because the thing they describe has already happened.
Dry runs are not recorded: they change nothing. Storage follows the deployment store: SQLite
locally, a second table in the same storage account when hosted."""

import json
import logging
import sqlite3
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from app.settings import settings

REDACTED = "***"
_log = logging.getLogger("forgeapi.audit")


class Event(BaseModel):
    id: str  # time-ordered
    at: datetime
    actor: str  # caller's object ID, or "worker"
    action: str  # deployment.create | .update | .retry | .destroy | .access | .state
    outcome: str  # accepted | refused | succeeded | failed | destroyed
    status: int | None = None  # HTTP status of a refusal
    deployment_id: str | None = None
    pattern: str | None = None
    version: str | None = None
    business_unit: str | None = None
    environment: str | None = None
    detail: dict[str, Any] = {}


def safe_inputs(inputs: dict[str, Any], variables: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Inputs fit to keep: values of variables the pattern marks `sensitive` are redacted. When
    the pattern could not be read, only the input names are kept."""
    if variables is None:
        return {name: "(not recorded)" for name in inputs}
    sensitive = {v["name"] for v in variables if v.get("sensitive")}
    return {name: REDACTED if name in sensitive else value for name, value in inputs.items()}


def record(action: str, outcome: str, actor: str, **fields: Any) -> Event:
    event = Event(
        id=f"{time.time_ns():020d}-{uuid.uuid4().hex[:8]}",
        at=datetime.now(UTC), actor=actor, action=action, outcome=outcome, **fields,
    )  # fmt: skip
    _store().insert(event)
    return event


def record_quietly(action: str, outcome: str, actor: str, **fields: Any) -> None:
    """For events about something that already happened: never let the audit write mask it."""
    try:
        record(action, outcome, actor, **fields)
    except Exception as err:  # noqa: BLE001
        # Visible to operators, without the event's content (it may hold inputs).
        _log.warning(
            "audit event %s/%s could not be stored: %s", action, outcome, type(err).__name__
        )


def for_deployment(deployment_id: str, business_unit: str | None) -> list[Event]:
    return _store().query(deployment_id=deployment_id, business_units=_units(business_unit))


def query(business_units: list[str] | None, since: datetime | None, limit: int) -> list[Event]:
    """Newest first. `business_units=None` means every business unit (auditors, single-tenant)."""
    return _store().query(business_units=business_units, since=since, limit=limit)


def _units(business_unit: str | None) -> list[str] | None:
    return [business_unit] if business_unit else None


def _store():
    return _Table if settings.db_backend == "table" else _Sqlite


_COLUMNS = ("id", "at", "actor", "action", "outcome", "status", "deployment_id", "pattern",
            "version", "business_unit", "environment", "detail")  # fmt: skip


class _Sqlite:
    @staticmethod
    def _connect() -> sqlite3.Connection:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.data_dir / "forgeapi.db", timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, at TEXT NOT NULL, "
            "actor TEXT NOT NULL, action TEXT NOT NULL, outcome TEXT NOT NULL, status INTEGER, "
            "deployment_id TEXT, pattern TEXT, version TEXT, business_unit TEXT, "
            "environment TEXT, detail TEXT NOT NULL)"
        )
        return conn

    @classmethod
    def insert(cls, e: Event) -> None:
        row = e.model_dump()
        row["at"], row["detail"] = e.at.isoformat(), json.dumps(e.detail, default=str)
        with cls._connect() as conn:
            marks = ",".join("?" * len(_COLUMNS))
            conn.execute(f"INSERT INTO events VALUES ({marks})", [row[c] for c in _COLUMNS])

    @classmethod
    def query(cls, *, business_units=None, deployment_id=None, since=None, limit=500):
        where, args = [], []
        if business_units is not None:
            where.append(f"business_unit IN ({','.join('?' * len(business_units)) or 'NULL'})")
            args += business_units
        if deployment_id:
            where.append("deployment_id = ?")
            args.append(deployment_id)
        if since:
            where.append("at >= ?")
            args.append(since.isoformat())
        sql = "SELECT * FROM events" + (" WHERE " + " AND ".join(where) if where else "")
        with cls._connect() as conn:
            rows = conn.execute(f"{sql} ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return [Event(**{**dict(r), "detail": json.loads(r["detail"])}) for r in rows]


class _Table:
    """PartitionKey = business unit ("-" when none), RowKey = the time-ordered event ID."""

    @staticmethod
    def _table():
        from app import db_table

        return db_table.table(f"{settings.table_name}events")

    @classmethod
    def insert(cls, e: Event) -> None:
        entity = {k: ("" if v is None else v) for k, v in e.model_dump().items() if k != "id"}
        entity |= {"at": e.at.isoformat(), "detail": json.dumps(e.detail, default=str)[:30_000]}
        entity |= {"PartitionKey": e.business_unit or "-", "RowKey": e.id}
        with cls._table() as table:
            table.create_entity(entity)

    @classmethod
    def query(cls, *, business_units=None, deployment_id=None, since=None, limit=500):
        if business_units is not None and not business_units:
            return []
        clauses, parameters = [], {}
        if business_units is not None:
            names = " or ".join(f"PartitionKey eq @bu{i}" for i in range(len(business_units)))
            clauses.append(f"( {names} )")
            parameters |= {f"bu{i}": name for i, name in enumerate(business_units)}
        if deployment_id:
            clauses.append("deployment_id eq @dep")
            parameters["dep"] = deployment_id
        if since:
            clauses.append("at ge @since")
            parameters["since"] = since.isoformat()
        with cls._table() as table:
            found = table.query_entities(" and ".join(clauses) or "", parameters=parameters)
            events = [cls._to_event(e) for e in found]
        return sorted(events, key=lambda e: e.id, reverse=True)[:limit]

    @staticmethod
    def _to_event(e) -> Event:
        fields = {k: (None if e.get(k) in ("", None) else e[k]) for k in _COLUMNS if k != "id"}
        return Event(id=e["RowKey"], **{**fields, "detail": json.loads(e.get("detail") or "{}")})
