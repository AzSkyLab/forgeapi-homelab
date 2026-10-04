"""Cost history rebuilt from the operation ledger; nothing new is stored.

Each resource's reserved estimate is replayed from its operations' timestamps using the same
rules `ledger` applies when it writes the resource row: an accepted deploy reserves
max(previous, requested) at once; a failed deploy (planning or apply refused, discarded,
expired, or reconciled as failed) restores the previous amount at its finish time; a succeeded
deploy keeps the requested amount; a succeeded destroy releases the resource; an uncertain
operation keeps its reservation. Drift checks reserve nothing.
Estimates are the pattern authors' numbers, not billing."""

import json
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, Field

from app import ledger, policy

MAX_RESOURCES = 5000
CURRENCY_NOTE = "pattern estimates in the budget's unit, not billing"
LEGACY_PATTERN = "legacy"


class CostDay(BaseModel):
    date: str = Field(description="UTC day, YYYY-MM-DD.")
    reserved: float = Field(description="Reserved estimate at the end of that UTC day.")


class CostByPattern(BaseModel):
    pattern: str = Field(description="Pattern name; `legacy` for pre-ledger deployments.")
    reserved_now: float = Field(description="Reserved estimate for this pattern now.")
    change_over_period: float = Field(description="Change since the start of the period.")


class CostMover(BaseModel):
    resource_id: str = Field(description="Resource whose reservation changed.")
    pattern: str = Field(description="Its pattern.")
    labels: dict[str, str] | None = Field(default=None, description="Its labels, if any.")
    delta: float = Field(description="Net change in its reserved estimate over the period.")
    at: str = Field(description="When its reservation last changed in the period (UTC).")


class CostHistory(BaseModel):
    business_unit: str | None = Field(description="Business unit; null without tenancy.")
    environment: str | None = Field(description="Environment; null without tenancy or filter.")
    currency_note: str = Field(description="Estimates are the budget's unit, not billing.")
    monthly_budget: float | None = Field(description="Current monthly budget, if one is set.")
    truncated: bool = Field(description="True when more resources exist than were scanned.")
    days: list[CostDay] = Field(description="One entry per UTC day, oldest first, ending today.")
    by_pattern: list[CostByPattern] = Field(description="Per-pattern reservation and change.")
    movers: list[CostMover] = Field(description="Up to 10 resources by largest absolute delta.")


def _amount(value) -> float:
    return ledger.accounting_amount(value)


def _current(resource: dict) -> float:
    return (
        0
        if resource.get("state") == "destroyed"
        else _amount(resource.get("estimated_monthly_cost"))
    )


def replay(ops: list[dict], resource: dict, stop: str) -> tuple[float, list[tuple[str, float]]]:
    """(value held before the first operation, [(timestamp, delta)] in ledger order) for one
    resource. A resource with no operations (seeded or pre-ledger) is held flat. The last step
    corrects to the stored row if the rules above cannot explain it (a row written outside any
    operation), so the series always ends at what `ledger._reserved` counts."""
    real = [op for op in ops if op.get("action") != "drift_check"]
    target = _current(resource)
    if not real:
        return target, []
    first = real[0]
    cost = _amount(
        first.get("estimated_monthly_cost")
        if first["action"] == "destroy"
        else first.get("previous_estimated_monthly_cost")
    )
    initial = last = cost
    destroyed = False
    steps: list[tuple[str, float]] = []

    def move(at, new_cost=None, new_destroyed=None):
        nonlocal cost, destroyed, last
        cost = cost if new_cost is None else new_cost
        destroyed = destroyed if new_destroyed is None else new_destroyed
        value = 0 if destroyed else cost
        if value != last:
            steps.append((at, value - last))
            last = value

    for op in real:
        deploy = op["action"] != "destroy"
        requested = _amount(op.get("estimated_monthly_cost"))
        if deploy:
            previous = _amount(op.get("previous_estimated_monthly_cost"))
            move(op["created_at"], max(previous, requested))
        end = op.get("updated_at") or op["created_at"]
        if op["state"] == "succeeded":
            move(end, requested, not deploy)
        elif op["state"] == "failed" and deploy:  # every failed deploy released its reservation
            move(end, _amount(op.get("previous_estimated_monthly_cost")))
    if last != target:
        steps.append((stop, target - last))
    return initial, steps


def _day(stamp: str) -> date:
    return datetime.fromisoformat(stamp).astimezone(UTC).date()


def build(caller, unit: str | None, environment: str | None, days: int, budget) -> dict:
    stop = ledger.now()
    today = _day(stop)
    first = today - timedelta(days=days - 1)
    with ledger.connect(write=False) as con:
        if unit is None:
            where, args = "json_extract(body,'$.actor')=?", [caller.id]
            if environment:
                where += " AND json_extract(body,'$.environment')=?"
                args.append(environment)
        else:
            where = (
                "json_extract(body,'$.business_unit')=? AND json_extract(body,'$.environment')=?"
            )
            args = [unit, environment]
        rows = con.execute(
            f"SELECT body FROM resources WHERE {where} ORDER BY id LIMIT ?",
            (*args, MAX_RESOURCES + 1),
        ).fetchall()
        truncated = len(rows) > MAX_RESOURCES
        resources = {r["id"]: r for r in (json.loads(row[0]) for row in rows[:MAX_RESOURCES])}
        ops: dict[str, list[dict]] = {}
        for row in con.execute("SELECT resource_id, body FROM operations ORDER BY rowid"):
            if row[0] in resources:
                ops.setdefault(row[0], []).append(json.loads(row[1]))
        if unit is not None:
            now_total = ledger._reserved(con, unit, environment)
        else:
            now_total = sum(_current(r) for r in resources.values())
    legacy = policy.legacy_committed(unit, environment) if unit is not None else 0.0
    deltas = [0.0] * days
    base = 0.0
    patterns: dict[str, list[float]] = {}  # pattern -> [now, baseline]
    movers = []
    for rid, resource in resources.items():
        initial, steps = replay(ops.get(rid, []), resource, stop)
        baseline, net, changed = initial, 0.0, None
        base += initial
        for at, delta in steps:
            if _day(at) < first:
                baseline += delta
                base += delta
                continue
            deltas[min((_day(at) - first).days, days - 1)] += delta
            net += delta
            changed = at if changed is None else max(changed, at)
        entry = patterns.setdefault(resource.get("pattern") or "unknown", [0.0, 0.0])
        entry[0] += _current(resource)
        entry[1] += baseline
        if net:
            movers.append(
                {
                    "resource_id": rid,
                    "pattern": resource.get("pattern") or "unknown",
                    "labels": resource.get("labels") or None,
                    "delta": net,
                    "at": changed,
                }
            )
    series, running = [], base
    for index in range(days):
        running += deltas[index]
        series.append(
            {"date": (first + timedelta(days=index)).isoformat(), "reserved": running + legacy}
        )
    series[-1]["reserved"] = _amount(now_total + legacy)  # exact, not float-accumulated
    by_pattern = [
        {"pattern": name, "reserved_now": now, "change_over_period": now - before}
        for name, (now, before) in sorted(patterns.items())
    ]
    if legacy:
        by_pattern.append(
            {"pattern": LEGACY_PATTERN, "reserved_now": legacy, "change_over_period": 0.0}
        )
    movers.sort(key=lambda m: (-abs(m["delta"]), m["resource_id"]))
    return {
        "business_unit": unit,
        "environment": environment,
        "currency_note": CURRENCY_NOTE,
        "monthly_budget": budget,
        "truncated": truncated,
        "days": series,
        "by_pattern": by_pattern,
        "movers": movers[:10],
    }
