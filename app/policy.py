"""Shared catalog and placement rules for agent intents; execution never trusts agent claims."""

import math
import sqlite3
from dataclasses import asdict
from pathlib import Path

from fastapi import HTTPException

from app import budgets, catalog, cloud_targets, ledger, pattern_check, placement, schema, tenants
from app.contracts import Intent, OperationError
from app.settings import settings
from app.tenants import Caller


def legacy_committed(unit: str, environment: str) -> float:
    """Read existing legacy costs without creating/migrating the old deployment database."""
    path = settings.data_dir.resolve() / "forgeapi.db"
    if not path.exists():
        return 0.0
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(deployments)")}
        if not {"business_unit", "environment", "state", "estimated_monthly_cost"} <= columns:
            return 0.0  # schemas predating budget estimates have no costs to carry over
        rows = connection.execute(
            "SELECT estimated_monthly_cost FROM deployments "
            "WHERE business_unit=? AND environment=? AND state != 'destroyed'",
            (unit, environment),
        )
        return ledger.accounting_amount(sum(ledger.accounting_amount(row[0]) for row in rows))
    finally:
        connection.close()


def authorize(record: dict | None, caller: Caller, change: bool = False) -> dict:
    if record is None:
        raise HTTPException(404, "resource or operation not found")
    if not tenants.enabled():
        if record["actor"] != caller.id:
            raise HTTPException(404, "resource or operation not found")
        return record
    unit = tenants.load().get(record.get("business_unit"))
    if unit is None or not unit.includes(caller):
        raise HTTPException(404, "resource or operation not found")
    if change:
        where = tenants.place(
            caller, unit, record.get("environment"), record["pattern"], allow_archived=True
        )
        cloud = catalog.get(record["pattern"]).cloud
        target = cloud_targets.select(where, cloud)
        cloud_targets.unchanged(record, cloud, target, where.environment.subscription_id)
        _refuse_new_work_on_archived(unit, record)
    return record


def _refuse_new_work_on_archived(unit, record: dict) -> None:
    """An archived team still destroys, and plans accepted before the archive still execute; an
    operation accepted at or after the archive (a race with it) must not apply."""
    if not (unit.archived and tenants.db_source()) or not record.get("action"):
        return
    if record["action"] in ("destroy", "drift_check"):
        return
    from app import team_store  # lazy: team_store imports the ledger

    since = team_store.archived_since(unit.name)
    if since is not None and record.get("created_at", "") >= since:
        raise OperationError(
            409,
            f"business unit {unit.name} was archived after this was submitted; only a destroy "
            "can run",
            "team_archived",
            next_action="submit_destroy",
        )


def authorize_reconcile(record: dict | None, caller: Caller) -> dict:
    """Operators may resolve any business unit's uncertain operation. A non-operator who can
    see it gets 403; one who cannot gets the usual 404. Tenancy disabled: the creating actor."""
    if not tenants.enabled():
        return authorize(record, caller)
    if record is None:
        raise HTTPException(404, "resource or operation not found")
    if tenants.is_operator(caller):
        return record
    unit = tenants.load().get(record.get("business_unit"))
    if unit is None or not unit.includes(caller):
        raise HTTPException(404, "resource or operation not found")
    raise HTTPException(403, "operator role required")


def check_guardrails(op: dict) -> None:
    """Deny a plan that deletes or replaces a protected resource type, or a destroy where the
    environment forbids it. No-op when tenancy is disabled or the operation's business unit or
    environment is not configured (e.g. removed from the mapping since acceptance)."""
    if not tenants.enabled():
        return
    unit = tenants.load().get(op.get("business_unit"))
    env = unit.environments.get(op.get("environment")) if unit else None
    if env is None:
        return
    if op.get("action") == "destroy" and not env.allow_destroy:
        raise OperationError(
            403,
            f"destroy is not allowed in {unit.name}/{env.name}",
            "policy_denied",
            next_action="revise_intent",
        )
    if not env.protected_resource_types:
        return
    offending = sorted(
        {
            change["address"]
            for change in op.get("changes") or []
            if change.get("type") in env.protected_resource_types
            and "delete" in change.get("actions", [])
        }
    )
    if offending:
        raise OperationError(
            403,
            "plan denied by policy; protected resources would be removed: "
            + ", ".join(offending[:20]),
            "policy_denied",
            next_action="revise_intent",
        )


def available(caller: Caller) -> list[str]:
    names = set(catalog.load())
    if tenants.enabled():
        names &= set().union(*(u.patterns for u in tenants.units_for(caller)))
    return sorted(names)


def deployable_in(unit, env) -> list[str]:
    """The unit's patterns that this environment can actually place (its cloud is targeted)."""
    known = catalog.load()
    return sorted(
        n for n in unit.patterns & set(known) if cloud_targets.deployable(env, known[n].cloud)
    )


def deployable_patterns(caller: Caller, environment: str) -> set[str]:
    names: set[str] = set()
    for unit in tenants.units_for(caller):
        env = unit.environments.get(environment)
        if env is not None and unit.can_deploy(caller, env):
            names.update(deployable_in(unit, env))
    return names


def pattern_changes(name, older, newer, business_unit, environment, caller) -> dict:
    """Changelog between two tags of a pattern the caller may use. Defaults are never returned,
    only whether they changed; platform-injected inputs are hidden when an environment is named."""
    if name not in available(caller):
        raise HTTPException(404, "pattern not found; discover available patterns")
    pattern = catalog.get(name)
    try:
        raw = catalog.changes(pattern, older, newer)
    except catalog.UnknownVersion as error:
        raise HTTPException(422, str(error)) from None
    hidden: set[str] = set()
    if environment is not None and tenants.enabled():
        unit = tenants.select_unit(caller, business_unit)
        where = tenants.place(caller, unit, environment, name, allow_archived=True)
        target = cloud_targets.select(where, pattern.cloud)
        newest = catalog.Resolved(pattern, newer, raw["to_commit"])
        all_variables = raw["to_variables"]
        visible, _ = placement.shape(
            [v for v in all_variables if v["name"] not in (target or {})],
            where,
            catalog.config(newest),
            None,
            require_size=False,
        )
        hidden = {v["name"] for v in all_variables} - {v["name"] for v in visible}
    old = {v["name"]: v for v in raw["from_variables"] if v["name"] not in hidden}
    new = {v["name"]: v for v in raw["to_variables"] if v["name"] not in hidden}
    added = []
    for variable_name in sorted(new.keys() - old.keys()):
        v = new[variable_name]
        item = {"name": variable_name, "required": v["required"], "type": v["type"]}
        if v["description"]:
            item["description"] = v["description"]
        added.append(item)
    changed = []
    for variable_name in sorted(new.keys() & old.keys()):
        before, after, fields = old[variable_name], new[variable_name], {}
        if before["required"] != after["required"]:
            fields["required"] = [before["required"], after["required"]]
        if before["type"] != after["type"]:
            fields["type"] = [before["type"], after["type"]]
        if (
            not before["required"]
            and not after["required"]
            and before["default"] != after["default"]
        ):
            fields["default_changed"] = True
        if fields:
            changed.append({"name": variable_name, "fields": fields})
    required_now = [a["name"] for a in added if a["required"]] + [
        c["name"] for c in changed if c["fields"].get("required") == [False, True]
    ]
    return {
        "name": name,
        "from": older,
        "to": newer,
        "from_commit": raw["from_commit"],
        "to_commit": raw["to_commit"],
        "commits": raw["commits"],
        "inputs": {"added": added, "removed": sorted(old.keys() - new.keys()), "changed": changed},
        "new_required_inputs": sorted(required_now),
    }


_check_cache: dict[tuple, list] = {}
_default_key: dict[str, tuple] = {}  # pattern -> _check_cache key of its default (pinned) version
_CHECK_CACHE_MAX = 256


def check_pattern(name: str, version: str | None, caller: Caller) -> dict:
    """Static contract check of a pattern the caller may use, at its pinned commit. A commit
    never changes, so the findings are cached per (pattern, commit, path, cloud); local patterns
    (no commit) are re-checked. Never runs Terraform."""
    resolved = resolve(name, version, caller)
    pattern = resolved.pattern
    key = (name, resolved.commit, pattern.path, pattern.cloud)
    findings = _check_cache.get(key) if resolved.commit else None
    if findings is None:
        try:
            root = catalog._checkout(resolved)
        except catalog.CatalogError:
            raise HTTPException(502, "pattern repository unavailable") from None
        up = None
        if pattern.path and not pattern.local:
            up = root.parents[len(Path(pattern.path).parts) - 1]
        found = pattern_check.check(root, cloud=pattern.cloud, config_fallback=up)
        findings = [asdict(f) for f in found]
        if resolved.commit:
            if len(_check_cache) >= _CHECK_CACHE_MAX:
                _check_cache.clear()
            _check_cache[key] = findings
    if resolved.commit and _is_default(name, version, pattern):
        _default_key[name] = key
    return {
        "name": name,
        "version": resolved.version,
        "commit": resolved.commit,
        "findings": findings,
        "errors": sum(f["level"] == "error" for f in findings),
        "warnings": sum(f["level"] == "warning" for f in findings),
    }


def _is_default(name: str, version: str | None, pattern) -> bool:
    """Whether `version` is what a request naming no version gets (the pinned one)."""
    if version is None or version == pattern.default_version:
        return True
    if pattern.default_version:
        return False
    try:
        return catalog.resolve(name, None).version == version
    except catalog.CatalogError:
        return False


def default_findings() -> dict[str, list]:
    """Cached contract findings of each pattern's default (pinned) version only, as a snapshot:
    what a caller checked last never changes them, and concurrent checks cannot break the read."""
    out = {}
    for name, key in list(_default_key.items()):
        findings = _check_cache.get(key)
        if findings is not None:
            out[name] = findings
    return out


def resolve(name: str, version: str | None, caller: Caller):
    if name not in available(caller):
        raise HTTPException(404, "pattern not found; discover available patterns")
    try:
        return catalog.resolve(name, version)
    except catalog.UnknownVersion:
        raise HTTPException(422, "version unavailable; describe the pattern") from None
    except catalog.CatalogError:
        raise HTTPException(502, "pattern repository unavailable") from None


def inspect_pattern(name, version, business_unit, environment, caller):
    resolved = resolve(name, version, caller)
    variables, config = catalog.variables(resolved), catalog.config(resolved)
    if tenants.enabled():
        unit = tenants.select_unit(caller, business_unit)
        where = tenants.place(caller, unit, environment, name, allow_archived=True)
        target = cloud_targets.select(where, resolved.pattern.cloud)
        variables, _ = cloud_targets.inject(variables, target)
        variables, _ = placement.shape(variables, where, config, None, require_size=False)
    about = schema.about(config)
    about.pop("sizing", None)
    return {
        "name": name,
        "cloud": resolved.pattern.cloud,
        "version": resolved.version,
        "commit": resolved.commit,
        "about": about,
        "input_schema": schema.json_schema(name, variables),
        "versions": list(catalog.versions(resolved.pattern)),
        "example": schema.example(name, resolved.version, variables),
        "sizes": list(placement.sizes_for(config, environment)) if environment else [],
    }


def resolve_refs(intent: Intent, caller: Caller, where: tuple) -> dict:
    """Snapshot referenced outputs; where is the (business_unit, environment) in tenant mode."""
    values = {}
    for name, ref in (intent.input_refs or {}).items():
        if name in intent.inputs:
            raise HTTPException(422, f"input {name} is set both in inputs and input_refs")
        source = authorize(ledger.resource(ref.resource_id), caller)
        with ledger.connect(write=False) as con:
            if problem := ledger.reference_error(con, ref.resource_id):
                raise OperationError(409, problem, "reference_not_ready")
        if tenants.enabled() and (source.get("business_unit"), source.get("environment")) != where:
            raise HTTPException(
                422, "references must stay within one business unit and environment"
            )
        if ref.output not in (source.get("outputs") or {}):
            raise HTTPException(422, f"output {ref.output} is not available for reference")
        values[name] = source["outputs"][ref.output]
    return values


def validate(intent: Intent, caller: Caller) -> tuple[dict, dict | None]:
    previous = None
    if intent.action == "destroy" and intent.labels is not None:
        raise HTTPException(422, "labels cannot be set on destroy")
    if intent.action == "destroy" and intent.input_refs is not None:
        raise HTTPException(422, "input_refs cannot be set on destroy")
    if intent.resource_id:
        previous = authorize(ledger.resource(intent.resource_id), caller, change=True)
        if previous.get("state") == "destroyed":
            raise OperationError(
                409, "resource was destroyed; submit a new resource intent", "resource_destroyed"
            )
        if intent.pattern != previous["pattern"]:
            raise OperationError(
                409, "a resource cannot change patterns", "pattern_change_not_supported"
            )
        if intent.business_unit not in (
            None,
            previous.get("business_unit"),
        ) or intent.environment not in (None, previous.get("environment")):
            raise OperationError(409, "a resource cannot change placement", "placement_changed")
        if intent.action == "destroy":
            check_guardrails(
                {
                    "business_unit": previous.get("business_unit"),
                    "environment": previous.get("environment"),
                    "action": "destroy",
                }
            )
            if ledger.referenced(intent.resource_id):
                raise OperationError(
                    409,
                    "resource is referenced by other resources; update or destroy them first",
                    "resource_referenced",
                )
            # The existing definition, including its pinned source, owns cleanup.
            return {**previous, "previous_operation_id": previous["operation_id"]}, None
        intent = intent.model_copy(
            update={
                "business_unit": previous.get("business_unit"),
                "environment": previous.get("environment"),
                "version": intent.version or previous.get("version"),
            }
        )
    resolved = resolve(intent.pattern, intent.version, caller)
    if intent.expected_commit and intent.expected_commit != resolved.commit:
        raise OperationError(
            409,
            "pattern revision changed; describe and validate again",
            "revision_moved",
            next_action="describe_and_validate_again",
        )
    variables, config = catalog.variables(resolved), catalog.config(resolved)
    result = {
        "pattern": intent.pattern,
        "version": resolved.version,
        "commit": resolved.commit,
        "source": resolved.terraform_source,
        "inputs": intent.inputs,
        "labels": (previous or {}).get("labels", {}) if intent.labels is None else intent.labels,
        "input_refs": {k: r.model_dump() for k, r in (intent.input_refs or {}).items()},
        "cloud": resolved.pattern.cloud,
        "previous_operation_id": previous["operation_id"] if previous else None,
    }
    budget = None
    if tenants.enabled():
        unit = tenants.select_unit(caller, intent.business_unit)
        where = tenants.place(caller, unit, intent.environment, intent.pattern)
        target = cloud_targets.select(where, resolved.pattern.cloud)
        variables, target_inputs = cloud_targets.inject(variables, target)
        variables, injected = placement.shape(variables, where, config, intent.size)
        injected = {**injected, **target_inputs}
        if (
            any(v["name"] == "location" for v in variables)
            and "location" not in intent.inputs
            and "location" not in (intent.input_refs or {})
            and unit.region_default
        ):
            injected = {**injected, "location": unit.region_default}
        try:
            cost = budgets.estimated_cost(config, where.environment.name, intent.size)
        except OverflowError:
            raise HTTPException(503, "invalid estimated cost configuration") from None
        if cost is not None and (not math.isfinite(cost) or cost < 0):
            raise HTTPException(503, "invalid estimated cost configuration")
        limit = where.environment.budget_monthly
        if limit is not None:
            try:
                valid_limit = (
                    isinstance(limit, (int, float))
                    and not isinstance(limit, bool)
                    and math.isfinite(limit)
                    and limit >= 0
                )
            except OverflowError:
                valid_limit = False
            if not valid_limit:
                raise HTTPException(503, "invalid monthly budget configuration")
            if cost is None:
                raise HTTPException(
                    403, "pattern must declare estimated costs for this environment"
                )
            budget = {
                "limit": limit,
                "legacy_committed": legacy_committed(unit.name, where.environment.name),
            }
        if tenants.db_source():
            result["_team_revision"] = unit.revision  # compared (then dropped) by ledger.accept
        result.update(
            business_unit=unit.name,
            environment=where.environment.name,
            subscription_id=(target or {}).get("subscription_id")
            if target
            else where.environment.subscription_id,
            cloud_target=target,
            size=intent.size,
            injected=injected,
            estimated_monthly_cost=cost,
        )
    elif intent.business_unit or intent.environment or intent.size:
        raise HTTPException(422, "placement fields require configured business units")
    where = (result["business_unit"], result["environment"]) if tenants.enabled() else ()
    result["inputs"] = {**intent.inputs, **resolve_refs(intent, caller, where)}
    if problems := schema.errors(intent.pattern, variables, result["inputs"]):
        raise HTTPException(422, problems)
    # Sensitive inputs would leak through Terraform plan/apply diagnostics. Patterns must use
    # vault references instead, just as sensitive output values stay outside the API.
    if any(v.get("sensitive") for v in variables):
        raise HTTPException(
            422, "patterns with sensitive inputs must accept vault references instead"
        )
    return result, budget
