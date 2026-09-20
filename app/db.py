"""SQLite persistence: one `deployments` table, one short-lived connection per call."""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.models import Deployment, State
from app.settings import settings

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


def _connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.data_dir / "forgeapi.db", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(deployments)")}
    for column in ("version", "commit_sha"):
        if column not in columns:
            conn.execute(f"ALTER TABLE deployments ADD COLUMN {column} TEXT")
    return conn


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create(
    pattern: str, inputs: dict[str, Any], version: str | None = None, commit: str | None = None
) -> Deployment:
    deployment_id = f"dep_{uuid.uuid4().hex}"
    now = _now()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO deployments "
            "(id, pattern, version, commit_sha, inputs, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (deployment_id, pattern, version, commit, json.dumps(inputs), State.accepted, now, now),
        )
    return get(deployment_id)


def get(deployment_id: str) -> Deployment | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM deployments WHERE id = ?", (deployment_id,)).fetchone()
    if row is None:
        return None
    fields = dict(row)
    fields["commit"] = fields.pop("commit_sha")
    return Deployment(
        **{
            **fields,
            "inputs": json.loads(row["inputs"]),
            "outputs": json.loads(row["outputs"]) if row["outputs"] else None,
        }
    )


def update(
    deployment_id: str,
    state: State,
    *,
    outputs: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE deployments SET state = ?, outputs = COALESCE(?, outputs), error = ?, "
            "updated_at = ? WHERE id = ?",
            (state, json.dumps(outputs) if outputs is not None else None, error, _now(),
             deployment_id),
        )
