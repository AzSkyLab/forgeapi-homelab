"""Agent-first infrastructure control plane: discover → intent → plan → execute → observe."""

import asyncio
import json
import logging
import re
import sqlite3
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.utils import generate_unique_id
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import apps, catalog, cost_history, drift, ledger, policy, promotion, team_admin, tenants
from app.auth import require_caller
from app.contracts import (
    App,
    AppApprove,
    AppFailover,
    AppIntent,
    AppPage,
    Discovery,
    ErrorEnvelope,
    EventPage,
    Execute,
    Intent,
    Operation,
    OperationPage,
    PatternChanges,
    PatternCheck,
    PatternDescription,
    PatternList,
    Promote,
    Reconcile,
    Resource,
    ResourcePage,
    Upgrade,
    ValidationResult,
    canonical_intent,
    public_operation,
    public_resource,
)
from app.cost_history import CostHistory
from app.dispatch import OperationDispatcher, get_dispatcher
from app.tenants import Caller, TenancyError

SOFTWARE_RELEASE = "1.0.0"
MAX_PAGE_POSITION = (1 << 63) - 1
app = FastAPI(
    title="ForgeAPI Agent Control Plane",
    version=SOFTWARE_RELEASE,
    description=(
        "Plan a Terraform change, inspect it, then execute the exact plan digest you "
        "reviewed; never replay blindly. An `uncertain` outcome needs operator "
        "reconciliation, not a retry. Start at GET /agent (or /v1/agent) for capabilities, "
        "available patterns and links to the rest of the API."
    ),
)
ERROR_RESPONSES = {code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 422, 502, 503)}
router = APIRouter(responses=ERROR_RESPONSES)

OPERATION_STATES = (
    "queued", "planning", "planned", "apply_queued", "applying", "succeeded", "failed",
    "uncertain",
)  # fmt: skip

REQUEST_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_access_log = logging.getLogger("forgeapi.access")
_access_log.propagate = False
if not _access_log.handlers:
    _access_handler = logging.StreamHandler()
    _access_handler.setFormatter(logging.Formatter("%(message)s"))
    _access_log.addHandler(_access_handler)
_access_log.setLevel(logging.INFO)


def _route_template(request: Request) -> str:
    """The matched route's path template. Root and `/v1` share one `APIRouter`, whose routes
    carry no prefix of their own; add the request's own prefix back on, except for the few
    routes (like `/v1/openapi.json`) registered directly with `/v1` already in their path."""
    route = request.scope.get("route")
    if route is None:
        return "unmatched"
    return route.path if route.path.startswith("/v1") else route_prefix(request) + route.path


def _access_entry(request: Request, request_id: str, status: int, start: float) -> str:
    caller = getattr(request.state, "caller", None)
    return json.dumps(
        {
            "request_id": request_id,
            "method": request.method,
            "route": _route_template(request),
            "status": status,
            "duration_ms": round((time.monotonic() - start) * 1000, 2),
            "caller": caller.id if caller else None,
        }
    )


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    """Assigns/validates a correlation ID and emits exactly one JSON access log line per
    request. Never logs headers, query values, bodies or exception text."""
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
    request.state.request_id = request_id
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        _access_log.info(_access_entry(request, request_id, 500, start))
        raise
    response.headers["X-Request-ID"] = request_id
    _access_log.info(_access_entry(request, request_id, response.status_code, start))
    return response


ADMIN_OPERATIONS = (
    "admin_list_teams", "admin_get_team", "admin_validate_team", "admin_create_team",
    "admin_replace_team", "admin_list_team_revisions", "admin_get_team_revision",
    "admin_revert_team", "admin_archive_team", "admin_unarchive_team", "admin_import_teams",
)  # fmt: skip


def operation_id(route):
    old = {
        "submit_intent": "submit_intent",
        "list_operations": "list_operations",
        "get_operation": "get_operation",
        "execute_operation": "execute_plan",
        "submit_app": "submit_app",
        "list_apps": "list_apps",
        "get_app": "get_app",
        "approve_app": "approve_app",
        "failover_app": "failover_app",
        **{name: name for name in ADMIN_OPERATIONS},
        "destroy_app": "destroy_app",
        "discard_app": "discard_app",
        "check_drift": "check_drift",
        "promote_resource": "promote_resource",
        "upgrade_resource": "upgrade_resource",
    }.get(route.name)
    identifier = old or generate_unique_id(route)
    return f"v1_{identifier}" if route.path.startswith("/v1/") else identifier


def route_prefix(request: Request) -> str:
    return "/v1" if request.url.path.startswith("/v1/") else ""


def caller_context(request: Request, caller: Caller = Depends(require_caller)) -> Caller:
    request.state.caller = caller
    return caller


CallerDep = Annotated[Caller, Depends(caller_context)]


def operator_caller(request: Request, caller: CallerDep) -> Caller:
    """Team administration is operator-only, in every tenants source and for reads too."""
    if not tenants.is_operator(caller):
        raise HTTPException(403, "operator role required")
    return caller


OperatorDep = Annotated[Caller, Depends(operator_caller)]
IfMatch = Annotated[
    str | None,
    Header(
        alias="If-Match",
        description='The team revision you read, as the ETag `"<revision>"`. Required: 428 when '
        "missing, 412 when it is no longer current.",
    ),
]
DispatcherDep = Annotated[OperationDispatcher, Depends(get_dispatcher)]
Key = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Client-generated key. The same caller, key and body returns the same "
        "operation instead of creating a new one; reuse it to retry safely.",
    ),
]


def error_response(request: Request, code: int, detail, headers=None, exc=None):
    if request.method in {"POST", "PUT", "DELETE", "PATCH"} and request.url.path not in {
        "/intents/validate",
        "/v1/intents/validate",
    }:
        try:
            actor = getattr(request.state, "caller", Caller("unauthenticated")).id
            route = getattr(request.scope.get("route"), "path", "unknown")
            ledger.refused(
                actor, getattr(request.state, "audit_action", None) or f"{request.method} {route}"
            )
        except (sqlite3.Error, OSError, HTTPException, AttributeError):
            code, detail, exc = 503, "audit unavailable; request was not accepted", None
    names = {
        401: "authentication_required",
        403: "permission_denied",
        404: "not_found",
        409: "conflict",
        412: "precondition_failed",
        422: "invalid_request",
        428: "precondition_required",
        502: "catalog_unavailable",
        503: "service_unavailable",
    }
    body = {
        "code": names.get(code, "request_failed"),
        "detail": detail,
        "next_action": getattr(exc, "next_action", None) or "inspect_and_correct_request",
        "request_id": getattr(request.state, "request_id", None),
    }
    if reason := getattr(exc, "reason", None):
        body["reason"] = reason
    if operation_id := getattr(exc, "operation_id", None):
        body["operation_id"] = operation_id
        body["status_url"] = f"{route_prefix(request)}/operations/{operation_id}"
    return JSONResponse({"error": body}, status_code=code, headers=headers)


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc):
    return await run_in_threadpool(
        error_response, request, exc.status_code, exc.detail, exc.headers, exc
    )


@app.exception_handler(TenancyError)
async def tenant_error(request, exc):
    return await run_in_threadpool(error_response, request, exc.status, exc.message)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Never echo input values or raw auth exceptions into model context.
    return await run_in_threadpool(
        error_response,
        request,
        422,
        [{"field": list(e["loc"]), "code": e["type"]} for e in exc.errors()],
    )


@app.exception_handler(catalog.CatalogError)
async def catalog_error(request, _exc):
    return await run_in_threadpool(error_response, request, 502, "pattern repository unavailable")


@app.exception_handler(sqlite3.Error)
async def storage_error(request, _exc):
    return await run_in_threadpool(error_response, request, 503, "operation ledger unavailable")


@app.get("/healthz", tags=["health"], summary="Constant liveness answer.")
def health():
    """Always returns the same body when the process is up. Never mutates, never checks a
    dependency; see `GET /readyz` for whether this instance can actually serve traffic."""
    return {"status": "ok", "contract": "agent-v1"}


def _ledger_trivial_read() -> None:
    with ledger.connect(write=False) as con:
        con.execute("SELECT 1")


@app.get(
    "/readyz",
    tags=["health"],
    include_in_schema=True,
    summary="Report whether the ledger and Temporal are both reachable.",
)
async def ready(dispatcher: DispatcherDep):
    """Unauthenticated readiness probe; unversioned like `/healthz`, which it does not change.

    Read-only: never mutates state, records no audit event, and writes nothing beyond what an
    ordinary ledger read already touches. Checks a trivial ledger read and a short Temporal
    health call. 503 means retry shortly; it is not a signal to change the request."""
    checks = {"ledger": "ok", "temporal": "ok"}
    try:
        await run_in_threadpool(_ledger_trivial_read)
    except (sqlite3.Error, OSError, HTTPException):
        checks["ledger"] = "unavailable"
    try:
        await dispatcher.ready()
    except Exception:
        checks["temporal"] = "unavailable"
    ok = all(value == "ok" for value in checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503
    )


def _budget(unit: str, environment):
    limit = ledger.accounting_amount(environment.budget_monthly)
    used = ledger.accounting_amount(
        ledger.reserved(unit, environment.name) + policy.legacy_committed(unit, environment.name)
    )
    return {"monthly_budget": limit, "reserved": used, "available": max(0, limit - used)}


@router.get(
    "/agent",
    response_model=Discovery,
    tags=["discovery"],
    summary="Capabilities, available patterns and entry points for this caller.",
)
def discover(request: Request, caller: CallerDep):
    """Read-only and idempotent; call it first. Never mutates anything. Returns the workflow
    an agent should follow, which patterns and business units this caller can use, and the
    links to validate, submit and list operations from here."""
    prefix = route_prefix(request)
    return {
        "contract": "agent-v1",
        "supported_api_major": 1,
        "software_release": SOFTWARE_RELEASE,
        "capabilities": {
            "idempotent_intents": True,
            "exact_plan_execution": True,
            "operation_events": True,
            "stable_operation_pagination": True,
            "discard_planned_operation": True,
            "resource_inventory": True,
            "operator_reconciliation": True,
            "operation_filters": True,
            "operation_long_poll": True,
            "plan_guardrails": True,
            "reference_protection": True,
            "resource_filters": True,
            "upgrade_detection": True,
            "pattern_listing": True,
            "guardrail_discovery": True,
            "budget_discovery": True,
            "resource_labels": True,
            "input_references": True,
            "plan_expiry": True,
            "failure_diagnostics": True,
            "web_console": True,
            "app_rollouts": True,
            "app_failover": True,
            "app_teardown": True,
            "drift_checks": True,
            "promotion": True,
            "resource_upgrade": True,
            "cost_history": True,
            "deployable_patterns": True,
            "pattern_changes": True,
            "pattern_checks": True,
            "team_admin": True,
        },
        "caller": {"operator": tenants.is_operator(caller)},
        "patterns": policy.available(caller),
        "business_units": [
            {
                "name": u.name,
                "archived": u.archived,
                "environments": [
                    e.name for e in u.environments.values() if u.can_deploy(caller, e)
                ],
                "regions": list(u.regions_allowed),
                "default_region": u.region_default,
                "patterns": sorted(u.patterns & set(catalog.load())),
                "deployable_patterns": {
                    e.name: policy.deployable_in(u, e)
                    for e in u.environments.values()
                    if u.can_deploy(caller, e)
                },
                "guardrails": {
                    e.name: {
                        "allow_destroy": e.allow_destroy,
                        "protected_resource_types": sorted(e.protected_resource_types),
                    }
                    for e in u.environments.values()
                    if u.can_deploy(caller, e)
                },
                "clouds": {
                    e.name: sorted(e.targets)
                    for e in u.environments.values()
                    if u.can_deploy(caller, e)
                },
                "budgets": {
                    e.name: _budget(u.name, e)
                    for e in u.environments.values()
                    if u.can_deploy(caller, e) and e.budget_monthly is not None
                },
            }
            for u in tenants.units_for(caller)
        ]
        if tenants.enabled()
        else [],
        "workflow": [
            "describe_pattern",
            "validate_intent",
            "submit_intent",
            "poll_until_planned",
            "inspect_changes",
            "execute_exact_plan",
            "poll_outcome",
        ],
        "semantics": {
            "dispatch": (
                "202 acknowledges queued work; on dispatch_unconfirmed retry the same request"
            ),
            "intent_idempotency": "required Idempotency-Key; same caller/key/body returns same job",
            "execution_idempotency": "same operation and digest executes at most once",
            "plan": "persisted Terraform binary; digest binds execution to reviewed changes",
            "uncertain": "operator reconciliation required; never automatically replayed",
            "validation": "schema and placement only; no Terraform plan or budget reservation",
            "plan_expiry": (
                "opt-in: when plan_expires_at is set, an unexecuted plan past it fails with "
                "plan_expired; submit a new intent"
            ),
            "versions": "commit pinned at acceptance; expected_commit detects moved tags",
        },
        "links": {
            "patterns": f"{prefix}/patterns/{{name}}",
            "catalog": f"{prefix}/patterns",
            "validate": f"{prefix}/intents/validate",
            "submit": f"{prefix}/operations",
            "operations": f"{prefix}/operations",
            "openapi": f"{prefix}/openapi.json",
            "resources": f"{prefix}/resources",
        },
    }


@router.get(
    "/budgets/history",
    response_model=CostHistory,
    tags=["discovery"],
    summary="Reserved estimated cost per day, by pattern, with the biggest movers.",
)
def budget_history(
    caller: CallerDep,
    business_unit: str | None = Query(
        default=None, description="Business unit; may be omitted if you belong to only one."
    ),
    environment: str | None = Query(
        default=None, description="Environment; may be omitted if the unit has only one."
    ),
    days: int = Query(30, ge=1, le=366, description="Days of history, ending today (UTC)."),
):
    """Read-only. Rebuilt from the operation ledger with the rules admission uses; estimates are
    pattern authors' numbers, not billing. The last day equals `reserved` in budget discovery.
    Requires capability `cost_history`."""
    if not tenants.enabled():
        return cost_history.build(caller, None, environment, days, None)
    unit = tenants.select_unit(caller, business_unit)
    allowed = {e.name: e for e in unit.environments.values() if unit.can_deploy(caller, e)}
    if environment is None and len(allowed) == 1:
        environment = next(iter(allowed))
    if environment is None:
        raise TenancyError(422, f"name an environment; {unit.name} has {sorted(allowed)}")
    if environment not in unit.environments:
        raise TenancyError(422, f"{unit.name} has no {environment!r} environment")
    if environment not in allowed:
        raise TenancyError(403, f"you may not view {unit.name}/{environment}")
    limit = allowed[environment].budget_monthly
    limit = None if limit is None else ledger.accounting_amount(limit)
    return cost_history.build(caller, unit.name, environment, days, limit)


@router.get(
    "/patterns",
    response_model=PatternList,
    tags=["patterns"],
    summary="List the patterns this caller can use.",
)
def list_patterns(request: Request, caller: CallerDep, environment: str | None = None):
    """Read-only and cheap: names and clouds only, no versions or schemas. Follow `links.self`
    (or `describe_pattern`) for one pattern's inputs and tags. With `environment`, only
    patterns deployable there (the environment targets their cloud); requires capability
    `deployable_patterns`."""
    prefix, patterns = route_prefix(request), catalog.load()
    names = policy.available(caller)
    if environment is not None and tenants.enabled():
        names = sorted(set(names) & policy.deployable_patterns(caller, environment))
    return {
        "items": [
            {
                "name": name,
                "cloud": patterns[name].cloud,
                "links": {"self": f"{prefix}/patterns/{name}"},
            }
            for name in names
        ]
    }


@router.get(
    "/patterns/{name}",
    response_model=PatternDescription,
    tags=["patterns"],
    summary="Input schema, versions and an example for one pattern.",
)
def describe_pattern(
    name: str,
    caller: CallerDep,
    version: str | None = None,
    business_unit: str | None = None,
    environment: str | None = None,
):
    """Read-only and idempotent. Platform-injected inputs (placement, cost center, etc.) are
    already removed from the schema; build an intent from what's left, then validate it."""
    return policy.inspect_pattern(name, version, business_unit, environment, caller)


@router.get(
    "/patterns/{name}/changes",
    response_model=PatternChanges,
    tags=["patterns"],
    summary="What changed between two tags of a pattern.",
)
def pattern_changes(
    name: str,
    caller: CallerDep,
    from_: Annotated[str, Query(alias="from", description="Older tag.")],
    to: Annotated[str, Query(description="Newer tag.")],
    business_unit: str | None = None,
    environment: str | None = None,
):
    """Read-only. Commit subjects (no authors) and the input-schema difference between two
    existing tags; default values are never returned, only whether they changed. With
    `environment`, platform-injected inputs are left out. Requires capability
    `pattern_changes`."""
    return policy.pattern_changes(name, from_, to, business_unit, environment, caller)


@router.get(
    "/patterns/{name}/check",
    response_model=PatternCheck,
    tags=["patterns"],
    summary="Static contract check of one pattern version.",
)
def pattern_check(name: str, caller: CallerDep, version: str | None = None):
    """Read-only. Checks the pattern's Terraform at its pinned commit against the platform's
    contract (inputs, placement wiring, outputs, config, state, remote code). Terraform is not
    run. Results are cached per commit. Requires capability `pattern_checks`."""
    return policy.check_pattern(name, version, caller)


@router.post(
    "/intents/validate",
    response_model=ValidationResult,
    tags=["intents"],
    summary="Check an intent's schema and placement without submitting it.",
)
def validate_intent(body: Intent, caller: CallerDep):
    """Mutates nothing: no Terraform plan, no budget reservation, no ledger record. Safe to
    call repeatedly while drafting an intent. A valid result does not guarantee acceptance;
    submit the same body to `POST /operations` to actually queue it."""
    resolved, _ = policy.validate(body, caller)
    return {
        "valid": True,
        "validation_scope": "schema_and_placement",
        "terraform_plan_performed": False,
        "budget_reserved": False,
        **{k: resolved.get(k) for k in ("pattern", "version", "commit", "estimated_monthly_cost")},
    }


@router.post(
    "/operations",
    status_code=202,
    response_model=Operation,
    tags=["intents"],
    summary="Submit an intent; queues a Terraform plan.",
)
async def submit_intent(
    body: Intent,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates: creates (or reuses) a durable operation and resource, then queues planning.
    Idempotent by `Idempotency-Key`: the same caller, key and body returns the same operation
    instead of creating a new one; reuse the same key and identical body after an uncertain
    response.

    Returns after Temporal accepts the job, before Terraform finishes. If dispatch returns
    503 dispatch_unconfirmed, retry the identical request/key; the operation is already recorded.
    Returns a durable operation and resource ID. This does not apply infrastructure changes.
    Poll until planned, inspect changes, then explicitly execute with the returned plan digest.
    """
    op = await run_in_threadpool(accept_intent, body, caller, idempotency_key)
    if op["state"] == "queued":
        failure = await dispatch_operation(
            dispatcher, op, "plan", route_prefix(request), request.state.request_id
        )
        if failure:
            return failure
    response.headers["Location"] = f"{route_prefix(request)}/operations/{op['id']}"
    response.headers["Retry-After"] = "2"
    return public_operation(op, route_prefix(request))


def accept_intent(body: Intent, caller: Caller, key: str) -> dict:
    material = canonical_intent(body)
    if existing := ledger.replay(caller.id, key, material):
        return policy.authorize(existing, caller)
    if body.resource_id:
        apps.refuse_member_change(body.resource_id, caller)
    resolved, budget = policy.validate(body, caller)
    return ledger.accept(caller.id, key, material, resolved, budget, caller.groups)


async def dispatch_operation(dispatcher, op, phase, prefix="", request_id=None):
    try:
        await dispatcher.dispatch(op["id"], phase)
    except Exception:
        # Acceptance is already durable. Do not falsely audit a refusal or expose RPC errors.
        location = f"{prefix}/operations/{op['id']}"
        return JSONResponse(
            {
                "error": {
                    "code": "dispatch_unconfirmed",
                    "detail": "request recorded; Temporal dispatch could not be confirmed",
                    "operation_id": op["id"],
                    "next_action": "retry_same_request",
                    "status_url": location,
                    "request_id": request_id,
                }
            },
            status_code=503,
            headers={"Location": location, "Retry-After": "2"},
        )


async def dispatch_app(dispatcher, app, ops, prefix="", request_id=None):
    """Plan every replica still queued, then start the app's rollout workflow. Every call is
    idempotent, so retrying the same request after a failure finishes the dispatch."""
    try:
        for operation_id in apps.queued_ids(app, ops):
            await dispatcher.dispatch(operation_id, "plan")
        await dispatcher.dispatch_app(app["id"])
    except Exception:
        location = f"{prefix}/apps/{app['id']}"
        return JSONResponse(
            {
                "error": {
                    "code": "dispatch_unconfirmed",
                    "detail": "request recorded; Temporal dispatch could not be confirmed",
                    "next_action": "retry_same_request",
                    "status_url": location,
                    "request_id": request_id,
                }
            },
            status_code=503,
            headers={"Location": location, "Retry-After": "2"},
        )


@router.post(
    "/apps",
    status_code=202,
    response_model=App,
    tags=["apps"],
    summary="Submit a multi-cloud app: replicas, then a router behind a second approval.",
)
async def submit_app(
    body: AppIntent,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates: validates every replica like `POST /operations` (placement, allowed patterns,
    injected inputs, budget), then accepts all of them and the app together or not at all, and
    queues their plans. The router's references and budget are checked when it is accepted, after
    every replica has succeeded. Idempotent by `Idempotency-Key`: the same caller, key and body
    returns the same app; on 503 dispatch_unconfirmed retry the identical request.

    Review the replica plans, then `POST /apps/{id}/approve` with every replica's digest. When
    all replicas succeed the router is planned automatically; approve its digest the same way.
    Nothing is applied without those two explicit approvals."""
    app = await run_in_threadpool(apps.accept, body, caller, idempotency_key)
    ops = await run_in_threadpool(apps.load, app)
    prefix = route_prefix(request)
    failure = await dispatch_app(dispatcher, app, ops, prefix, request.state.request_id)
    if failure:
        return failure
    response.headers["Location"] = f"{prefix}/apps/{app['id']}"
    response.headers["Retry-After"] = "2"
    return apps.public_app(app, ops, prefix, notice=True)


@router.get(
    "/apps",
    response_model=AppPage,
    tags=["apps"],
    summary="List the caller's apps, oldest first.",
)
def list_apps(
    request: Request,
    caller: CallerDep,
    after: str | None = Query(default=None, pattern=r"^app_[0-9a-f]{32}$"),
    limit: int = Query(20, ge=1, le=100),
):
    """Read-only and idempotent. Visible under the same rules as operations and resources.
    Pass the previous page's last ID as `after` for the next page."""
    units = [u.name for u in tenants.units_for(caller)] if tenants.enabled() else None
    items = ledger.list_apps(caller.id, units, after, limit + 1)
    shown = items[:limit]
    return {
        "items": [apps.public_app(a, apps.load(a), route_prefix(request)) for a in shown],
        "next_after": shown[-1]["id"] if len(items) > limit else None,
    }


@router.get(
    "/apps/{app_id}",
    response_model=App,
    tags=["apps"],
    summary="Poll one app's derived state.",
)
def get_app(app_id: str, request: Request, caller: CallerDep):
    """Read-only and idempotent. `state` is derived from the member operations; follow
    `next_action`. An app you cannot see is 404."""
    app = policy.authorize(ledger.get_app(app_id), caller)
    return apps.public_app(app, apps.load(app), route_prefix(request))


@router.post(
    "/apps/{app_id}/approve",
    status_code=202,
    response_model=App,
    tags=["apps"],
    summary="Apply the current gate's exact plans: all replicas, then the router.",
)
async def approve_app(
    app_id: str,
    body: AppApprove,
    request: Request,
    response: Response,
    caller: CallerDep,
    dispatcher: DispatcherDep,
):
    """Mutates: name exactly the planned operations at the app's current gate (every replica at
    gate one, the router at gate two) with their exact plan digests. Anything else (a missing or
    extra operation, a wrong digest, an app that is not at a gate) is refused with nothing
    executed; reason `app_gate_mismatch` or `plan_digest_mismatch`. Each operation executes
    through the same path as `POST /operations/{id}/execute`. Repeating an accepted approval is
    safe."""

    def accept_approval():
        app = policy.authorize(ledger.get_app(app_id), caller)
        return app, apps.approve(app, body.plan_digests, caller)

    app, queued = await run_in_threadpool(accept_approval)
    prefix = route_prefix(request)
    for op in queued:
        if op["state"] == "apply_queued":
            failure = await dispatch_operation(
                dispatcher, op, "apply", prefix, request.state.request_id
            )
            if failure:
                return failure
    response.headers["Location"] = f"{prefix}/apps/{app_id}"
    response.headers["Retry-After"] = "2"
    return apps.public_app(app, await run_in_threadpool(apps.load, app), prefix)


@router.post(
    "/apps/{app_id}/failover",
    status_code=202,
    response_model=App,
    tags=["apps"],
    summary="Fail an app over: make another replica primary behind an approval.",
)
async def failover_app(
    app_id: str,
    body: AppFailover,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates: accepts a router update that points every `primary_*` input at the chosen
    replica and every `secondary_*` input at the replica that was primary (two replicas swap),
    through the same validation, reference and budget rules as `POST /operations`, and queues its
    plan. The app goes back to `planning_router`, then `awaiting_router_approval`; approve the
    router's digest with `POST /apps/{id}/approve`, after which the app is `ready` again with the
    new `primary`. Allowed only when the app is `ready` and every replica is ready. Refused with
    409 `app_failover_noop` when that replica is already primary, 422 when the index does not
    exist. Idempotent by `Idempotency-Key`: the same caller, key and body returns the same
    router operation; on 503 dispatch_unconfirmed retry the identical request."""
    app, operation_id = await run_in_threadpool(
        apps.failover, app_id, body, caller, idempotency_key
    )
    ops = await run_in_threadpool(apps.load, app)
    prefix = route_prefix(request)
    if ops[operation_id]["state"] == "queued":
        failure = await dispatch_operation(
            dispatcher, ops[operation_id], "plan", prefix, request.state.request_id
        )
        if failure:
            return failure
    response.headers["Location"] = f"{prefix}/apps/{app_id}"
    response.headers["Retry-After"] = "2"
    return apps.public_app(app, ops, prefix)


@router.post(
    "/apps/{app_id}/destroy",
    status_code=202,
    response_model=App,
    tags=["apps"],
    summary="Tear an app down in order: the router, then the replicas, each behind an approval.",
)
async def destroy_app(
    app_id: str,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates: allowed when the app is `ready`, or `failed` with nothing in flight. Accepts a
    destroy of the router (when it is deployed) through the ordinary policy path, so an
    environment that forbids destroy refuses with 403 `policy_denied` and nothing is accepted.
    Approve the router's destroy digest with `POST /apps/{id}/approve` (`teardown` lists it) at
    `awaiting_router_destroy_approval`; once it succeeds the replicas' destroys are accepted and
    planned, approved the same way at `awaiting_replica_destroy_approval`, and the app becomes
    `destroyed`. Nothing applies without those approvals. Needs change rights in the app's
    environment (view-only is 403). Idempotent by `Idempotency-Key`: replaying any earlier
    teardown's key returns that teardown, never a new one; a failed teardown can be retried with
    a new key. While a teardown is unfinished (including `uncertain`), any key accepts nothing
    and restarts its workflow if it stopped: that is how to resume after an operator reconciled
    an uncertain destroy. A failed app that still holds `planned` operations needs
    `POST /apps/{id}/discard` first (409 `app_destroy_unavailable`, next_action
    `discard_planned_operations`)."""
    app, router_id = await run_in_threadpool(apps.destroy, app_id, caller, idempotency_key)
    ops = await run_in_threadpool(apps.load, app)
    prefix = route_prefix(request)
    try:
        for operation_id in apps.queued_ids(app, ops):
            await dispatcher.dispatch(operation_id, "plan")
        if apps.derive_state(app, ops) not in ("destroyed", "failed"):
            await dispatcher.dispatch_teardown(app_id)
    except Exception:
        location = f"{prefix}/apps/{app_id}"
        return JSONResponse(
            {
                "error": {
                    "code": "dispatch_unconfirmed",
                    "detail": "request recorded; Temporal dispatch could not be confirmed",
                    "next_action": "retry_same_request",
                    "status_url": location,
                    "request_id": request.state.request_id,
                }
            },
            status_code=503,
            headers={"Location": location, "Retry-After": "2"},
        )
    response.headers["Location"] = f"{prefix}/apps/{app_id}"
    response.headers["Retry-After"] = "2"
    return apps.public_app(app, ops, prefix)


@router.post(
    "/apps/{app_id}/discard",
    response_model=App,
    tags=["apps"],
    summary="Discard the plans a failed app still holds, releasing their budget.",
)
async def discard_app(app_id: str, request: Request, caller: CallerDep):
    """Mutates: when an app has failed (a member was discarded, a router was refused, a rollout
    timed out), the replicas or destroys that were already planned stay reserved against the
    budget until they are discarded. This discards every one of them without applying anything
    (each is audited as an `operation.discard`, the request as `app.discard`). Needs change
    rights in the app's environment. Only a `failed` app (409 `app_discard_unavailable`
    otherwise); repeating it changes nothing. Deployed replicas are not touched: tear the app
    down with `POST /apps/{id}/destroy` afterwards."""
    app, _ = await run_in_threadpool(apps.discard, app_id, caller)
    ops = await run_in_threadpool(apps.load, app)
    return apps.public_app(app, ops, route_prefix(request))


@router.get(
    "/operations",
    response_model=OperationPage,
    tags=["operations"],
    summary="List the caller's operations, most stable with `before`.",
)
def list_operations(
    request: Request,
    caller: CallerDep,
    offset: int = Query(0, ge=0, le=MAX_PAGE_POSITION),
    limit: int = Query(20, ge=1, le=100),
    before: str | None = Query(default=None, pattern=r"^op_[0-9a-f]{32}$"),
    resource_id: str | None = Query(
        default=None,
        pattern=r"^res_[0-9a-f]{32}$",
        description="Only operations on this resource. Composes with `state`, `before` and "
        "`offset`/`limit`; requires capability `operation_filters`.",
    ),
    state: Literal[OPERATION_STATES] | None = Query(
        default=None,
        description="Only operations currently in this state. Composes with `resource_id`, "
        "`before` and `offset`/`limit`; requires capability `operation_filters`.",
    ),
    action: Literal["deploy", "destroy", "drift_check"] | None = Query(
        default=None,
        description="Only operations of this action. Drift checks are listed only when asked "
        "for here or with `include_checks`; requires capability `drift_checks`.",
    ),
    include_checks: bool = Query(
        default=False,
        description="Also list `drift_check` operations, which are omitted by default so older "
        "clients never meet an action they do not know; requires capability `drift_checks`.",
    ),
):
    """Read-only and idempotent. Paginate with `before` (an operation ID) for a stable walk
    even while new operations are created; `offset` pagination can skip or repeat items under
    concurrent writes. `resource_id` and `state` filter inside the same visibility condition as
    an unfiltered list; a `before` anchor that does not match the active filters is treated as
    invisible and returns 404, exactly like an anchor belonging to another caller."""
    if before is not None and offset != 0:
        raise HTTPException(422, "before and nonzero offset cannot be combined")
    units = [u.name for u in tenants.units_for(caller)] if tenants.enabled() else None
    items = ledger.list_operations(
        caller.id, units, offset, limit + 1, before, resource_id, state, action, include_checks
    )
    shown = items[:limit]
    return {
        "items": [public_operation(op, route_prefix(request)) for op in shown],
        "next_offset": offset + limit if before is None and len(items) > limit else None,
        "next_before": shown[-1]["id"] if len(items) > limit else None,
    }


@router.get(
    "/operations/{operation_id}",
    response_model=Operation,
    tags=["operations"],
    summary="Poll one operation's current state.",
)
async def get_operation(
    operation_id: str,
    request: Request,
    caller: CallerDep,
    wait: int = Query(
        0,
        ge=0,
        le=30,
        description="Seconds to long-poll for a state change before answering; 0 (default) "
        "answers immediately, exactly like a plain poll. Requires capability "
        "`operation_long_poll` when nonzero.",
    ),
):
    """Read-only and idempotent. Poll this until `terminal`, following `next_action`; use
    `poll_after_seconds` as the suggested delay between polls. With `wait` greater than zero and
    a `next_action` of `poll`, holds the request open (checking every 0.5s) until the state
    changes or `wait` elapses, then answers with the operation's current state either way."""

    def read():
        return public_operation(
            policy.authorize(ledger.get(operation_id), caller), route_prefix(request)
        )

    current = await run_in_threadpool(read)
    deadline = time.monotonic() + wait
    while wait > 0 and current["next_action"] == "poll" and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        current = await run_in_threadpool(read)
    return current


@router.get(
    "/operations/{operation_id}/events",
    response_model=EventPage,
    tags=["operations"],
    summary="This operation's append-only audit trail.",
)
def operation_events(
    operation_id: str,
    caller: CallerDep,
    after: int = Query(0, ge=0, le=MAX_PAGE_POSITION),
    limit: int = Query(20, ge=1, le=100),
):
    """Read-only and idempotent. Diagnostic detail beyond the operation's own `state`; events
    are never edited or deleted. Pass the previous page's `next_after` to continue."""
    policy.authorize(ledger.get(operation_id), caller)
    items = ledger.events(operation_id, after, limit + 1)
    shown = items[:limit]
    return {"items": shown, "next_after": shown[-1]["seq"] if len(items) > limit else None}


@router.post(
    "/operations/{operation_id}/execute",
    status_code=202,
    response_model=Operation,
    tags=["operations"],
    summary="Apply the plan with this exact digest.",
)
async def execute_operation(
    operation_id: str,
    body: Execute,
    request: Request,
    caller: CallerDep,
    response: Response,
    dispatcher: DispatcherDep,
):
    """Execute the inspected plan once. Repeated calls return its current operation state.

    Returns after Temporal accepts execution. On 503 dispatch_unconfirmed, retry this same
    operation/digest. A matching digest is required. Applying can create or delete resources. An
    uncertain outcome needs operator reconciliation; never submit replacement work blindly.
    """

    def accept_execution():
        op = policy.authorize(ledger.get(operation_id), caller, change=True)
        policy.check_guardrails(op)
        return ledger.execute(operation_id, body.plan_digest, caller.id)

    op = await run_in_threadpool(accept_execution)
    if op["state"] == "apply_queued":
        failure = await dispatch_operation(
            dispatcher, op, "apply", route_prefix(request), request.state.request_id
        )
        if failure:
            return failure
    response.headers["Location"] = f"{route_prefix(request)}/operations/{op['id']}"
    response.headers["Retry-After"] = "2"
    return public_operation(op, route_prefix(request))


@router.post(
    "/operations/{operation_id}/discard",
    response_model=Operation,
    tags=["operations"],
    summary="Abandon a planned operation without applying it.",
)
def discard_operation(operation_id: str, request: Request, caller: CallerDep):
    """Mutates: releases the resource and restores any budget reservation. Idempotent: repeated
    calls return the same operation unchanged once discarded.

    Only a `planned` operation can be discarded. This releases its resource for a new intent
    and restores any budget reservation to its pre-acceptance amount; it never touches the
    Terraform workspace or saved plan file, and never dispatches to Temporal. Repeated calls
    return the same operation unchanged. An operation that is already executing or finished
    cannot be discarded.
    """
    policy.authorize(ledger.get(operation_id), caller, change=True)
    op = ledger.discard(operation_id, caller.id)
    return public_operation(op, route_prefix(request))


@router.post(
    "/operations/{operation_id}/reconcile",
    response_model=Operation,
    tags=["operations"],
    summary="Record an operator's verdict on an uncertain operation.",
)
def reconcile_operation(operation_id: str, body: Reconcile, request: Request, caller: CallerDep):
    """Mutates: records the human decision and releases the resource's reservation. Idempotent:
    repeated calls with the same outcome return the operation unchanged. Operator-only.

    Resolve an `uncertain` operation after a human inspects Terraform state and provider
    evidence themselves. This runs no Terraform and does not touch cloud state itself.
    `succeeded` requires the operation to have reached apply; an operation that never executed
    cannot be reconciled as succeeded.
    """
    policy.authorize_reconcile(ledger.get(operation_id), caller)
    op = ledger.reconcile(operation_id, caller.id, body.outcome, body.reason)
    return public_operation(op, route_prefix(request))


UPGRADE_FILTER_SCAN = 500  # most rows one filtered request examines


def _resource_view(resource: dict, caller_id: str, prefix: str, allowed: set[str]) -> dict:
    view = public_resource(resource, caller_id, prefix)
    if view["state"] == "ready":
        view["latest_version"], view["upgrade_available"] = catalog.upgrade_available(
            view["pattern"], view["version"], allowed
        )
    return view


@router.get(
    "/resources",
    response_model=ResourcePage,
    tags=["resources"],
    summary="List the caller's resource inventory, oldest first.",
)
def list_resources(
    request: Request,
    caller: CallerDep,
    after: str | None = Query(default=None, pattern=r"^res_[0-9a-f]{32}$"),
    limit: int = Query(20, ge=1, le=100),
    pattern: str | None = Query(
        default=None,
        description="Only resources of this pattern. Composes with `environment`, `state`, "
        "`after` and `limit`; requires capability `resource_filters`.",
    ),
    environment: str | None = Query(
        default=None,
        description="Only resources in this environment. Composes with `pattern`, `state`, "
        "`after` and `limit`; requires capability `resource_filters`.",
    ),
    state: Literal["pending", "ready", "destroyed"] | None = Query(
        default=None,
        description="Only resources in this state. Composes with `pattern`, `environment`, "
        "`after` and `limit`; requires capability `resource_filters`.",
    ),
    label: str | None = Query(
        default=None,
        description="Only resources carrying this label, as `key=value`. Composes with the "
        "other filters; requires capability `resource_labels`.",
    ),
    drift_status: Literal["in_sync", "drifted", "unknown"] | None = Query(
        default=None,
        description="Only resources whose latest drift verdict is this. Composes with the "
        "other filters; requires capability `drift_checks`.",
    ),
    upgrade_available: bool | None = Query(
        default=None,
        description="Only resources whose `upgrade_available` equals this (unknown, i.e. "
        "null, never matches). Computed after the database page, so a page can hold fewer "
        "than `limit` items even when `next_after` is set; keep following `next_after`. "
        "Requires capability `upgrade_detection`.",
    ),
):
    """Read-only and idempotent. Outputs are from each resource's last successful apply;
    sensitive ones are withheld by name in `withheld_outputs`. `latest_operation_id` points at
    the in-flight or most recent operation on that resource. Pass the previous page's last ID
    as `after` for the next page.
    """
    units = [u.name for u in tenants.units_for(caller)] if tenants.enabled() else None
    if label is not None and "=" not in label:
        raise HTTPException(422, "label must be key=value")
    allowed = set(policy.available(caller))
    prefix = route_prefix(request)
    if upgrade_available is None:
        items = ledger.list_resources(
            caller.id, units, after, limit + 1, pattern, environment, state, label, drift_status
        )
        shown = items[:limit]
        return {
            "items": [_resource_view(r, caller.id, prefix, allowed) for r in shown],
            "next_after": shown[-1]["id"] if len(items) > limit else None,
        }
    # Computed filter: scan SQL pages (bounded) and keep matches. If the bound stops the scan
    # early, next_after is the last row scanned, so paging still never skips or repeats rows.
    matched, scanned, cursor, exhausted = [], 0, after, False
    while len(matched) <= limit and scanned < UPGRADE_FILTER_SCAN:
        chunk = ledger.list_resources(
            caller.id, units, cursor, 101, pattern, environment, state, label, drift_status
        )
        page = chunk[:100]
        for r in page:
            view = _resource_view(r, caller.id, prefix, allowed)
            scanned += 1
            if view["upgrade_available"] is upgrade_available:
                matched.append((r["id"], view))
                if len(matched) > limit:
                    break
        if len(matched) > limit:
            break
        if len(chunk) <= 100:
            exhausted = True
            break
        cursor = page[-1]["id"]
    if len(matched) > limit:
        shown = matched[:limit]
        return {"items": [v for _, v in shown], "next_after": shown[-1][0]}
    return {"items": [v for _, v in matched], "next_after": None if exhausted else cursor}


@router.get(
    "/resources/{resource_id}",
    response_model=Resource,
    tags=["resources"],
    summary="Read one resource from the caller's inventory.",
)
def get_resource(resource_id: str, request: Request, caller: CallerDep):
    """Read-only and idempotent. Outputs are from its last successful apply; sensitive ones
    are withheld by name in `withheld_outputs`. `latest_operation_id` points at the in-flight
    or most recent operation on it.
    """
    return _resource_view(
        policy.authorize(ledger.resource(resource_id), caller),
        caller.id,
        route_prefix(request),
        set(policy.available(caller)),
    )


@router.post(
    "/resources/{resource_id}/promote",
    status_code=202,
    response_model=Operation,
    tags=["resources"],
    summary="Deploy a ready resource's exact pattern commit into another environment.",
)
async def promote_resource(
    resource_id: str,
    body: Promote,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates: submits a deploy for a NEW resource through exactly the path of
    `POST /operations` (placement, injected inputs, allowed patterns and regions, guardrails,
    budget), with the source's pattern, version and applied commit (a tag that moved since is
    refused 409 `revision_moved`), the source's caller inputs merged with `inputs`, and labels
    from the source plus `labels` plus `promoted_from`. The target environment injects its own
    placement; the source's is never copied. Inputs the source took from `input_refs` cannot
    cross environments: supply `input_refs` (or `inputs`) for each, else 422
    `promotion_needs_refs` names them. The source must be `ready` (409 `resource_not_ready`) and
    visible to you (404). Promoting into the source's own business unit and environment is 422.
    Returns a planned-later operation; review and execute it as usual: promotion never applies by
    itself. Idempotent by `Idempotency-Key`; on 503 dispatch_unconfirmed retry the identical
    request. Requires capability `promotion`."""
    op = await run_in_threadpool(promotion.accept, resource_id, body, caller, idempotency_key)
    if op["state"] == "queued":
        failure = await dispatch_operation(
            dispatcher, op, "plan", route_prefix(request), request.state.request_id
        )
        if failure:
            return failure
    response.headers["Location"] = f"{route_prefix(request)}/operations/{op['id']}"
    response.headers["Retry-After"] = "2"
    return public_operation(op, route_prefix(request))


@router.post(
    "/resources/{resource_id}/upgrade",
    status_code=202,
    response_model=Operation,
    tags=["resources"],
    summary="Plan moving a ready resource to another pattern version.",
)
async def upgrade_resource(
    resource_id: str,
    body: Upgrade,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates: submits an update of this resource through exactly the path of
    `POST /operations` (placement re-derived, guardrails, budget), with the new `version`, the
    resource's stored caller inputs (never injected placement) merged with `inputs`, and its
    labels and input references. 409 `upgrade_noop` when it already runs that version, 409 when
    it is not `ready` or has an operation in flight, 404 when not visible, 403 without deploy
    rights, 422 for an unknown version. Returns an operation to review and execute as usual:
    an upgrade never applies by itself. Idempotent by `Idempotency-Key`; on 503
    dispatch_unconfirmed retry the identical request. Requires capability `resource_upgrade`."""
    op = await run_in_threadpool(
        promotion.accept_upgrade, resource_id, body, caller, idempotency_key
    )
    if op["state"] == "queued":
        failure = await dispatch_operation(
            dispatcher, op, "plan", route_prefix(request), request.state.request_id
        )
        if failure:
            return failure
    response.headers["Location"] = f"{route_prefix(request)}/operations/{op['id']}"
    response.headers["Retry-After"] = "2"
    return public_operation(op, route_prefix(request))


@router.post(
    "/resources/{resource_id}/drift-check",
    status_code=202,
    response_model=Operation,
    tags=["resources"],
    summary="Check a ready resource for changes made outside the API.",
)
async def check_drift(
    resource_id: str,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: Key,
    dispatcher: DispatcherDep,
):
    """Mutates only the resource's drift fields: runs a read-only `terraform plan
    -refresh-only` and never applies anything, reserves no budget and changes neither the
    resource's state nor its version. Returns a `drift_check` operation; poll it until
    `terminal`. It ends `succeeded` with `drift` (empty when in sync) and updates the
    resource's `drift_status`, or `failed` with a `diagnostic` (a held state lock fails it at
    once, never unlocked). Allowed only for a `ready` resource with no operation in flight:
    otherwise 409 (`resource_busy`, `resource_not_ready`). Idempotent by `Idempotency-Key`;
    on 503 dispatch_unconfirmed retry the identical request. Requires capability
    `drift_checks`."""
    op = await run_in_threadpool(drift.accept, resource_id, caller, idempotency_key)
    if op["state"] == "queued":
        failure = await dispatch_operation(
            dispatcher, op, "plan", route_prefix(request), request.state.request_id
        )
        if failure:
            return failure
    response.headers["Location"] = f"{route_prefix(request)}/operations/{op['id']}"
    response.headers["Retry-After"] = "2"
    return public_operation(op, route_prefix(request))


ADMIN_ERRORS = {code: {"model": ErrorEnvelope} for code in (412, 428)}


def _audit_as(request: Request, action: str, name: str | None = None) -> None:
    """Name the team in the `refused` audit event if this request is refused."""
    request.state.audit_action = f"team.{action}" + (f" {name}" if name else "")


def _etag(response: Response, view: dict) -> dict:
    response.headers["ETag"] = f'"{view["revision"]}"'
    return view


@router.get(
    "/admin/teams",
    response_model=team_admin.TeamList,
    tags=["admin"],
    summary="List teams with resources, budgets and finding counts. Operators only.",
)
def admin_list_teams(caller: OperatorDep):
    """Read-only. Operators only (403 otherwise), in every tenants source. Never contains
    target identifiers; `GET /admin/teams/{name}` does. With the file source the list is the
    mapping file, read-only (revision 0)."""
    return team_admin.list_teams()


@router.post(
    "/admin/teams/validate",
    response_model=team_admin.ValidationReport,
    tags=["admin"],
    summary="Check a team document and its impact; writes nothing.",
)
def admin_validate_team(body: team_admin.TeamValidate, caller: OperatorDep):
    """Mutates nothing and records no audit event. When the team exists the document is judged
    as an update (impact on its resources and budgets); otherwise as a create."""
    return team_admin.validate(body)


@router.post(
    "/admin/teams/import",
    response_model=team_admin.ImportReport,
    tags=["admin"],
    summary="Dry-run or apply a whole tenants.yaml; all or nothing.",
)
def admin_import_teams(body: team_admin.TeamImport, request: Request, caller: OperatorDep):
    """With `apply: false` only reports create/update/unchanged per team and findings. With
    `apply: true` and a `reason`, every changed team is written in one transaction: an error in
    any team, or an unacknowledged warning, writes nothing. Teams missing from the text are left
    alone. Top-level `operators`/`auditors` are ignored: they come from settings."""
    _audit_as(request, "import")
    return team_admin.import_teams(caller.id, body)


@router.post(
    "/admin/teams",
    status_code=201,
    response_model=team_admin.TeamView,
    tags=["admin"],
    summary="Create a team.",
)
def admin_create_team(
    body: team_admin.TeamCreate, request: Request, response: Response, caller: OperatorDep
):
    """Mutates. Records `accepted` with the actor, team name and revision, then stores revision
    1. Refused 422 on any error finding, 409 if the name exists or the source is `file`."""
    _audit_as(request, "create", body.name)
    view = team_admin.create(caller.id, body)
    response.headers["Location"] = f"{route_prefix(request)}/admin/teams/{body.name}"
    return _etag(response, view)


@router.get(
    "/admin/teams/{name}",
    response_model=team_admin.TeamView,
    tags=["admin"],
    summary="One team's full document and revision (ETag). Operators only.",
)
def admin_get_team(name: str, response: Response, caller: OperatorDep):
    """Read-only. Includes target identifiers, so operators only. The `ETag` header is the
    revision to send as `If-Match` on the next write."""
    return _etag(response, team_admin.get_team(name))


@router.put(
    "/admin/teams/{name}",
    response_model=team_admin.TeamView,
    tags=["admin"],
    summary="Replace a team's whole document.",
    responses=ADMIN_ERRORS,
)
def admin_replace_team(
    name: str,
    body: team_admin.TeamReplace,
    request: Request,
    response: Response,
    caller: OperatorDep,
    if_match: IfMatch = None,
):
    """Mutates. Needs `If-Match` (428 missing, 412 stale). Error findings: 422 with the
    findings, nothing written. Every warning code present must be listed in
    `acknowledge_warnings`, else 409 `warnings_not_acknowledged` with the list. A document equal
    to the current one is 409 `team_unchanged`."""
    _audit_as(request, "update", name)
    return _etag(response, team_admin.replace(caller.id, name, body, if_match))


@router.get(
    "/admin/teams/{name}/revisions",
    response_model=team_admin.RevisionPage,
    tags=["admin"],
    summary="A team's revision history, newest first. Operators only.",
)
def admin_list_team_revisions(
    name: str,
    caller: OperatorDep,
    limit: Annotated[int, Query(ge=1, le=100, description="Revisions per page.")] = 20,
    before: Annotated[
        int | None, Query(ge=1, description="Only revisions older than this number.")
    ] = None,
):
    """Read-only; revisions are append-only and never change."""
    return team_admin.revisions(name, before, limit)


@router.get(
    "/admin/teams/{name}/revisions/{revision}",
    response_model=team_admin.RevisionView,
    tags=["admin"],
    summary="One revision's document and its diff from the previous one.",
)
def admin_get_team_revision(name: str, revision: int, caller: OperatorDep):
    """Read-only. `change` lists the added, removed and changed paths against the previous
    revision (everything is `added` for revision 1)."""
    return team_admin.get_revision(name, revision)


@router.post(
    "/admin/teams/{name}/revert",
    response_model=team_admin.TeamView,
    tags=["admin"],
    summary="Make a past revision current, as a new revision.",
    responses=ADMIN_ERRORS,
)
def admin_revert_team(
    name: str,
    body: team_admin.TeamRevert,
    request: Request,
    response: Response,
    caller: OperatorDep,
    if_match: IfMatch = None,
):
    """Mutates. Same `If-Match`, validation and warning acknowledgement rules as `PUT`; the
    document of the named revision becomes a new revision (history is never rewritten)."""
    _audit_as(request, "revert", name)
    return _etag(response, team_admin.revert(caller.id, name, body, if_match))


@router.post(
    "/admin/teams/{name}/archive",
    response_model=team_admin.TeamView,
    tags=["admin"],
    summary="Archive a team: no new intents; destroys still work.",
    responses=ADMIN_ERRORS,
)
def admin_archive_team(
    name: str,
    body: team_admin.TeamArchive,
    request: Request,
    response: Response,
    caller: OperatorDep,
    if_match: IfMatch = None,
):
    """Mutates. Needs `If-Match`. Refused 409 `team_has_resources` while resources exist unless
    `force_archive`. An archived team's members still see its resources and may destroy them;
    new intents, updates, promotions and placement are refused 409."""
    _audit_as(request, "archive", name)
    return _etag(response, team_admin.archive(caller.id, name, body, if_match))


@router.post(
    "/admin/teams/{name}/unarchive",
    response_model=team_admin.TeamView,
    tags=["admin"],
    summary="Make an archived team active again.",
    responses=ADMIN_ERRORS,
)
def admin_unarchive_team(
    name: str,
    body: team_admin.TeamUnarchive,
    request: Request,
    response: Response,
    caller: OperatorDep,
    if_match: IfMatch = None,
):
    """Mutates. Needs `If-Match`; the stored document is validated again and error findings
    refuse the change."""
    _audit_as(request, "unarchive", name)
    return _etag(response, team_admin.unarchive(caller.id, name, body, if_match))


app.include_router(router, generate_unique_id_function=operation_id)
app.include_router(router, prefix="/v1", generate_unique_id_function=operation_id)


CONSOLE_DIR = Path(__file__).parent / "console"
CONSOLE_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _console_file(name: str, media_type: str) -> Response:
    """Static, data-free assets: no auth. Every data call the page makes carries the caller's
    own credentials against the ordinary JSON API."""
    return Response(
        (CONSOLE_DIR / name).read_bytes(), media_type=media_type, headers=CONSOLE_HEADERS
    )


@app.get("/console", include_in_schema=False)
def console_page():
    return _console_file("index.html", "text/html; charset=utf-8")


@app.get("/console/console.js", include_in_schema=False)
def console_script():
    return _console_file("console.js", "text/javascript; charset=utf-8")


@app.get("/console/console.css", include_in_schema=False)
def console_style():
    return _console_file("console.css", "text/css; charset=utf-8")


@app.get("/v1/openapi.json", include_in_schema=False)
def versioned_openapi():
    document = deepcopy(app.openapi())
    document["paths"] = {
        path: value for path, value in document["paths"].items() if path.startswith("/v1/")
    }
    return document
