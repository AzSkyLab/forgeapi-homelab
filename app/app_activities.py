"""Activities for app rollouts. They only read the ledger, or write idempotently through the
same acceptance code as POST /operations. None of them runs Terraform or applies anything."""

from fastapi import HTTPException
from temporalio import activity

from app import apps, ledger
from app.tenants import TenancyError


@activity.defn
def check_app(app_id: str) -> str:
    """Read-only: the app's derived state, or `router_needed` when every replica has succeeded
    and the router is not accepted yet."""
    app = ledger.get_app(app_id)
    ops = apps.load(app)
    if app.get("teardown"):
        return "superseded"  # a teardown took over: the rollout has nothing left to do
    first = apps.router_ops(app)[:1]
    if first and ops[first[0]]["state"] == "succeeded":
        return "ready"  # the rollout's own router is deployed; a later failover is not its job
    state = apps.derive_state(app, ops)
    return (
        "router_needed" if state == "planning_router" and not app["router_operation_id"] else state
    )


@activity.defn
def queued_app_operations(app_id: str) -> list[str]:
    """Read-only: operations of this app (replicas, routers, teardown destroys) that were
    accepted but whose plan phase never started, so a restarted workflow can start them."""
    app = ledger.get_app(app_id)
    return apps.queued_ids(app, apps.load(app))


@activity.defn
def accept_app_router(app_id: str) -> str:
    """Accept the router intent (references built from the replicas' outputs) through the
    ordinary acceptance path and return its operation ID. A refusal fails the app with the
    reason and returns ""; a refusal that clears by itself (resource busy) returns "retry"; a
    transient failure raises so Temporal may retry. Idempotent: the router's key derives from
    the app, so a retry returns the same operation."""
    app = ledger.get_app(app_id)
    if app["router_operation_id"]:
        return app["router_operation_id"]
    try:
        return apps.accept_router(app)["router_operation_id"] or ""
    except (HTTPException, TenancyError) as error:
        if getattr(error, "status_code", getattr(error, "status", 0)) >= 500:
            raise  # the pattern repository or ledger is unavailable: retry, do not refuse
        if apps.transient(error):
            return "retry"
        ledger.fail_app(app_id, apps.refusal_text(error), refused=True)
        return ""


@activity.defn
def fail_app(app_id: str, reason: str) -> None:
    ledger.fail_app(app_id, reason)


@activity.defn
def check_teardown(app_id: str) -> str:
    """Read-only: the app's derived teardown state, or `replicas_needed` when the router is gone
    (or there never was one) and the replicas' destroys are not accepted yet."""
    app = ledger.get_app(app_id)
    state = apps.derive_state(app, apps.load(app))
    if state == "planning_replica_destroy" and app["teardown"]["replicas"] is None:
        return "replicas_needed"
    return state


@activity.defn
def accept_teardown_replica_destroys(app_id: str) -> dict:
    """Accept destroy operations for the replicas still deployed, through the ordinary
    acceptance path (policy, guardrails, references), and return their IDs. A refusal fails the
    teardown with the reason and returns `refused`; a busy replica (for example a drift check
    is running on it) returns `retry` and changes nothing, to be tried again next cycle; a
    transient failure raises so Temporal may retry. Idempotent: once recorded, a retry returns
    the same operations. Never applies."""
    app = ledger.get_app(app_id)
    try:
        app = apps.accept_teardown_replicas(app)
    except (HTTPException, TenancyError) as error:
        if getattr(error, "status_code", getattr(error, "status", 0)) >= 500:
            raise  # the pattern repository or ledger is unavailable: retry, do not refuse
        if apps.transient(error):
            return {"operations": [], "refused": False, "retry": True}
        ledger.fail_teardown(app_id, apps.refusal_text(error, "replica destroy"), refused=True)
        return {"operations": [], "refused": True}
    return {"operations": app["teardown"]["replicas"] or [], "refused": False}


@activity.defn
def fail_app_teardown(app_id: str, reason: str) -> None:
    ledger.fail_teardown(app_id, reason)
