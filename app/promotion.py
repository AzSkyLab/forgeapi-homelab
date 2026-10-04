"""Environment promotion: the same pattern commit, deployed as a new resource elsewhere.

A promotion is an ordinary deploy intent built from a ready resource and submitted through
`policy.validate` and `ledger.accept`, so placement, injected inputs, allowed patterns, guardrails,
budget, idempotency and audit all apply unchanged. It never applies by itself."""

from fastapi import HTTPException

from app import apps, ledger, policy
from app.contracts import InputRef, Intent, OperationError, Promote, Upgrade
from app.tenants import Caller, TenancyError


def material(resource_id: str, body: Promote) -> dict:
    """The idempotency identity: the request, not the derived intent, so it cannot collide with
    a plain `POST /operations` body."""
    return {"action": "deploy", "promote_from": resource_id, **body.model_dump(exclude_none=True)}


def rebuild_intent(source: dict, refs: dict | None = None, **fields) -> Intent:
    """A deploy intent from a resource's stored definition: its pattern, its CALLER inputs (never
    the injected placement; values copied from references are dropped, so only inputs the caller
    typed remain), size, labels and the given `refs`. `fields` override or add intent fields;
    `inputs` is merged over the carried inputs and `labels` over the stored ones."""
    refs = refs or {}
    named = set(source.get("input_refs") or {}) | set(refs)
    carried = {k: v for k, v in (source.get("inputs") or {}).items() if k not in named}
    labels = {**(source.get("labels") or {}), **(fields.pop("labels", None) or {})}
    size = fields.pop("size", None) or source.get("size")
    inputs = {**carried, **(fields.pop("inputs", None) or {})}
    if len(labels) > 16:
        raise HTTPException(422, "labels exceed 16 entries")
    return Intent(
        pattern=source["pattern"],
        size=size,
        inputs=inputs,
        input_refs=refs or None,
        labels=labels,
        **fields,
    )


def intent(source: dict, body: Promote) -> Intent:
    referenced = set(source.get("input_refs") or {})
    if missing := sorted(referenced - set(body.input_refs or {}) - set(body.inputs)):
        raise OperationError(
            422,
            "inputs taken from references in the source environment cannot cross "
            "environments; supply input_refs or inputs for: " + ", ".join(missing),
            "promotion_needs_refs",
            next_action="supply_input_refs",
        )
    return rebuild_intent(
        source,
        body.input_refs,
        version=source.get("version"),
        expected_commit=source.get("commit"),
        business_unit=body.business_unit or source.get("business_unit"),
        environment=body.environment,
        size=body.size,
        inputs=body.inputs,
        labels={**(body.labels or {}), "promoted_from": source["id"]},
    )


def accept(resource_id: str, body: Promote, caller: Caller, key: str) -> dict:
    identity = material(resource_id, body)
    if existing := ledger.replay(caller.id, key, identity):
        return policy.authorize(existing, caller)
    source = policy.authorize(ledger.resource(resource_id), caller)
    if source.get("state") != "ready":
        raise OperationError(409, "only a ready resource can be promoted", "resource_not_ready")
    new = intent(source, body)
    if (new.business_unit, new.environment) == (
        source.get("business_unit"),
        source.get("environment"),
    ):
        raise HTTPException(422, "resource is already in that environment")
    try:
        resolved, budget = policy.validate(new, caller)
    except TenancyError as error:
        if body.size is None and source.get("size") and "is not offered" in str(error):
            raise OperationError(
                422,
                f"the source's size {source['size']!r} is not offered in the target "
                f"environment; set `size`. {error}",
                "promotion_needs_size",
                next_action="supply_size",
            ) from None
        raise
    resolved["promoted_from"] = resource_id
    return ledger.accept(caller.id, key, identity, resolved, budget)


def accept_upgrade(resource_id: str, body: Upgrade, caller: Caller, key: str) -> dict:
    """Update the same resource to another pattern version through the ordinary update path."""
    identity = {
        "action": "deploy",
        "resource_id": resource_id,
        "upgrade": True,
        **body.model_dump(exclude_none=True),
    }
    if existing := ledger.replay(caller.id, key, identity):
        return policy.authorize(existing, caller)
    apps.refuse_member_change(resource_id, caller)
    source = policy.authorize(ledger.resource(resource_id), caller)
    if source.get("state") != "ready":
        raise OperationError(409, "only a ready resource can be upgraded", "resource_not_ready")
    if body.version == source.get("version"):
        raise OperationError(
            409, "resource already runs that version", "upgrade_noop", next_action="done"
        )
    refs = {k: InputRef(**v) for k, v in (source.get("input_refs") or {}).items()}
    new = rebuild_intent(
        source,
        refs,
        resource_id=resource_id,
        version=body.version,
        expected_commit=body.expected_commit,
        inputs=body.inputs,
        labels=body.labels,
    )
    resolved, budget = policy.validate(new, caller)
    return ledger.accept(caller.id, key, identity, resolved, budget)
