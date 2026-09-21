"""Deployment records. Three operations (`create`, `get`, `update`) over a pluggable store:
SQLite for local runs, Azure Table Storage when hosted (`FORGEAPI_DB_BACKEND`)."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app import db_table
from app.models import Deployment, State
from app.settings import settings

_PLACEMENT = ("business_unit", "environment", "subscription_id", "size", "requested_by")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS deployments (
    id         TEXT PRIMARY KEY,
    pattern    TEXT NOT NULL,
    inputs     TEXT NOT NULL,
    state      TEXT NOT NULL,
    outputs    TEXT,
    error      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def create(
    pattern: str,
    inputs: dict[str, Any],
    version: str | None = None,
    commit: str | None = None,
    **placement: Any,
) -> Deployment:
    """`placement`: business_unit, environment, subscription_id, size, injected, requested_by,
    estimated_monthly_cost."""
    now = datetime.now(UTC)
    deployment = Deployment(
        id=f"dep_{uuid.uuid4().hex}", pattern=pattern, version=version, commit=commit,
        inputs=inputs, state=State.accepted, created_at=now, updated_at=now, **placement,
    )  # fmt: skip
    _store().insert(deployment)
    return deployment


def get(deployment_id: str) -> Deployment | None:
    return _store().get(deployment_id)


def update(
    deployment_id: str,
    state: State,
    *,
    outputs: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Set state and error; `outputs` is kept unless a new value is given."""
    _store().update(deployment_id, state, outputs, error, datetime.now(UTC))


def respec(
    deployment_id: str,
    inputs: dict[str, Any],
    version: str | None,
    commit: str | None,
    size: str | None = None,
    injected: dict[str, Any] | None = None,
    cost: float | None = None,
):
    """Change what the deployment should be; the next run applies it against the same state."""
    now = datetime.now(UTC)
    _store().respec(deployment_id, inputs, version, commit, size, injected, cost, now)


def list_for(business_units: list[str] | None) -> list[Deployment]:
    """Newest first. `None` means no business-unit filter (single-tenant mode)."""
    return _store().list_for(business_units)


def _store():
    return db_table if settings.db_backend == "table" else _Sqlite


class _Sqlite:
    """One short-lived connection per call."""

    @staticmethod
    def _connect() -> sqlite3.Connection:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.data_dir / "forgeapi.db", timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(deployments)")}
        for column in ("version", "commit_sha", *_PLACEMENT, "injected"):
            if column not in columns:
                conn.execute(f"ALTER TABLE deployments ADD COLUMN {column} TEXT")
        for column in ("estimated_monthly_cost",):
            if column not in columns:
                conn.execute(f"ALTER TABLE deployments ADD COLUMN {column} REAL")
        return conn

    @classmethod
    def insert(cls, d: Deployment) -> None:
        with cls._connect() as conn:
            conn.execute(
                "INSERT INTO deployments (id, pattern, version, commit_sha, inputs, state, "
                "created_at, updated_at, business_unit, environment, subscription_id, size, "
                "requested_by, injected, estimated_monthly_cost) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d.id, d.pattern, d.version, d.commit, json.dumps(d.inputs), d.state,
                 d.created_at.isoformat(), d.updated_at.isoformat(),
                 *(getattr(d, name) for name in _PLACEMENT),
                 json.dumps(d.injected) if d.injected is not None else None,
                 d.estimated_monthly_cost),
            )  # fmt: skip

    @classmethod
    def get(cls, deployment_id: str) -> Deployment | None:
        with cls._connect() as conn:
            row = conn.execute(
                "SELECT * FROM deployments WHERE id = ?", (deployment_id,)
            ).fetchone()
        return cls._to_deployment(row) if row else None

    @staticmethod
    def _to_deployment(row: sqlite3.Row) -> Deployment:
        fields = dict(row)
        fields["commit"] = fields.pop("commit_sha")
        fields["inputs"] = json.loads(fields["inputs"])
        for name in ("outputs", "injected"):
            fields[name] = json.loads(fields[name]) if fields[name] else None
        return Deployment(**fields)

    @classmethod
    def list_for(cls, business_units: list[str] | None) -> list[Deployment]:
        query, args = "SELECT * FROM deployments", []
        if business_units is not None:
            marks = ",".join("?" * len(business_units)) or "NULL"
            query, args = f"{query} WHERE business_unit IN ({marks})", list(business_units)
        with cls._connect() as conn:
            rows = conn.execute(f"{query} ORDER BY created_at DESC", args).fetchall()
        return [cls._to_deployment(row) for row in rows]

    @classmethod
    def respec(cls, deployment_id, inputs, version, commit, size, injected, cost, now) -> None:
        with cls._connect() as conn:
            conn.execute(
                "UPDATE deployments SET inputs = ?, version = ?, commit_sha = ?, size = ?, "
                "injected = ?, estimated_monthly_cost = ?, updated_at = ? WHERE id = ?",
                (json.dumps(inputs), version, commit, size,
                 json.dumps(injected) if injected is not None else None, cost,
                 now.isoformat(), deployment_id),
            )  # fmt: skip

    @classmethod
    def update(cls, deployment_id, state, outputs, error, now) -> None:
        with cls._connect() as conn:
            conn.execute(
                "UPDATE deployments SET state = ?, outputs = COALESCE(?, outputs), error = ?, "
                "updated_at = ? WHERE id = ?",
                (state, json.dumps(outputs) if outputs is not None else None, error,
                 now.isoformat(), deployment_id),
            )  # fmt: skip
