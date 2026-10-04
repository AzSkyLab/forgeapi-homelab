"""App rollouts: several replicas, then a router that reads their outputs.

An app is a thin layer over ordinary operations. Every member goes through the same intent
validation, placement, budget and plan review as `POST /operations`; this module only builds the
member intents, derives the app's state from its members, and gates the two approvals."""

from fastapi import HTTPException
from pydantic import ValidationError

from app import cloud_targets, ledger, policy, tenants
from app.contracts import AppFailover, AppIntent, Intent, OperationError, canonical_intent
from app.tenants import Caller, TenancyError

NOTICE = (
    "replicas were validated and budget-checked now; the router's budget is checked when it is "
    "accepted, after every replica has succeeded"
)
ACTIVE = {"apply_queued", "applying", "succeeded"}
NEXT_ACTION = {
    "awaiting_replica_approval": "approve_with_plan_digests",
    "awaiting_router_approval": "approve_with_plan_digests",
    "awaiting_router_destroy_approval": "approve_with_plan_digests",
    "awaiting_replica_destroy_approval": "approve_with_plan_digests",
    "ready": "done",
    "destroyed": "done",
    "failed": "inspect_failure",
    "uncertain": "reconcile_with_operator",
}
DISCARD = "discard_planned_operations"
# A refusal that clears by itself (a drift check holds the resource for a moment): the workflows
# retry it on the next cycle instead of failing the app.
TRANSIENT = {"resource_busy", "resource_changed"}


def transient(error: Exception) -> bool:
    return isinstance(error, OperationError) and error.reason in TRANSIENT


def ensure_access(app: dict, caller: Caller) -> None:
    """The requester's groups are a snapshot from when they asked. Before accepting more work in
    their name later (the router, the replica destroys), check the current mapping still lets
    them deploy to the app's business unit and environment."""
    if not tenants.enabled() or not app.get("business_unit"):
        return
    unit = tenants.load().get(app["business_unit"])
    env = unit.environments.get(app.get("environment")) if unit else None
    if env is None or not unit.can_deploy(caller, env):
        raise OperationError(
            403,
            "the requester no longer has access to this business unit and environment",
            "requester_access_revoked",
            next_action="inspect_failure",
        )


def require_change(app: dict, ops: dict[str, dict], caller: Caller) -> None:
    """Changing an app needs the same rights as changing its members: viewing is not enough."""
    policy.authorize(ops[app["members"][0]["operation_id"]], caller, change=True)


def queued_ids(app: dict, ops: dict[str, dict]) -> list[str]:
    """Operations accepted for this app whose plan phase has not started (their dispatch was
    lost); the workflows and the API start those plans again."""
    return [i for i in op_ids(app) if ops[i]["state"] == "queued"]


def planned_ids(app: dict, ops: dict[str, dict]) -> list[str]:
    """Reviewed plans still holding a reservation, which a failed app leaves behind."""
    return [i for i in op_ids(app) if ops[i]["state"] == "planned"]


def refuse_member_change(resource_id: str, caller: Caller) -> None:
    """A replica or router changed directly would make the app's derived state wrong. Refuse
    deploy, update, upgrade and destroy on them until the app is destroyed; drift checks and
    reads are separate endpoints and stay allowed."""
    policy.authorize(ledger.resource(resource_id), caller)
    owner = ledger.app_for_resource(resource_id)
    if owner and derive_state(owner, load(owner)) != "destroyed":
        raise OperationError(
            409,
            f"resource is a member of app {owner['id']}; change it through the app "
            "(failover, destroy), not directly",
            "app_member",
            next_action="use_app_endpoints",
        )


def member_intent(app: dict, spec: dict, role: str, refs: dict | None = None) -> Intent:
    """The ordinary intent for one member. Our `app`/`app_role` labels win over caller labels."""
    labels = {
        **(app.get("labels") or {}),
        **(spec.get("labels") or {}),
        "app": app["name"],
        "app_role": role,
    }
    try:
        return Intent(
            pattern=spec["pattern"],
            version=spec.get("version"),
            size=spec.get("size"),
            business_unit=app.get("business_unit_request"),
            environment=app.get("environment_request"),
            inputs=spec.get("inputs") or {},
            labels=labels,
            input_refs=refs,
        )
    except ValidationError:
        raise HTTPException(
            422, "app labels and member labels together exceed 16 labels or are invalid"
        ) from None


def accept(body: AppIntent, caller: Caller, key: str) -> dict:
    """Validate everything up front, then accept all replicas and the app atomically."""
    material = body.model_dump(mode="json")
    if existing := ledger.replay_app(caller.id, key, material):
        return policy.authorize(existing, caller)
    base = {
        "name": body.name,
        "labels": body.labels or {},
        "business_unit_request": body.business_unit,
        "environment_request": body.environment,
        "caller_groups": sorted(caller.groups),
        "router_spec": body.router.model_dump(mode="json"),
    }
    base["primary"] = initial_primary(base["router_spec"])
    replicas, placement = [], None
    for spec in body.replicas:
        intent = member_intent(base, spec.model_dump(mode="json"), "replica")
        resolved, budget = policy.validate(intent, caller)
        replicas.append((canonical_intent(intent), resolved, budget))
        placement = placement or (resolved.get("business_unit"), resolved.get("environment"))
    check_router(base, caller)
    base["business_unit"], base["environment"] = placement
    return ledger.accept_app(caller.id, key, material, replicas, base)


def check_router(base: dict, caller: Caller) -> None:
    """What can be checked before the replicas exist: the router's pattern, version and placement
    are acceptable to this caller. Its references and budget are checked when it is accepted."""
    spec = base["router_spec"]
    resolved = policy.resolve(spec["pattern"], spec.get("version"), caller)
    if tenants.enabled():
        unit = tenants.select_unit(caller, base["business_unit_request"])
        where = tenants.place(caller, unit, base["environment_request"], spec["pattern"])
        cloud_targets.select(where, resolved.pattern.cloud)


def accept_router(app: dict) -> dict:
    """Accept the router once the replicas have succeeded, exactly as `POST /operations` would:
    same validation, same budget check, same ledger code. Raises on refusal."""
    caller = Caller(app["actor"], frozenset(app["caller_groups"]))
    ensure_access(app, caller)
    spec = app["router_spec"]
    ops = ledger.operations_by_id([m["operation_id"] for m in app["members"]])
    refs = {
        name: {
            "resource_id": ops[app["members"][ref["replica"]]["operation_id"]]["resource_id"],
            "output": ref["output"],
        }
        for name, ref in spec["replica_refs"].items()
    }
    intent = member_intent(app, spec, "router", refs)
    resolved, budget = policy.validate(intent, caller)
    return ledger.accept_router(app["id"], canonical_intent(intent), resolved, budget)


def refusal_text(error: Exception, what: str = "router") -> str:
    """A safe, short reason: the same text a caller would have seen from POST /operations."""
    if isinstance(error, (HTTPException, OperationError)):
        reason = getattr(error, "reason", None)
        detail = error.detail if isinstance(error.detail, str) else f"invalid {what} intent"
        return f"{what} refused ({reason}): {detail}" if reason else f"{what} refused: {detail}"
    if isinstance(error, TenancyError):
        return f"{what} refused: {error.message}"
    return f"{what} refused"


def router_ops(app: dict) -> list[str]:
    """Every router operation, oldest first: the rollout's, then one per failover. Rows stored
    before failover existed only have `router_operation_id`."""
    if app.get("router_operations"):
        return app["router_operations"]
    return [app["router_operation_id"]] if app.get("router_operation_id") else []


def current_router(app: dict, ops: dict[str, dict]) -> str | None:
    """The router operation the app is described by. A failover whose plan failed (a discarded
    or expired plan changes nothing that is deployed) is skipped, so the app returns to the
    previous router; the rollout's own router is never skipped."""
    ids = router_ops(app)
    for operation_id in reversed(ids[1:]):
        if ops[operation_id]["state"] != "failed":
            return operation_id
    return ids[0] if ids else None


def initial_primary(spec: dict) -> int:
    """The replica the first router treats as primary: the replica behind a `primary_*` ref."""
    for name, ref in spec["replica_refs"].items():
        if name.startswith("primary_"):
            return ref["replica"]
    return 0


def primary_of(app: dict, ops: dict[str, dict]) -> int:
    """The replica the deployed router treats as primary: that of the newest router operation
    that has succeeded (a failover still being planned or applied has not changed it yet)."""
    targets = {f["operation_id"]: f["primary"] for f in app.get("failovers") or []}
    for operation_id in reversed(router_ops(app)):
        if ops[operation_id]["state"] == "succeeded":
            return targets.get(operation_id, initial_primary(app["router_spec"]))
    return initial_primary(app["router_spec"])


def teardown_ids(app: dict) -> list[str]:
    teardown = app.get("teardown") or {}
    return ([teardown["router"]] if teardown.get("router") else []) + (
        teardown.get("replicas") or []
    )


def op_ids(app: dict) -> list[str]:
    return [m["operation_id"] for m in app["members"]] + router_ops(app) + teardown_ids(app)


def derive_teardown_state(app: dict, ops: dict[str, dict]) -> str:
    """Teardown progress from the destroy operations: router first (planning, awaiting its
    approval, destroying), then the replicas once `teardown.replicas` is recorded. A failed or
    uncertain destroy (or a refusal recorded on the teardown) stops it."""
    teardown = app["teardown"]
    router = ops[teardown["router"]]["state"] if teardown.get("router") else None
    replicas = (
        [ops[i]["state"] for i in teardown["replicas"]]
        if teardown.get("replicas") is not None
        else None
    )
    states = ([router] if router else []) + (replicas or [])
    if "uncertain" in states:
        return "uncertain"
    if "failed" in states or teardown.get("error"):
        return "failed"
    if replicas is not None:
        if all(state == "succeeded" for state in replicas):
            return "destroyed"
        if any(state in ACTIVE for state in replicas):
            return "destroying_replicas"
        if all(state == "planned" for state in replicas):
            return "awaiting_replica_destroy_approval"
        return "planning_replica_destroy"
    if router and router != "succeeded":
        return {
            "queued": "planning_router_destroy",
            "planning": "planning_router_destroy",
            "planned": "awaiting_router_destroy_approval",
            "apply_queued": "destroying_router",
            "applying": "destroying_router",
        }[router]
    return "planning_replica_destroy"


def derive_state(app: dict, ops: dict[str, dict]) -> str:
    """The app's state, computed from its members' operation states.

    failed    - any member failed (a discarded plan is a failed operation) or `app.error` is set;
    uncertain - any member uncertain (checked first: it needs an operator, not a new request);
    then by progress: replicas queued/planning/planned -> planning_replicas, all planned ->
    awaiting_replica_approval, any applying -> applying_replicas, all succeeded and no router yet
    -> planning_router; router queued/planning -> planning_router, planned ->
    awaiting_router_approval, applying -> applying_router, succeeded -> ready. A failover is a
    newer router operation, so it walks the same router states again. Once a teardown exists,
    its destroy operations decide instead (see `derive_teardown_state`)."""
    if app.get("teardown"):
        return derive_teardown_state(app, ops)
    replicas = [ops[m["operation_id"]]["state"] for m in app["members"]]
    router_id = current_router(app, ops)
    router = ops[router_id]["state"] if router_id else None
    states = replicas + ([router] if router else [])
    if "uncertain" in states:
        return "uncertain"
    if "failed" in states or app.get("error"):
        return "failed"
    if router:
        return {
            "queued": "planning_router",
            "planning": "planning_router",
            "planned": "awaiting_router_approval",
            "apply_queued": "applying_router",
            "applying": "applying_router",
            "succeeded": "ready",
        }[router]
    if all(state == "succeeded" for state in replicas):
        return "planning_router"
    if any(state in ACTIVE for state in replicas):
        return "applying_replicas"
    if all(state == "planned" for state in replicas):
        return "awaiting_replica_approval"
    return "planning_replicas"


def gate(app: dict, ops: dict[str, dict]) -> list[str]:
    """Operation IDs the app's current approval names, or refuse: the app must be at a gate
    (or already executing it, which makes a repeated approval idempotent)."""
    state = derive_state(app, ops)
    if state in ("awaiting_replica_approval", "applying_replicas"):
        return [m["operation_id"] for m in app["members"]]
    if state in ("awaiting_router_approval", "applying_router"):
        return [current_router(app, ops)]
    if state in ("awaiting_router_destroy_approval", "destroying_router"):
        return [app["teardown"]["router"]]
    if state in ("awaiting_replica_destroy_approval", "destroying_replicas"):
        return list(app["teardown"]["replicas"])
    raise OperationError(
        409,
        f"app is {state}; it is not awaiting approval",
        "app_gate_mismatch",
        next_action=NEXT_ACTION.get(state, "poll"),
    )


def approve(app: dict, plan_digests: dict[str, str], caller: Caller) -> list[dict]:
    """All-or-nothing approval of the current gate: exactly the gate's operations, each with
    its exact digest, through the ordinary execute path."""
    ops = load(app)
    wanted = gate(app, ops)
    if set(plan_digests) != set(wanted):
        raise OperationError(
            409,
            "approval must name exactly the operations at the current gate: " + ", ".join(wanted),
            "app_gate_mismatch",
            next_action="inspect_and_correct_request",
        )
    for operation_id in wanted:
        op = policy.authorize(ops[operation_id], caller, change=True)
        policy.check_guardrails(op)
    return ledger.execute_many({i: plan_digests[i] for i in wanted}, caller.id, app["id"])


def public_app(app: dict, ops: dict[str, dict], prefix: str = "", notice: bool = False) -> dict:
    """Explicit allowlist. Members carry operation and resource IDs, never placement."""

    def member(role, index, operation_id, resource_id):
        op = ops[operation_id]
        return {
            "role": role,
            "index": index,
            "operation_id": operation_id,
            "resource_id": resource_id,
            "state": op["state"],
            "plan_digest": op.get("plan_digest"),
        }

    state = derive_state(app, ops)
    next_action = NEXT_ACTION.get(state, "poll")
    if state == "failed" and planned_ids(app, ops):
        next_action = DISCARD  # `POST .../discard` releases what the other plans reserved
    base = f"{prefix}/apps/{app['id']}"
    router_id = current_router(app, ops)
    resource_index = {m["resource_id"]: m["index"] for m in app["members"]}
    teardown = None
    if app.get("teardown"):
        teardown = (
            [member("router_destroy", None, app["teardown"]["router"], app["router_resource_id"])]
            if app["teardown"].get("router")
            else []
        ) + [
            member(
                "replica_destroy",
                resource_index.get(ops[i]["resource_id"]),
                i,
                ops[i]["resource_id"],
            )
            for i in app["teardown"].get("replicas") or []
        ]
    return {
        "id": app["id"],
        "name": app["name"],
        "state": state,
        "primary": primary_of(app, ops),
        "business_unit": app.get("business_unit"),
        "environment": app.get("environment"),
        "created_at": app["created_at"],
        "updated_at": app["updated_at"],
        "members": [
            member("replica", m["index"], m["operation_id"], m["resource_id"])
            for m in app["members"]
        ],
        "router": member("router", None, router_id, app["router_resource_id"])
        if router_id
        else None,
        "teardown": teardown,
        "error": error_text(app, ops, state),
        "notice": NOTICE if notice else None,
        "next_action": next_action,
        "poll_after_seconds": 5 if next_action == "poll" else None,
        "links": {
            "self": base,
            "approve": f"{base}/approve",
            **({"discard": f"{base}/discard"} if next_action == DISCARD else {}),
        },
    }


def error_text(app: dict, ops: dict[str, dict], state: str) -> str | None:
    """The recorded reason, plus (for a failed app) the plans still reserving budget."""
    error = app["teardown"].get("error") if app.get("teardown") else app.get("error")
    planned = planned_ids(app, ops) if state == "failed" else []
    if planned:
        note = f"planned operations still reserve budget: {', '.join(planned)}; discard them"
        return f"{error}; {note}" if error else note
    return error


def load(app: dict) -> dict[str, dict]:
    return ledger.operations_by_id(op_ids(app))


# --- Failover and teardown ---------------------------------------------------------------------


def known_request(entries: list[dict] | None, actor: str, key: str, material: dict) -> dict | None:
    """The earlier request made with this caller's key, if any. The same key with a different
    body is refused, exactly like a reused key on `POST /operations`."""
    for entry in entries or []:
        if entry.get("key") == f"{actor}:{key}":
            if entry["fingerprint"] != ledger.fingerprint(material):
                raise OperationError(
                    409,
                    "idempotency key already used for different intent",
                    "idempotency_key_reused",
                )
            return entry
    return None


def failover_refs(app: dict, current: int, target: int) -> dict:
    """The router's refs after a failover: `primary_*` inputs follow `target`, `secondary_*`
    inputs follow the replica that was primary; every other ref is unchanged."""
    router = ledger.resource(app["router_resource_id"]) or {}
    refs = dict(router.get("input_refs") or {})
    replica_refs = app["router_spec"]["replica_refs"]
    for name, ref in replica_refs.items():
        if name.startswith("primary_"):
            index = target
        elif name.startswith("secondary_"):
            index = current
        else:
            continue
        refs[name] = {
            "resource_id": app["members"][index]["resource_id"],
            "output": ref["output"],
        }
    return refs


def failover(app_id: str, body: AppFailover, caller: Caller, key: str) -> tuple[dict, str]:
    """Accept a router update that swaps primary and secondary, through the ordinary
    validation, reference and budget rules. Returns (app, the failover's router operation)."""
    app = policy.authorize(ledger.get_app(app_id), caller)
    material = {"app_id": app_id, "primary": body.primary}
    if entry := known_request(app.get("failovers"), caller.id, key, material):
        return app, entry["operation_id"]
    if body.primary >= len(app["members"]):
        raise HTTPException(422, "primary names a replica that does not exist")
    ops = load(app)
    if (state := derive_state(app, ops)) != "ready":
        raise OperationError(
            409,
            f"app is {state}; failover needs a ready app",
            "app_not_ready",
            next_action=NEXT_ACTION.get(state, "poll"),
        )
    if not any(name.startswith("primary_") for name in app["router_spec"]["replica_refs"]):
        raise OperationError(
            409,
            "the router has no primary_* replica_refs, so there is nothing to fail over",
            "app_failover_unsupported",
        )
    for member in app["members"]:
        if (ledger.resource(member["resource_id"]) or {}).get("state") != "ready":
            raise OperationError(
                409,
                f"replica {member['index']} is not ready",
                "app_replica_not_ready",
                next_action="inspect_failure",
            )
    current = primary_of(app, ops)
    if body.primary == current:
        raise OperationError(
            409,
            f"replica {current} is already primary",
            "app_failover_noop",
            next_action="done",
        )
    previous = ledger.resource(app["router_resource_id"]) or {}
    intent = member_intent(
        app, app["router_spec"], "router", failover_refs(app, current, body.primary)
    ).model_copy(update={"resource_id": app["router_resource_id"], "version": previous["version"]})
    resolved, budget = policy.validate(intent, caller)
    return ledger.accept_failover(
        caller.id,
        app_id,
        key,
        material,
        (canonical_intent(intent), resolved, budget),
        body.primary,
        app["updated_at"],
    )


def destroy_intent(resource: dict) -> Intent:
    return Intent(action="destroy", resource_id=resource["id"], pattern=resource["pattern"])


def destroy(app_id: str, caller: Caller, key: str) -> tuple[dict, str | None]:
    """Start the ordered teardown: accept the router's destroy now (when it exists), and let the
    teardown workflow accept the replicas' destroys once the router is gone. Needs change
    rights, not just visibility. Returns (app, the router destroy's operation or None).

    Idempotent by key across every teardown the app ever had: an older key returns that
    teardown's result. When a teardown exists but is unfinished, any other key accepts nothing
    and just returns the app, so the caller (which re-dispatches the teardown workflow) can
    recover a teardown whose workflow stopped."""
    app = policy.authorize(ledger.get_app(app_id), caller)
    ops = load(app)
    require_change(app, ops, caller)
    material = {"app_id": app_id, "action": "destroy"}
    teardown = app.get("teardown")
    seen = [*(app.get("teardown_history") or []), *([teardown] if teardown else [])]
    if entry := known_request(seen, caller.id, key, material):
        return app, entry.get("router")
    state = derive_state(app, ops)
    if teardown and state not in ("destroyed", "failed"):
        return app, teardown.get("router")
    busy = [i for i, op in ops.items() if op["state"] not in ("succeeded", "failed")]
    if state not in ("ready", "failed") or busy:
        raise OperationError(
            409,
            f"app is {state}; teardown needs a ready or failed app with nothing in flight",
            "app_destroy_unavailable",
            next_action=DISCARD
            if state == "failed" and planned_ids(app, ops)
            else NEXT_ACTION.get(state, "poll"),
        )
    policy.check_guardrails(
        {
            "business_unit": app.get("business_unit"),
            "environment": app.get("environment"),
            "action": "destroy",
        }
    )
    router = None
    resource = ledger.resource(app["router_resource_id"]) if app.get("router_resource_id") else None
    if resource and resource.get("state") == "ready":
        intent = destroy_intent(resource)
        resolved, budget = policy.validate(intent, caller)
        router = (canonical_intent(intent), resolved, budget)
    return ledger.accept_teardown(caller, app_id, key, material, router, app["updated_at"])


def discard(app_id: str, caller: Caller) -> tuple[dict, list[str]]:
    """Discard every plan a failed app still holds (replicas or destroys that were planned but
    will never be approved), releasing their budget reservations. Audited; idempotent."""
    app = policy.authorize(ledger.get_app(app_id), caller)
    ops = load(app)
    require_change(app, ops, caller)
    state = derive_state(app, ops)
    if state != "failed":
        raise OperationError(
            409,
            f"app is {state}; only a failed app's plans are discarded together",
            "app_discard_unavailable",
            next_action=NEXT_ACTION.get(state, "poll"),
        )
    planned = planned_ids(app, ops)
    if planned:
        ledger.record_app_event(app_id, caller.id, "app.discard", "accepted")
        for operation_id in planned:
            ledger.discard(operation_id, caller.id)
    return app, planned


def accept_teardown_replicas(app: dict) -> dict | None:
    """Accept destroys for every replica that is still deployed, as the requester would have
    through `POST /operations`; all or none. Raises on refusal. Idempotent."""
    teardown = app["teardown"]
    caller = Caller(teardown["actor"], frozenset(teardown["caller_groups"]))
    ensure_access(app, caller)
    items = []
    for member in app["members"]:
        resource = ledger.resource(member["resource_id"])
        if resource and resource.get("state") == "ready":
            intent = destroy_intent(resource)
            resolved, budget = policy.validate(intent, caller)
            items.append((member["index"], canonical_intent(intent), resolved, budget))
    return ledger.accept_teardown_replicas(app["id"], items)
