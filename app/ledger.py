"""Transactional operation ledger and append-only audit on durable local disk.

Acceptance precedes Temporal dispatch. Retrying the same request closes a dispatch gap.
Interrupted execution is uncertain and is never automatically replayed.
"""

import contextlib
import hashlib
import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import HTTPException

from app.contracts import OperationError, plan_expires_at
from app.settings import settings
from app.terraform import deployment_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, actor TEXT NOT NULL,
  request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
  state TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(actor, request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS resource_busy ON operations(resource_id)
  WHERE state NOT IN ('succeeded', 'failed');
CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS apps (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, request_key TEXT NOT NULL,
  fingerprint TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(actor, request_key)
);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, actor TEXT NOT NULL,
  action TEXT NOT NULL, outcome TEXT NOT NULL, timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_operation_seq ON events(operation_id, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TABLE IF NOT EXISTS teams (
  name TEXT PRIMARY KEY, revision INTEGER NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'archived')), doc TEXT NOT NULL,
  created_at TEXT NOT NULL, created_by TEXT NOT NULL,
  updated_at TEXT NOT NULL, updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_revisions (
  name TEXT NOT NULL, revision INTEGER NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
  reason TEXT NOT NULL, state TEXT NOT NULL, doc TEXT NOT NULL, summary TEXT NOT NULL,
  PRIMARY KEY (name, revision)
);
CREATE TRIGGER IF NOT EXISTS team_revisions_no_update BEFORE UPDATE ON team_revisions
BEGIN SELECT RAISE(ABORT, 'team revisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS team_revisions_no_delete BEFORE DELETE ON team_revisions
BEGIN SELECT RAISE(ABORT, 'team revisions are append-only'); END;
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


@contextmanager
def connect(*, write: bool = True):
    if settings.db_backend != "sqlite":
        raise HTTPException(503, "operation ledger requires a local SQLite disk; Table is legacy")
    try:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise HTTPException(503, "operation ledger unavailable") from None
    con = sqlite3.connect(settings.data_dir / "operations.sqlite", timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def event(con, operation_id, actor, action, outcome):
    con.execute(
        "INSERT INTO events VALUES (NULL, ?, ?, ?, ?, ?)",
        (operation_id, actor, action, outcome, now()),
    )


def refused(actor: str, action: str):
    with connect() as con:
        event(con, None, actor, action, "refused")


def fingerprint(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def accounting_amount(value):
    """Reject corrupt stored monetary values before budget arithmetic."""
    if value is None:
        return 0
    try:
        valid = (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and value >= 0
        )
    except OverflowError:
        valid = False
    if not valid:
        raise HTTPException(503, "invalid budget accounting data")
    return value


def replay(actor: str, key: str, body: dict) -> dict | None:
    with connect(write=False) as con:
        return _replay(con, actor, key, fingerprint(body))


def _replay(con, actor, key, digest):
    row = con.execute(
        "SELECT * FROM operations WHERE actor=? AND request_key=?", (actor, key)
    ).fetchone()
    if row:
        if row["fingerprint"] != digest:
            raise OperationError(
                409,
                "idempotency key already used for different intent",
                "idempotency_key_reused",
            )
        return json.loads(row["body"])


def _save(con, op):
    op["updated_at"] = now()
    con.execute(
        "UPDATE operations SET state=?, body=? WHERE id=?", (op["state"], json.dumps(op), op["id"])
    )


def _reserved(con, unit, environment, exclude_id=None):
    resources = [json.loads(r[0]) for r in con.execute("SELECT body FROM resources")]
    return accounting_amount(
        sum(
            accounting_amount(r.get("estimated_monthly_cost"))
            for r in resources
            if r.get("business_unit") == unit
            and r.get("environment") == environment
            and r["id"] != exclude_id
            and r.get("state") != "destroyed"
        )
    )


def reserved(unit: str, environment: str) -> float:
    with connect(write=False) as con:
        return _reserved(con, unit, environment)


def _expired(op: dict) -> bool:
    expires = plan_expires_at(op)
    return expires is not None and datetime.fromisoformat(now()) > expires


def _unlink_plan(op: dict):
    with contextlib.suppress(OSError):  # a leftover plan is harmless once failed
        (deployment_dir(op["resource_id"]) / "work" / "tfplan").unlink(missing_ok=True)


def _restore_reservation(con, op: dict):
    """Give back what acceptance reserved for a deploy that ended `failed`: the resource returns
    to its previous amount (0 for a brand-new resource). `failed` always means nothing was
    changed (anything that may have changed infrastructure is `uncertain`, which keeps the
    reservation). One path for discard, expiry, failed finish and reconcile-as-failed. Destroys
    and drift checks reserved nothing; an operation accepted before the previous amount was
    recorded keeps its reservation."""
    if op["action"] in ("destroy", "drift_check") or "previous_estimated_monthly_cost" not in op:
        return
    row = con.execute("SELECT body FROM resources WHERE id=?", (op["resource_id"],)).fetchone()
    if row:
        resource = json.loads(row[0])
        resource["estimated_monthly_cost"] = op["previous_estimated_monthly_cost"]
        con.execute(
            "UPDATE resources SET body=? WHERE id=?", (json.dumps(resource), op["resource_id"])
        )


def _release_plan(
    con, op: dict, actor: str, action: str, error: str, flag: str, unlink: bool = True
):
    """Fail a planned operation that will not execute, releasing its resource and reservation.
    Shared by discard and expiry. With unlink=False the caller removes the plan after commit."""
    event(con, op["id"], actor, action, "accepted")
    op["state"] = "failed"
    op["error"] = error
    op[flag] = True
    _save(con, op)
    # The resource stays reserved until commit, so nothing else is writing this path.
    if unlink:
        _unlink_plan(op)
    _restore_reservation(con, op)


def _expire(con, op: dict, actor: str, unlink: bool = True):
    _release_plan(
        con,
        op,
        actor,
        "operation.expire",
        "plan expired before execution; submit a new intent",
        "expired",
        unlink,
    )


def reference_error(con, resource_id: str) -> str | None:
    """Why a resource cannot be referenced right now, or None. Shared by policy, accept and
    execute so every check agrees."""
    row = con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
    source = json.loads(row[0]) if row else {}
    if source.get("state") != "ready":
        return "referenced resource is not ready"
    latest = con.execute(
        "SELECT body FROM operations WHERE id=?", (source["operation_id"],)
    ).fetchone()
    latest = json.loads(latest[0]) if latest else {}
    if latest.get("action") == "destroy" and latest.get("state") not in ("succeeded", "failed"):
        return "referenced resource is being destroyed"
    return None


def _referenced(con, resource_id: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM resources WHERE id<>? AND json_extract(body,'$.state')='ready' AND "
            "EXISTS (SELECT 1 FROM json_each(body,'$.input_refs') "
            "WHERE json_extract(value,'$.resource_id')=?)",
            (resource_id, resource_id),
        ).fetchone()
        is not None
    )


def accept(
    actor: str,
    key: str,
    intent: dict,
    resolved: dict,
    budget: dict | None,
    caller_groups=None,
) -> dict:
    with connect() as con:
        op, expired = _accept(con, actor, key, intent, resolved, budget, caller_groups)
    if expired:
        _unlink_plan(expired)
    return op


def _check_placement(con, intent: dict, resolved: dict, stamp, caller_groups) -> None:
    """Inside the acceptance transaction, re-read the business unit and refuse work that a team
    edit has made stale since validation: archived (anything but a destroy), the caller no longer
    allowed to deploy there, a different team revision (database source) or a different resolved
    placement. Destroys and drift checks only need the resource they already name."""
    from app import cloud_targets, team_store, tenants

    unit_name = resolved.get("business_unit")
    if not tenants.enabled() or not unit_name or intent["action"] in ("destroy", "drift_check"):
        return
    if tenants.db_source():
        row = team_store.get(con, unit_name)
        unit = (
            tenants.build_unit(
                unit_name, row["doc"], archived=row["state"] == "archived", revision=row["revision"]
            )
            if row
            else None
        )
        moved = unit is not None and stamp is not None and unit.revision != stamp
    else:
        unit, moved = tenants.load().get(unit_name), False
    env = unit.environments.get(resolved.get("environment")) if unit else None
    stale = unit is None or env is None or unit.archived or moved
    if not stale and caller_groups is not None:
        stale = not unit.can_deploy(tenants.Caller("", frozenset(caller_groups)), env)
    if not stale:
        try:
            target = cloud_targets.select(tenants.Placement(unit, env), resolved.get("cloud"))
        except (tenants.TenancyError, OperationError):
            stale = True
        else:
            subscription = (
                target.get("subscription_id") if target else env.subscription_id
            )
            stale = (target or None) != (resolved.get("cloud_target") or None) or (
                subscription != resolved.get("subscription_id")
            )
    if stale:
        raise OperationError(
            409,
            "the business unit changed since this was validated; validate and submit again",
            "placement_stale",
            next_action="validate_and_resubmit",
        )


def _accept(
    con, actor, key, intent, resolved, budget, caller_groups=None
) -> tuple[dict, dict | None]:
    """Accept one intent inside the caller's transaction; returns (operation, a blocker that
    expired, whose plan file the caller removes after commit). `accept_app` runs several of
    these in one transaction so an app is accepted entirely or not at all."""
    if existing := _replay(con, actor, key, fingerprint(intent)):
        return existing, None
    resolved = dict(resolved)
    _check_placement(con, intent, resolved, resolved.pop("_team_revision", None), caller_groups)
    resource_id = intent.get("resource_id") or f"res_{uuid.uuid4().hex}"
    busy = con.execute(
        "SELECT id, body FROM operations WHERE resource_id=?"
        " AND state NOT IN ('succeeded','failed')",
        (resource_id,),
    ).fetchone()
    expired = None
    if busy and _expired(blocker := json.loads(busy["body"])):
        _expire(con, blocker, actor, unlink=False)  # unlinked after commit, if we get there
        expired, busy = blocker, None
    if busy:
        raise OperationError(
            409,
            "resource has an active or uncertain operation; inspect it",
            "resource_busy",
            operation_id=busy["id"],
            next_action="inspect_blocking_operation",
        )
    row = con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
    previous = json.loads(row[0]) if row else None
    if previous and previous["operation_id"] != resolved.get("previous_operation_id"):
        raise OperationError(
            409,
            "resource changed during validation; submit again",
            "resource_changed",
            next_action="validate_and_resubmit",
        )
    check = intent["action"] == "drift_check"
    if check and (not previous or previous.get("state") != "ready"):
        raise OperationError(
            409, "only a ready resource can be checked for drift", "resource_not_ready"
        )
    if intent["action"] not in ("destroy", "drift_check"):
        previous_cost = accounting_amount((previous or {}).get("estimated_monthly_cost"))
        requested_cost = accounting_amount(resolved.get("estimated_monthly_cost"))
    if budget:
        # Reservations include planned, failed and uncertain work until a destroy succeeds.
        used = _reserved(
            con, resolved.get("business_unit"), resolved.get("environment"), resource_id
        )
        reservation = max(previous_cost, requested_cost)
        total = accounting_amount(
            used + accounting_amount(budget["legacy_committed"]) + reservation
        )
        if total > budget["limit"]:
            raise OperationError(
                403, "intent would exceed the estimated monthly budget", "budget_exceeded"
            )
    if intent["action"] == "destroy" and _referenced(con, resource_id):
        raise OperationError(
            409,
            "resource is referenced by other resources; update or destroy them first",
            "resource_referenced",
        )
    for ref in (resolved.get("input_refs") or {}).values():
        if problem := reference_error(con, ref["resource_id"]):
            raise OperationError(409, problem, "reference_not_ready")
    stamp = now()
    op = {
        **resolved,
        "id": f"op_{uuid.uuid4().hex}",
        "resource_id": resource_id,
        "actor": actor,
        "action": intent["action"],
        "state": "queued",
        "created_at": stamp,
        "updated_at": stamp,
        "plan_digest": None,
        "changes": None,
        "outputs": None,
        "withheld_outputs": None,
        "error": None,
    }
    if op["action"] not in ("destroy", "drift_check"):
        # Needed to restore the reservation if this plan is discarded before execution.
        op["previous_estimated_monthly_cost"] = previous_cost
    event(con, op["id"], actor, "operation.create", "accepted")
    con.execute(
        "INSERT INTO operations VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            op["id"],
            resource_id,
            actor,
            key,
            fingerprint(intent),
            "queued",
            json.dumps(op),
        ),
    )
    # Retain the known deployed definition until success; reserve its new cost immediately.
    row = con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
    resource = (
        json.loads(row[0])
        if row
        else {**resolved, "id": resource_id, "actor": actor, "resource_created_at": stamp}
    )
    if op["action"] not in ("destroy", "drift_check"):
        resource["estimated_monthly_cost"] = max(previous_cost, requested_cost)
    if not check:
        # A drift check reserves nothing and leaves `latest_operation_id` on the last real
        # change; the resource is kept busy by the operations table, not by this pointer.
        resource["operation_id"] = op["id"]
        con.execute(
            "INSERT OR REPLACE INTO resources VALUES (?, ?)", (resource_id, json.dumps(resource))
        )
    return op, expired


def get(operation_id: str) -> dict | None:
    with connect(write=False) as con:
        row = con.execute("SELECT body FROM operations WHERE id=?", (operation_id,)).fetchone()
        return json.loads(row[0]) if row else None


def resource(resource_id: str) -> dict | None:
    with connect(write=False) as con:
        row = con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
        return json.loads(row[0]) if row else None


def referenced(resource_id: str) -> bool:
    """True when a ready resource was built from this resource's outputs."""
    with connect(write=False) as con:
        return _referenced(con, resource_id)


def list_resources(
    actor: str,
    units: list[str] | None,
    after: str | None,
    limit: int,
    pattern: str | None = None,
    environment: str | None = None,
    state: str | None = None,
    label: str | None = None,
    drift_status: str | None = None,
) -> list[dict]:
    with connect(write=False) as con:
        # accept() uses INSERT OR REPLACE, which changes rowid; page by id, never by rowid.
        if units is None:
            condition, values = "json_extract(body, '$.actor')=?", [actor]
        else:
            condition = "json_extract(body, '$.business_unit') IN (SELECT value FROM json_each(?))"
            values = [json.dumps(units)]
        for field, wanted in (("pattern", pattern), ("environment", environment)):
            if wanted is not None:
                condition += f" AND json_extract(body, '$.{field}')=?"
                values.append(wanted)
        if state is not None:
            condition += " AND COALESCE(json_extract(body, '$.state'), 'pending')=?"
            values.append(state)
        if drift_status is not None:
            condition += " AND json_extract(body, '$.drift_status')=?"
            values.append(drift_status)
        if label is not None:
            condition += (
                " AND EXISTS (SELECT 1 FROM json_each(body, '$.labels') WHERE key=? AND value=?)"
            )
            values.extend(label.split("=", 1))
        if after is not None:
            condition += " AND id>?"
            values.append(after)
        rows = con.execute(
            f"SELECT body FROM resources WHERE {condition} ORDER BY id LIMIT ?",
            [*values, limit],
        )
        return [json.loads(row[0]) for row in rows]


def list_operations(
    actor: str,
    units: list[str] | None,
    after: int,
    limit: int,
    before: str | None = None,
    resource_id: str | None = None,
    state: str | None = None,
    action: str | None = None,
    include_checks: bool = False,
) -> list[dict]:
    with connect(write=False) as con:
        # Filter inside SQL before applying pagination; never return another unit's records.
        if units is None:
            condition, values = "actor=?", [actor]
        else:
            condition = "json_extract(body, '$.business_unit') IN (SELECT value FROM json_each(?))"
            values = [json.dumps(units)]
        if resource_id is not None:
            condition += " AND resource_id=?"
            values.append(resource_id)
        if state is not None:
            condition += " AND state=?"
            values.append(state)
        # Drift checks (a newer `action` value) stay out of default listings: a v1 client that
        # predates them must never meet an action it does not know. Ask for them explicitly.
        if action is not None:
            condition += " AND json_extract(body, '$.action')=?"
            values.append(action)
        elif not include_checks:
            condition += " AND json_extract(body, '$.action')<>'drift_check'"
        if before is not None:
            anchor = con.execute(
                f"SELECT rowid FROM operations WHERE {condition} AND id=?",
                [*values, before],
            ).fetchone()
            if anchor is None:
                raise HTTPException(404, "resource or operation not found")
            condition += " AND rowid<?"
            values.append(anchor[0])
        rows = con.execute(
            f"SELECT body FROM operations WHERE {condition} ORDER BY rowid DESC LIMIT ? OFFSET ?",
            [*values, limit, after],
        )
        return [json.loads(row[0]) for row in rows]


def drift_candidates(checked_before: str, limit: int) -> list[dict]:
    """Ready resources with no operation in flight whose last drift check (or attempt) is older
    than `checked_before` or never happened; the least recently checked first."""
    with connect(write=False) as con:
        rows = con.execute(
            "SELECT body FROM resources r WHERE json_extract(body, '$.state')='ready' AND "
            "(json_extract(body, '$.drift_checked_at') IS NULL OR "
            "json_extract(body, '$.drift_checked_at') < ?) AND NOT EXISTS "
            "(SELECT 1 FROM operations o WHERE o.resource_id=r.id "
            "AND o.state NOT IN ('succeeded', 'failed')) "
            "ORDER BY COALESCE(json_extract(body, '$.drift_checked_at'), ''), id LIMIT ?",
            (checked_before, limit),
        )
        return [json.loads(row[0]) for row in rows]


def queued_operations(actor: str, action: str) -> list[str]:
    """IDs of this actor's `queued` operations of one action: accepted, plan not yet started."""
    with connect(write=False) as con:
        rows = con.execute(
            "SELECT id FROM operations WHERE actor=? AND state='queued' "
            "AND json_extract(body, '$.action')=? ORDER BY rowid",
            (actor, action),
        )
        return [row[0] for row in rows]


def outstanding_count(actor: str, action: str) -> int:
    """This actor's operations of one action that are accepted but not yet finished
    (`queued` or `planning`): what a sweep must not add to."""
    with connect(write=False) as con:
        return con.execute(
            "SELECT count(*) FROM operations WHERE actor=? AND state IN ('queued', 'planning') "
            "AND json_extract(body, '$.action')=?",
            (actor, action),
        ).fetchone()[0]


def stamp_drift_refusal(resource_id: str, reason: str, unknown: bool) -> None:
    """A sweep could not accept a check: record the attempt time (so the resource is not
    retried until the next interval) and why. Nothing else about the resource changes."""
    with connect() as con:
        row = con.execute("SELECT body FROM resources WHERE id=?", (resource_id,)).fetchone()
        if not row:
            return
        resource = json.loads(row[0])
        if unknown:
            resource["drift_status"] = "unknown"
            resource["drift"] = None
        resource["drift_checked_at"] = now()
        resource["drift_error"] = reason
        con.execute("UPDATE resources SET body=? WHERE id=?", (json.dumps(resource), resource_id))


def _execute(con, operation_id: str, digest: str, actor: str) -> tuple[dict, bool]:
    """Queue one planned operation's apply inside the caller's transaction; returns
    (operation, whether it was found expired and has just been failed)."""
    row = con.execute("SELECT body FROM operations WHERE id=?", (operation_id,)).fetchone()
    op = json.loads(row[0])
    if op.get("plan_digest") != digest:
        raise OperationError(
            409, "plan digest does not match the saved plan", "plan_digest_mismatch"
        )
    if op["state"] in {"apply_queued", "applying", "succeeded"}:
        return op, False  # intrinsically idempotent: the exact operation is already executing
    if op["state"] == "planned" and _expired(op):
        _expire(con, op, actor)
        return op, True
    if op["state"] != "planned":
        raise OperationError(409, "only a planned operation can execute", "operation_not_planned")
    if op["action"] != "destroy":
        for ref in (op.get("input_refs") or {}).values():
            if problem := reference_error(con, ref["resource_id"]):
                raise OperationError(409, problem, "reference_not_ready")
    event(con, operation_id, actor, "operation.execute", "accepted")
    op["state"] = "apply_queued"
    _save(con, op)
    return op, False


def _plan_expired(op: dict) -> OperationError:
    return OperationError(
        409,
        op["error"],
        "plan_expired",
        operation_id=op["id"],
        next_action="validate_and_resubmit",
    )


def execute(operation_id: str, digest: str, actor: str) -> dict:
    with connect() as con:
        op, expired = _execute(con, operation_id, digest, actor)
    if expired:
        # Raised after the transaction so the expiry commits even though the request is refused.
        raise _plan_expired(op)
    return op


class _Expired(Exception):
    """Internal: rolls back an all-or-nothing execute when one plan turned out to be expired."""


def execute_many(items: dict[str, str], actor: str, app_id: str | None = None) -> list[dict]:
    """Queue several planned operations together, all or none: one wrong digest, state or
    expired plan commits nothing (an expired plan is then failed on its own, as `execute`). With
    `app_id`, the app's approval event commits in the same transaction."""
    try:
        with connect() as con:
            results = []
            for operation_id, digest in items.items():
                op, expired = _execute(con, operation_id, digest, actor)
                if expired:
                    raise _Expired(operation_id)
                results.append(op)
            if app_id:
                event(con, app_id, actor, "app.approve", "accepted")
            return results
    except _Expired as stop:
        execute(stop.args[0], items[stop.args[0]], actor)  # fails the expired plan; raises
        raise OperationError(409, "plan expired before execution", "plan_expired") from None


def discard(operation_id: str, actor: str) -> dict:
    """Fail a reviewed plan without applying it, releasing its resource and reservation."""
    with connect() as con:
        row = con.execute("SELECT body FROM operations WHERE id=?", (operation_id,)).fetchone()
        op = json.loads(row[0])
        if op.get("discarded"):
            return op  # intrinsically idempotent: already discarded, no second event
        if op["state"] != "planned":
            raise OperationError(
                409, "only a planned operation can be discarded", "operation_not_planned"
            )
        _release_plan(
            con,
            op,
            actor,
            "operation.discard",
            "plan discarded before execution",
            "discarded",
        )
        return op


def claim(operation_id: str, phase: str) -> dict | None:
    expected, running = phase_states(phase)
    with connect() as con:
        row = con.execute(
            "SELECT body FROM operations WHERE id=? AND state=?", (operation_id, expected)
        ).fetchone()
        if not row:
            return None
        op = json.loads(row[0])
        op["state"] = running
        _save(con, op)
        event(con, op["id"], "temporal-worker", "operation.state", running)
        return op


def phase_states(phase: str) -> tuple[str, str]:
    return {"plan": ("queued", "planning"), "apply": ("apply_queued", "applying")}[phase]


def _record_drift_check(con, op: dict, state: str):
    """Write a finished drift check's verdict onto the resource and nothing else: cost, state,
    managed objects, version and `latest_operation_id` are left exactly as they were. A failed
    check leaves the status `unknown` (it could not tell) and still stamps the time, which also
    keeps the sweep from retrying a persistently failing resource every cycle."""
    row = con.execute("SELECT body FROM resources WHERE id=?", (op["resource_id"],)).fetchone()
    if not row:
        return
    resource = json.loads(row[0])
    drift = op.get("drift") or []
    if state == "succeeded":
        resource["drift_status"] = "drifted" if drift else "in_sync"
        resource["drift"] = drift
        resource.pop("drift_error", None)
    else:
        resource["drift_status"] = "unknown"
        resource["drift"] = None
    resource["drift_checked_at"] = now()
    con.execute("UPDATE resources SET body=? WHERE id=?", (json.dumps(resource), op["resource_id"]))


def _mark_succeeded_resource(con, op: dict, drift_status: str = "in_sync"):
    """Rewrite the resource row for a successful deploy or destroy. Shared by `finish` and
    `reconcile`, which both land an operation in `succeeded`. A fresh apply matches state, so
    drift resets to `in_sync` (reconcile passes `unknown`: nobody ran a check)."""
    row = con.execute("SELECT body FROM resources WHERE id=?", (op["resource_id"],)).fetchone()
    previous = json.loads(row[0]) if row else {}
    created = previous.get("resource_created_at")
    destroyed = op["action"] == "destroy"
    # Provenance set once at creation survives later updates, which do not restate it.
    resource = {
        **op,
        "id": op["resource_id"],
        "operation_id": op["id"],
        "state": "destroyed" if op["action"] == "destroy" else "ready",
        "resource_created_at": created,
        # Objects in state after this success: what its saved plan left, none after a destroy.
        "managed_objects": [] if op["action"] == "destroy" else op.get("planned_objects"),
        # `op["drift"]` is what the plan's refresh found before this apply; the resource's own
        # `drift` fields describe state *now*, so they never inherit it.
        "drift_status": None if destroyed else drift_status,
        "drift_checked_at": None if destroyed else now(),
        "drift": None if destroyed else [],
    }
    if not op.get("promoted_from") and previous.get("promoted_from"):
        resource["promoted_from"] = previous["promoted_from"]
    con.execute("UPDATE resources SET body=? WHERE id=?", (json.dumps(resource), op["resource_id"]))


def finish(op: dict, state: str, **result):
    with connect() as con:
        current = con.execute("SELECT state FROM operations WHERE id=?", (op["id"],)).fetchone()
        if not current or current[0] != op["state"]:
            return  # Fence a late activity result after its timeout was recorded.
        op = {**op, "state": state, **result}
        if state == "planned":
            op["planned_at"] = now()
        _save(con, op)
        event(con, op["id"], "temporal-worker", "operation.state", state)
        if op["action"] == "drift_check":
            if state in ("succeeded", "failed"):
                _record_drift_check(con, op, state)
        elif state == "succeeded":
            _mark_succeeded_resource(con, op)
        elif state == "failed":
            _restore_reservation(con, op)


def reconcile(operation_id: str, actor: str, outcome: str, reason: str) -> dict:
    """A human operator resolves an `uncertain` operation after inspecting Terraform state and
    provider evidence themselves. This records the decision and releases the resource's
    reservation; it runs no Terraform."""
    with connect() as con:
        row = con.execute("SELECT body FROM operations WHERE id=?", (operation_id,)).fetchone()
        op = json.loads(row[0])
        existing = op.get("reconciliation")
        if existing:
            if existing["outcome"] == outcome:
                return op  # intrinsically idempotent: already reconciled, no second event
            raise HTTPException(409, "operation already reconciled with a different outcome")
        if op["state"] != "uncertain":
            raise OperationError(
                409, "only an uncertain operation can be reconciled", "operation_not_uncertain"
            )
        if outcome == "succeeded" and not op.get("plan_digest"):
            raise HTTPException(
                409, "an operation that never executed cannot be reconciled as succeeded"
            )
        event(con, operation_id, actor, "operation.reconcile", "accepted")
        state = "succeeded" if outcome == "succeeded" else "failed"
        op = {
            **op,
            "state": state,
            "error": None if outcome == "succeeded" else "reconciled as failed by an operator",
            "reconciliation": {"outcome": outcome, "actor": actor, "reason": reason, "at": now()},
        }
        _save(con, op)
        event(con, op["id"], actor, "operation.state", state)  # a human decision, not the worker
        if state == "succeeded":
            _mark_succeeded_resource(con, op, drift_status="unknown")
        else:
            _restore_reservation(con, op)  # the operator found nothing changed
        return op


def interrupted(operation_id: str, phase: str):
    expected, running = phase_states(phase)
    with connect() as con:
        row = con.execute(
            "SELECT body FROM operations WHERE id=? AND state IN (?, ?)",
            (operation_id, expected, running),
        ).fetchone()
        if row:
            op = json.loads(row[0])
            if op["action"] == "drift_check":
                # A check never changes infrastructure, so an interrupted one is simply failed:
                # nothing to reconcile, and the resource is free for the next operation.
                op.update(state="failed", error="drift check interrupted; run it again")
                _save(con, op)
                event(con, operation_id, "temporal-worker", "operation.state", "failed")
                _record_drift_check(con, op, "failed")
                return
            op.update(
                state="uncertain", error="activity interrupted; operator reconciliation required"
            )
            _save(con, op)
            event(con, operation_id, "temporal-worker", "operation.state", "uncertain")


def events(operation_id: str, after: int, limit: int) -> list[dict]:
    with connect(write=False) as con:
        return [
            dict(row)
            for row in con.execute(
                "SELECT * FROM events WHERE operation_id=? AND seq>? ORDER BY seq LIMIT ?",
                (operation_id, after, limit),
            )
        ]


# --- App rollouts ------------------------------------------------------------------------------
# An app row stores only what cannot be derived: who asked, the spec, and which operations are its
# members. Its state is computed from those operations (see app.apps.derive_state).


def system_key(*parts: str) -> str:
    """The idempotency key the platform derives for one of an app's member operations. `|` is
    outside what `Idempotency-Key` accepts, so no caller's key can ever equal one (a caller who
    did would otherwise make the later acceptance fail with `idempotency_key_reused`)."""
    return "|".join(parts)


def _replay_app(con, actor, key, digest):
    row = con.execute("SELECT * FROM apps WHERE actor=? AND request_key=?", (actor, key)).fetchone()
    if row:
        if row["fingerprint"] != digest:
            raise OperationError(
                409,
                "idempotency key already used for different intent",
                "idempotency_key_reused",
            )
        return json.loads(row["body"])


def replay_app(actor: str, key: str, body: dict) -> dict | None:
    with connect(write=False) as con:
        return _replay_app(con, actor, key, fingerprint(body))


def accept_app(actor: str, key: str, material: dict, replicas: list[tuple], base: dict) -> dict:
    """Accept every replica operation and the app record in ONE transaction: if any replica is
    refused (budget, busy resource, ...) the whole transaction rolls back, so no operation,
    reservation or app row exists and nothing needs compensating. `replicas` holds
    `(intent, resolved, budget)` per replica, in request order."""
    expired = []
    with connect() as con:
        if existing := _replay_app(con, actor, key, fingerprint(material)):
            return existing
        app_id = f"app_{uuid.uuid4().hex}"
        members = []
        for index, (intent, resolved, budget) in enumerate(replicas):
            op, blocker = _accept(
                con, actor, system_key(app_id, "replica", str(index)), intent, resolved, budget
            )
            members.append(
                {"index": index, "operation_id": op["id"], "resource_id": op["resource_id"]}
            )
            if blocker:
                expired.append(blocker)
        stamp = now()
        app = {
            **base,
            "id": app_id,
            "actor": actor,
            "members": members,
            "router_operation_id": None,
            "router_resource_id": None,
            "error": None,
            "created_at": stamp,
            "updated_at": stamp,
        }
        event(con, app_id, actor, "app.create", "accepted")
        con.execute(
            "INSERT INTO apps VALUES (?, ?, ?, ?, ?)",
            (app_id, actor, key, fingerprint(material), json.dumps(app)),
        )
    for blocker in expired:
        _unlink_plan(blocker)
    return app


def get_app(app_id: str) -> dict | None:
    with connect(write=False) as con:
        row = con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()
        return json.loads(row[0]) if row else None


def operations_by_id(ids: list[str]) -> dict[str, dict]:
    with connect(write=False) as con:
        return _operations_by_id(con, ids)


def _operations_by_id(con, ids):
    rows = con.execute(
        "SELECT body FROM operations WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(ids)),),
    )
    return {op["id"]: op for op in (json.loads(row[0]) for row in rows)}


def list_apps(actor: str, units: list[str] | None, after: str | None, limit: int) -> list[dict]:
    with connect(write=False) as con:
        if units is None:
            condition, values = "actor=?", [actor]
        else:
            condition = "json_extract(body, '$.business_unit') IN (SELECT value FROM json_each(?))"
            values = [json.dumps(units)]
        if after is not None:
            condition += " AND id>?"
            values.append(after)
        rows = con.execute(
            f"SELECT body FROM apps WHERE {condition} ORDER BY id LIMIT ?", [*values, limit]
        )
        return [json.loads(row[0]) for row in rows]


def _save_app(con, app):
    app["updated_at"] = now()
    con.execute("UPDATE apps SET body=? WHERE id=?", (json.dumps(app), app["id"]))


def accept_router(app_id: str, intent: dict, resolved: dict, budget: dict | None) -> dict:
    """Accept the router operation and link it to the app in one transaction. The router's
    idempotency key is derived from the app, so an activity retry returns the same operation
    (and links nothing twice)."""
    expired = None
    with connect() as con:
        app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
        if app["router_operation_id"] or app["error"]:
            return app
        op, expired = _accept(
            con, app["actor"], system_key(app_id, "router"), intent, resolved, budget
        )
        app["router_operation_id"], app["router_resource_id"] = op["id"], op["resource_id"]
        _save_app(con, app)
    if expired:
        _unlink_plan(expired)
    return app


def fail_app(app_id: str, reason: str, *, refused: bool = False) -> dict:
    """Record why a rollout cannot continue. Idempotent: the first reason stays. Never marks an
    app failed once its rollout reached ready (its first router succeeded) or a teardown
    started: a late rollout timeout must not turn a healthy app into a failed one."""
    with connect() as con:
        app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
        if app["error"] or app.get("teardown"):
            return app
        first = (app.get("router_operations") or [app["router_operation_id"]])[0]
        if first:
            row = con.execute("SELECT state FROM operations WHERE id=?", (first,)).fetchone()
            if row and row[0] == "succeeded":
                return app
        app["error"] = reason
        _save_app(con, app)
        if refused:
            event(con, app_id, "temporal-worker", "app.router", "refused")
        event(con, app_id, "temporal-worker", "app.state", "failed")
        return app


def _app_for_update(con, app_id: str, updated_at: str) -> dict:
    app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
    if app["updated_at"] != updated_at:
        raise OperationError(
            409, "app changed during validation; submit again", "resource_changed",
            next_action="validate_and_resubmit",
        )  # fmt: skip
    return app


def accept_failover(
    actor: str,
    app_id: str,
    key: str,
    material: dict,
    router: tuple[dict, dict, dict | None],
    primary: int,
    updated_at: str,
) -> tuple[dict, str]:
    """Accept the failover's router update and make it the app's current router operation in
    one transaction, with the `app.failover` audit event. The request is remembered on the app by
    caller and key, so a retry returns the same operation and accepts nothing twice."""
    expired = None
    with connect() as con:
        app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
        for entry in app.get("failovers") or []:
            if entry["key"] == f"{actor}:{key}":
                if entry["fingerprint"] != fingerprint(material):
                    raise OperationError(
                        409,
                        "idempotency key already used for different intent",
                        "idempotency_key_reused",
                    )
                return app, entry["operation_id"]
        app = _app_for_update(con, app_id, updated_at)
        history = app.get("router_operations") or [app["router_operation_id"]]
        intent, resolved, budget = router
        op, expired = _accept(
            con, actor, system_key(app_id, "router", str(len(history))), intent, resolved, budget
        )
        app["router_operations"] = [*history, op["id"]]
        app["router_operation_id"] = op["id"]
        # `primary` is the latest requested; the reported primary is derived from the
        # newest router operation that succeeded.
        app["primary"] = primary
        app["failovers"] = [
            *(app.get("failovers") or []),
            {
                "operation_id": op["id"],
                "primary": primary,
                "key": f"{actor}:{key}",
                "fingerprint": fingerprint(material),
            },
        ]
        event(con, app_id, actor, "app.failover", "accepted")
        _save_app(con, app)
    if expired:
        _unlink_plan(expired)
    return app, op["id"]


def accept_teardown(
    caller,
    app_id: str,
    key: str,
    material: dict,
    router: tuple[dict, dict, dict | None] | None,
    updated_at: str,
) -> tuple[dict, str | None]:
    """Record the teardown on the app, accepting the router's destroy (when there is a router
    to destroy) in the same transaction, with the `app.destroy` audit event. A retry with the
    same caller, key and body returns what was accepted."""
    expired = None
    with connect() as con:
        app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
        current = app.get("teardown")
        # Every teardown request is remembered by key, so replaying an older key returns that
        # teardown's result and never starts another one. Rows from before the history existed
        # only have the current teardown.
        for entry in [*(app.get("teardown_history") or []), *([current] if current else [])]:
            if entry["key"] == f"{caller.id}:{key}":
                if entry["fingerprint"] != fingerprint(material):
                    raise OperationError(
                        409,
                        "idempotency key already used for different intent",
                        "idempotency_key_reused",
                    )
                return app, entry["router"]
        app = _app_for_update(con, app_id, updated_at)
        attempt = app.get("teardowns", 0) + 1
        router_id = None
        if router:
            intent, resolved, budget = router
            op, expired = _accept(
                con,
                caller.id,
                system_key(app_id, "teardown", str(attempt), "router"),
                intent,
                resolved,
                budget,
            )
            router_id = op["id"]
        app["teardowns"] = attempt
        app["teardown"] = {
            "key": f"{caller.id}:{key}",
            "fingerprint": fingerprint(material),
            "actor": caller.id,
            "caller_groups": sorted(caller.groups),
            "router": router_id,
            "replicas": None,
            "error": None,
        }
        app["teardown_history"] = [
            *(app.get("teardown_history") or []),
            {
                "key": f"{caller.id}:{key}",
                "fingerprint": fingerprint(material),
                "router": router_id,
            },
        ]
        event(con, app_id, caller.id, "app.destroy", "accepted")
        _save_app(con, app)
    if expired:
        _unlink_plan(expired)
    return app, router_id


def accept_teardown_replicas(app_id: str, items: list[tuple]) -> dict:
    """Accept every replica destroy (`(index, intent, resolved, budget)`) and link them to the
    teardown in one transaction: all or none. Idempotent: once linked, a retry changes nothing."""
    with connect() as con:
        app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
        teardown = app["teardown"]
        if teardown["replicas"] is not None or teardown.get("error"):
            return app
        ids, expired = [], []
        for index, intent, resolved, budget in items:
            op, blocker = _accept(
                con,
                teardown["actor"],
                system_key(app_id, "teardown", str(app["teardowns"]), "replica", str(index)),
                intent,
                resolved,
                budget,
            )
            ids.append(op["id"])
            if blocker:
                expired.append(blocker)
        teardown["replicas"] = ids
        _save_app(con, app)
    for blocker in expired:
        _unlink_plan(blocker)  # the plan file of a blocker that expired, after commit
    return app


def app_for_resource(resource_id: str) -> dict | None:
    """The app that owns this resource as a replica or router, if any."""
    with connect(write=False) as con:
        row = con.execute(
            "SELECT body FROM apps WHERE json_extract(body, '$.router_resource_id')=? OR EXISTS "
            "(SELECT 1 FROM json_each(body, '$.members') "
            "WHERE json_extract(value, '$.resource_id')=?)",
            (resource_id, resource_id),
        ).fetchone()
        return json.loads(row[0]) if row else None


def record_app_event(app_id: str, actor: str, action: str, outcome: str = "accepted") -> None:
    with connect() as con:
        event(con, app_id, actor, action, outcome)


def fail_teardown(app_id: str, reason: str, *, refused: bool = False) -> dict:
    """Record why a teardown cannot continue. Idempotent: the first reason stays."""
    with connect() as con:
        app = json.loads(con.execute("SELECT body FROM apps WHERE id=?", (app_id,)).fetchone()[0])
        teardown = app.get("teardown")
        if not teardown or teardown.get("error"):
            return app
        teardown["error"] = reason
        _save_app(con, app)
        if refused:
            event(con, app_id, "temporal-worker", "app.teardown", "refused")
        event(con, app_id, "temporal-worker", "app.state", "failed")
        return app
