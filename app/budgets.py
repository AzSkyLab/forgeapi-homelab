"""Estimated monthly cost budgets per business unit and environment. See docs/tenancy.md.

Costs are the pattern authors' estimates (`estimated_costs` in the pattern's config.yaml), not
Azure billing. That makes the check instant and possible *before* anything is deployed; real
spend (Cost Management, by the BusinessUnit tag the platform injects) is a later addition."""

from typing import Any

from app import db
from app.models import State
from app.tenants import BusinessUnit, Environment, TenancyError


def estimated_cost(
    config: dict[str, Any] | None, environment: str, size: str | None
) -> float | None:
    """`estimated_costs.<size>.<environment>`, else `.<environment>`, else a single number."""
    costs = (config or {}).get("estimated_costs")
    for candidate in (
        costs.get(size, {}).get(environment) if isinstance(costs, dict) and size else None,
        costs.get(environment) if isinstance(costs, dict) else None,
        costs,
    ):
        if isinstance(candidate, (int, float)) and not isinstance(candidate, bool):
            return float(candidate)
    return None


def committed(unit: BusinessUnit, environment: Environment) -> float:
    """Estimated monthly cost of everything not yet destroyed. A failed deployment still counts:
    it may have created resources, and destroying it is what frees the budget."""
    return sum(
        d.estimated_monthly_cost or 0.0
        for d in db.list_for([unit.name])
        if d.environment == environment.name and d.state != State.destroyed
    )


def usage(unit: BusinessUnit, environment: Environment) -> dict[str, float | None]:
    used = committed(unit, environment)
    limit = environment.budget_monthly
    return {
        "monthly_budget": limit,
        "committed": used,
        "available": None if limit is None else max(0.0, limit - used),
    }


def check(
    unit: BusinessUnit,
    environment: Environment,
    pattern: str,
    cost: float | None,
    replacing: float = 0.0,
) -> dict[str, Any] | None:
    """Refuse a request that does not fit. `replacing` is this deployment's own current cost
    when it is being updated or retried, so it is not counted twice."""
    limit = environment.budget_monthly
    if limit is None:
        return None
    where = f"{unit.name}/{environment.name}"
    if cost is None:
        raise TenancyError(
            403,
            f"{where} has a budget, but pattern {pattern!r} declares no estimated cost for it; "
            "the pattern's config.yaml needs `estimated_costs` before it can be deployed here",
        )
    used = committed(unit, environment) - replacing
    summary = {
        "monthly_budget": limit,
        "committed": used,
        "this_request": cost,
        "available": max(0.0, limit - used),
    }
    if replacing:
        # An update: `committed` leaves this deployment out, so say what it costs today.
        summary = {**summary, "committed": used, "this_deployment_now": replacing}
    if used + cost > limit:
        raise TenancyError(
            403,
            {
                "message": f"this would exceed the estimated monthly budget for {where}",
                **summary,
                "hint": "destroy deployments you no longer need, choose a smaller size, or ask "
                "the platform team to raise the budget",
            },
        )
    return summary
