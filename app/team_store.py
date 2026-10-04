"""Teams (business units) in the operation ledger's SQLite file, so a backup of the data
directory includes them. One `teams` row per team (current doc) and an append-only
`team_revisions` history; revision rows are never updated or deleted (triggers enforce it).

A team doc is exactly one business-unit spec in the tenants.yaml shape. Operators are never
stored here: they come only from settings."""

import json
from datetime import UTC, datetime

from app import ledger


def now() -> str:
    return datetime.now(UTC).isoformat()


def dump(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, allow_nan=False)


def all_docs() -> list[tuple[str, str, dict, int]]:
    """(name, state, doc, revision) for every team: one cheap read, used by `tenants.load()`."""
    with ledger.connect(write=False) as con:
        rows = con.execute("SELECT name, state, doc, revision FROM teams ORDER BY name").fetchall()
    return [(r["name"], r["state"], json.loads(r["doc"]), r["revision"]) for r in rows]


def archived_since(name: str) -> str | None:
    """When the team's current archived run began (the first archived revision after the last
    active one), or None if it is not archived."""
    with ledger.connect(write=False) as con:
        row = con.execute("SELECT state FROM teams WHERE name=?", (name,)).fetchone()
        if row is None or row["state"] != "archived":
            return None
        found = con.execute(
            "SELECT min(at) FROM team_revisions WHERE name=? AND revision > "
            "COALESCE((SELECT max(revision) FROM team_revisions "
            "WHERE name=? AND state<>'archived'), 0)",
            (name, name),
        ).fetchone()
    return found[0]


def get(con, name: str) -> dict | None:
    row = con.execute("SELECT * FROM teams WHERE name=?", (name,)).fetchone()
    return _team(row) if row else None


def list_all(con) -> list[dict]:
    return [_team(r) for r in con.execute("SELECT * FROM teams ORDER BY name")]


def _team(row) -> dict:
    return {**dict(row), "doc": json.loads(row["doc"])}


def revisions(con, name: str, before: int | None, limit: int) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM team_revisions WHERE name=? AND revision<? ORDER BY revision DESC LIMIT ?",
        (name, before if before is not None else (1 << 62), limit),
    )
    return [_revision(r) for r in rows]


def revision(con, name: str, number: int) -> dict | None:
    row = con.execute(
        "SELECT * FROM team_revisions WHERE name=? AND revision=?", (name, number)
    ).fetchone()
    return _revision(row) if row else None


def _revision(row) -> dict:
    return {**dict(row), "doc": json.loads(row["doc"]), "summary": json.loads(row["summary"])}


def all_resources(con) -> list[dict]:
    """Every resource that still exists (anything not `destroyed`)."""
    found = (json.loads(r[0]) for r in con.execute("SELECT body FROM resources"))
    return [r for r in found if r.get("state") != "destroyed"]


def resources(con, name: str) -> list[dict]:
    """The team's resources that still exist."""
    return [r for r in all_resources(con) if r.get("business_unit") == name]


def write(
    con,
    actor: str,
    name: str,
    doc: dict,
    state: str,
    reason: str,
    summary: dict,
    previous: dict | None,
) -> dict:
    """Record `accepted`, then the new current row and its revision, all in the caller's
    transaction: if any statement fails nothing is kept, and there is no change without its
    audit event. Returns the new current row."""
    number = (previous["revision"] if previous else 0) + 1
    ledger.event(con, None, actor, f"team.{summary['action']} {name} r{number}", "accepted")
    stamp = now()
    text = dump(doc)
    if previous:
        con.execute(
            "UPDATE teams SET revision=?, state=?, doc=?, updated_at=?, updated_by=? WHERE name=?",
            (number, state, text, stamp, actor, name),
        )
    else:
        con.execute(
            "INSERT INTO teams VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, number, state, text, stamp, actor, stamp, actor),
        )
    con.execute(
        "INSERT INTO team_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, number, actor, stamp, reason, state, text, json.dumps(summary)),
    )
    return get(con, name)
