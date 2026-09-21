import uuid
from datetime import datetime

from azure.core.exceptions import AzureError
from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from temporalio.client import Client

from app import audit, budgets, catalog, db, logs, placement, schema, tenants
from app.auth import require_caller
from app.models import DeploymentCreate, DeploymentOut, DeploymentUpdate, State
from app.settings import settings
from app.tenants import Caller, TenancyError
from app.workflows import DeployWorkflow, DestroyWorkflow

app = FastAPI(title="forgeapi", version="0.1.0")


class TemporalDispatcher:
    """Starts one workflow per deployment. Connects lazily so the API starts without Temporal."""

    def __init__(self) -> None:
        self._client: Client | None = None

    async def __call__(self, deployment_id: str, action: str = "deploy") -> None:
        if self._client is None:
            self._client = await Client.connect(
                settings.temporal_address, namespace=settings.temporal_namespace
            )
        workflow = DestroyWorkflow.run if action == "destroy" else DeployWorkflow.run
        # A deployment can be retried and later destroyed, so each run gets its own workflow ID.
        run_id = f"{deployment_id}-{action}-{uuid.uuid4().hex[:8]}"
        await self._client.start_workflow(
            workflow, deployment_id, id=run_id, task_queue=settings.task_queue
        )


_dispatcher = TemporalDispatcher()


def get_dispatcher() -> TemporalDispatcher:
    return _dispatcher


@app.exception_handler(AzureError)
async def store_unavailable(_request, _exc):
    # Hosted: e.g. a role assignment that has not propagated yet. Details stay in the server log.
    return JSONResponse(
        {"detail": "deployment records are temporarily unavailable; try again shortly"},
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@app.exception_handler(TenancyError)
async def tenancy_refused(_request, exc: TenancyError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _resolve(name: str, version: str | None) -> catalog.Resolved:
    try:
        return catalog.resolve(name, version)
    except catalog.UnknownPattern:
        known = sorted(catalog.load())
        raise HTTPException(404, f"unknown pattern; choose one of {known}") from None
    except catalog.UnknownVersion as err:
        raise HTTPException(422, str(err)) from None
    except catalog.CatalogError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err)) from None


class _Audited:
    """Records a refusal when the wrapped request is refused; `accepted()` records the go-ahead.
    The handler fills `ctx` in as it learns more (pattern version, placement, safe inputs)."""

    def __init__(self, action: str, caller: Caller, **ctx):
        self.action, self.caller, self.ctx = action, caller, {"detail": {}, **ctx}

    def __enter__(self):
        return self

    def __exit__(self, _type, exc, _tb):
        if isinstance(exc, (HTTPException, TenancyError)):
            status_code = exc.status_code if isinstance(exc, HTTPException) else exc.status
            reason = exc.detail if isinstance(exc, HTTPException) else exc.message
            detail = {**self.ctx["detail"], "reason": reason}
            fields = {k: v for k, v in self.ctx.items() if k != "detail"}
            audit.record_quietly(
                self.action, "refused", self.caller.id, status=status_code, detail=detail, **fields
            )
        return False

    def note(self, **detail) -> None:
        self.ctx["detail"] |= detail

    def placed(self, deployment) -> None:
        self.ctx |= {
            "deployment_id": deployment.id, "pattern": deployment.pattern,
            "version": deployment.version, "business_unit": deployment.business_unit,
            "environment": deployment.environment,
        }  # fmt: skip

    def accepted(self) -> None:
        """No audit, no action: raises if the event cannot be stored."""
        fields = {k: v for k, v in self.ctx.items() if k != "detail"}
        try:
            audit.record(
                self.action, "accepted", self.caller.id, detail=self.ctx["detail"], **fields
            )
        except Exception:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "the audit trail is unavailable, so the request was not carried out",
            ) from None


def _variables(resolved: catalog.Resolved) -> list[dict]:
    try:
        return catalog.variables(resolved)
    except catalog.CatalogError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err)) from None


def _config(resolved: catalog.Resolved) -> dict | None:
    try:
        return catalog.config(resolved)
    except catalog.CatalogError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err)) from None


def _deployable(caller: Caller, unit: tenants.BusinessUnit) -> list[str]:
    return [name for name, env in unit.environments.items() if unit.can_deploy(caller, env)]


@app.get("/me")
def me(caller: Caller = Depends(require_caller)):
    """What this caller can do: their business units, environments, regions and patterns."""
    if not tenants.enabled():
        return {"id": caller.id, "business_units": None, "patterns": sorted(catalog.load())}
    return {
        "id": caller.id,
        "business_units": [
            {
                "name": unit.name,
                "environments": _deployable(caller, unit),
                "budgets": {
                    env: budgets.usage(unit, unit.environments[env])
                    for env in _deployable(caller, unit)
                },
                "regions": {"allowed": list(unit.regions_allowed), "default": unit.region_default},
                "patterns": sorted(unit.patterns & set(catalog.load())),
            }
            for unit in tenants.units_for(caller)
        ],
    }


@app.get("/patterns")
def list_patterns(caller: Caller = Depends(require_caller)):
    available = None
    if tenants.enabled():
        available = {}
        for unit in tenants.units_for(caller):
            for name in unit.patterns:
                available.setdefault(name, []).append(unit.name)
    return [
        {
            "name": p.name,
            "repo": p.repo,
            **({"business_units": sorted(available[p.name])} if available is not None else {}),
            "links": {"self": f"/patterns/{p.name}"},
        }
        for p in catalog.load().values()
        if available is None or p.name in available
    ]


@app.get("/patterns/{name}")
def get_pattern(
    name: str,
    version: str | None = None,
    business_unit: str | None = None,
    environment: str | None = None,
    caller: Caller = Depends(require_caller),
):
    """Everything a caller needs to use a pattern, read from the pattern repo at that version.
    With business units configured, inputs are shown as this caller will see them."""
    resolved = _resolve(name, version)
    variables = _variables(resolved)
    config = _config(resolved)
    shown_for = None
    example_extra: dict = {}
    if tenants.enabled():
        unit = tenants.select_unit(caller, business_unit)
        environment = environment or next(iter(_deployable(caller, unit)), None)
        where = tenants.place(caller, unit, environment, name)
        variables, _ = placement.shape(variables, where, config, None, require_size=False)
        sizes = {env: list(placement.sizes_for(config, env)) for env in _deployable(caller, unit)}
        shown_for = {
            "business_unit": unit.name,
            "environment": environment,
            "environments": _deployable(caller, unit),
            "sizes": sizes if (config or {}).get("sizing") else None,
        }
        offered = sizes.get(environment) or [None]
        shown_for["estimated_monthly_cost"] = {
            (size or "default"): budgets.estimated_cost(config, environment, size)
            for size in offered
        }
        shown_for["budget"] = budgets.usage(unit, where.environment)
        example_extra = {"environment": environment}
        if shown_for["sizes"] and sizes.get(environment):
            example_extra["size"] = sizes[environment][0]
    suffix = f"?version={resolved.version}" if resolved.version else ""
    return {
        "name": name,
        "repo": resolved.pattern.repo,
        "version": resolved.version,
        "commit": resolved.commit,
        "default_version": resolved.pattern.default_version or "latest",
        "versions": list(catalog.versions(resolved.pattern)),
        "about": schema.about(config),
        "placement": shown_for,
        "inputs": [schema.describe(v) for v in variables],
        "example": {**schema.example(name, resolved.version, variables), **example_extra},
        "links": {"schema": f"/patterns/{name}/schema{suffix}", "deploy": "/deployments"},
    }


@app.get("/patterns/{name}/schema")
def get_pattern_schema(
    name: str,
    version: str | None = None,
    business_unit: str | None = None,
    environment: str | None = None,
    caller: Caller = Depends(require_caller),
):
    """JSON Schema for `inputs`, for form builders, portals and client-side validation."""
    resolved = _resolve(name, version)
    variables = _variables(resolved)
    if tenants.enabled():
        unit = tenants.select_unit(caller, business_unit)
        environment = environment or next(iter(_deployable(caller, unit)), None)
        where = tenants.place(caller, unit, environment, name)
        variables, _ = placement.shape(
            variables, where, _config(resolved), None, require_size=False
        )
    return schema.json_schema(name, variables)


def _plan_request(
    caller: Caller,
    pattern: str,
    resolved: catalog.Resolved,
    inputs: dict,
    business_unit: str | None,
    environment: str | None,
    size: str | None,
    replacing: float = 0.0,
) -> dict:
    """Validate a request and work out its placement. Returns the record's placement fields
    (plus `budget`, for reporting only). `replacing`: this deployment's own current cost."""
    variables = _variables(resolved)
    if not tenants.enabled():
        if business_unit or environment or size:
            raise HTTPException(
                422, "business_unit, environment and size need business units to be configured"
            )
        where, injected = None, None
    else:
        unit = tenants.select_unit(caller, business_unit)
        where = tenants.place(caller, unit, environment, pattern)
        config = _config(resolved)
        variables, injected = placement.shape(variables, where, config, size)
    problems = schema.errors(pattern, variables, inputs)
    if problems:
        raise HTTPException(422, problems)
    if where is None:
        return {}
    declares_location = any(v["name"] == "location" for v in variables)
    if declares_location and "location" not in inputs and where.unit.region_default:
        # The business unit's default region beats the pattern's own default.
        injected = {**injected, "location": where.unit.region_default}
    cost = budgets.estimated_cost(config, where.environment.name, size)
    budget = budgets.check(where.unit, where.environment, pattern, cost, replacing)
    return {
        "estimated_monthly_cost": cost,
        "budget": budget,
        "business_unit": where.unit.name,
        "environment": where.environment.name,
        "subscription_id": where.environment.subscription_id,
        "size": size,
        "injected": injected,
    }


@app.post("/deployments", status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    body: DeploymentCreate,
    response: Response,
    dry_run: bool = False,
    dispatch=Depends(get_dispatcher),
    caller: Caller = Depends(require_caller),
):
    """Deploy a pattern. `?dry_run=true` checks the request and creates nothing."""
    if dry_run:  # changes nothing, so it is not part of the audit trail
        resolved = _resolve(body.pattern, body.version)
        placed = _plan_request(
            caller, body.pattern, resolved, body.inputs,
            body.business_unit, body.environment, body.size,
        )  # fmt: skip
        response.status_code = status.HTTP_200_OK
        return {
            "valid": True,
            "pattern": body.pattern,
            "version": resolved.version,
            "commit": resolved.commit,
            "inputs": body.inputs,
            # The subscription is deliberately not shown: callers never need it.
            **{k: v for k, v in placed.items() if k != "subscription_id"},
        }

    with _Audited(
        "deployment.create", caller, pattern=body.pattern, environment=body.environment,
        business_unit=body.business_unit or _only_unit(caller),
    ) as trail:  # fmt: skip
        trail.note(size=body.size, inputs=audit.safe_inputs(body.inputs, None))
        resolved = _resolve(body.pattern, body.version)
        trail.ctx["version"] = resolved.version
        trail.note(inputs=audit.safe_inputs(body.inputs, _variables(resolved)))
        placed = _plan_request(
            caller, body.pattern, resolved, body.inputs,
            body.business_unit, body.environment, body.size,
        )  # fmt: skip
        placed.pop("budget", None)
        deployment = db.create(
            body.pattern, body.inputs, resolved.version, resolved.commit,
            requested_by=caller.id, **placed,
        )  # fmt: skip
        trail.placed(deployment)
        trail.note(
            commit=resolved.commit, injected=deployment.injected,
            estimated_monthly_cost=deployment.estimated_monthly_cost,
        )  # fmt: skip
        try:
            trail.accepted()
        except HTTPException:
            db.update(deployment.id, State.failed, error="audit trail unavailable; not started")
            raise
        await _start(dispatch, deployment, "deploy")
        return DeploymentOut.of(deployment)


def _only_unit(caller: Caller) -> str | None:
    """The caller's business unit when they have exactly one: lets a refusal be filed under it."""
    if not tenants.enabled():
        return None
    mine = tenants.units_for(caller)
    return mine[0].name if len(mine) == 1 else None


async def _start(dispatch, deployment, action: str) -> None:
    try:
        await dispatch(deployment.id, action)
    except Exception:
        db.update(deployment.id, State.failed, error="could not reach Temporal to start the job")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"job engine unavailable; {deployment.id} failed"
        ) from None


def _load(deployment_id: str, caller: Caller, *, change: bool = False):
    """The deployment, if this caller may see it (and, for `change`, may deploy to its
    environment). Other business units' deployments look like they do not exist."""
    deployment = db.get(deployment_id)
    if deployment is None:
        raise HTTPException(404, "deployment not found")
    if not tenants.enabled():
        return deployment

    def denied(code: int) -> None:
        # Filed under the owning business unit, so they can see who tried.
        audit.record_quietly(
            "deployment.access", "refused", caller.id, status=code, deployment_id=deployment.id,
            pattern=deployment.pattern, business_unit=deployment.business_unit,
            environment=deployment.environment, detail={"wanted_to_change": change},
        )  # fmt: skip

    unit = tenants.load().get(deployment.business_unit or "")
    if unit is None or not unit.includes(caller):
        denied(404)
        raise HTTPException(404, "deployment not found")
    if change:
        env = unit.environments.get(deployment.environment or "")
        if env is None or not unit.can_deploy(caller, env):
            denied(403)
            raise HTTPException(
                403, f"you may not change deployments in {unit.name}/{deployment.environment}"
            )
    return deployment


@app.get("/deployments", response_model=list[DeploymentOut])
def list_deployments(caller: Caller = Depends(require_caller)):
    """Deployments of the caller's business units, newest first."""
    units = [u.name for u in tenants.units_for(caller)] if tenants.enabled() else None
    return [DeploymentOut.of(d) for d in db.list_for(units)]


@app.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(deployment_id: str, caller: Caller = Depends(require_caller)):
    return DeploymentOut.of(_load(deployment_id, caller))


@app.get("/deployments/{deployment_id}/logs", response_class=PlainTextResponse)
def get_logs(deployment_id: str, caller: Caller = Depends(require_caller)):
    _load(deployment_id, caller)
    return logs.read(deployment_id)


def _respec(
    caller: Caller, deployment, version: str | None, inputs: dict, size: str | None,
    *, dry: bool = False,
):  # fmt: skip
    """Re-validate against the current pattern version and the current mapping, then store
    (unless `dry`, which only validates)."""
    resolved = _resolve(deployment.pattern, version)
    placed = _plan_request(
        caller, deployment.pattern, resolved, inputs,
        deployment.business_unit, deployment.environment, size,
        replacing=deployment.estimated_monthly_cost or 0.0,
    )  # fmt: skip
    if placed and placed["subscription_id"] != deployment.subscription_id:
        raise HTTPException(
            409,
            f"{deployment.business_unit}/{deployment.environment} now maps to a different "
            "subscription; an existing deployment cannot move",
        )
    if dry:
        return
    db.respec(
        deployment.id, inputs, resolved.version, resolved.commit, size,
        placed.get("injected"), placed.get("estimated_monthly_cost"),
    )  # fmt: skip


@app.post(
    "/deployments/{deployment_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
)
async def retry_deployment(
    deployment_id: str,
    dispatch=Depends(get_dispatcher),
    caller: Caller = Depends(require_caller),
):
    """Re-run plan and apply for a failed deployment: same pattern commit, inputs and state, so
    Terraform finishes what is missing instead of starting over."""
    deployment = _load(deployment_id, caller, change=True)
    with _Audited("deployment.retry", caller) as trail:
        trail.placed(deployment)
        if deployment.state != State.failed:
            detail = f"only a failed deployment can be retried; this one is {deployment.state}"
            raise HTTPException(409, detail)
        if tenants.enabled():  # pick up corrections to the mapping (subnet, cost centre, ...)
            _respec(caller, deployment, deployment.version, deployment.inputs, deployment.size)
        trail.note(injected=db.get(deployment_id).injected)
        trail.accepted()
        db.update(deployment_id, State.accepted)
        await _start(dispatch, deployment, "deploy")
        return DeploymentOut.of(_load(deployment_id, caller))


@app.put(
    "/deployments/{deployment_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
)
async def update_deployment(
    deployment_id: str,
    body: DeploymentUpdate,
    dispatch=Depends(get_dispatcher),
    caller: Caller = Depends(require_caller),
):
    """Change a deployment's inputs, size and/or pattern version and apply the difference against
    its existing state. `inputs` replaces the whole set; omit it to keep the current inputs. Omit
    `version` or `size` to keep the current one. Business unit and environment never change."""
    deployment = _load(deployment_id, caller, change=True)
    with _Audited("deployment.update", caller) as trail:
        trail.placed(deployment)
        inputs = deployment.inputs if body.inputs is None else body.inputs
        version, size = body.version or deployment.version, body.size or deployment.size
        trail.note(
            from_version=deployment.version, to_version=version,
            from_size=deployment.size, to_size=size, inputs_changed=inputs != deployment.inputs,
            inputs=audit.safe_inputs(inputs, None),
        )  # fmt: skip
        if deployment.state not in (State.succeeded, State.failed):
            raise HTTPException(409, f"cannot update a deployment that is {deployment.state}")
        resolved = _resolve(deployment.pattern, version)
        trail.note(inputs=audit.safe_inputs(inputs, _variables(resolved)))
        # Record the go-ahead before anything changes; _respec validates and may still refuse.
        _respec(caller, deployment, version, inputs, size, dry=True)
        trail.accepted()
        _respec(caller, deployment, version, inputs, size)
        db.update(deployment_id, State.accepted)
        await _start(dispatch, deployment, "deploy")
        return DeploymentOut.of(_load(deployment_id, caller))


@app.delete(
    "/deployments/{deployment_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
)
async def destroy_deployment(
    deployment_id: str,
    dispatch=Depends(get_dispatcher),
    caller: Caller = Depends(require_caller),
):
    """`terraform destroy` everything the deployment's state records. The record itself is kept."""
    deployment = _load(deployment_id, caller, change=True)
    with _Audited("deployment.destroy", caller) as trail:
        trail.placed(deployment)
        if deployment.state not in (State.succeeded, State.failed):
            raise HTTPException(409, f"cannot destroy a deployment that is {deployment.state}")
        trail.accepted()
        db.update(deployment_id, State.destroying)
        await _start(dispatch, deployment, "destroy")
        return DeploymentOut.of(_load(deployment_id, caller))


@app.get("/deployments/{deployment_id}/events", response_model=list[audit.Event])
def deployment_events(deployment_id: str, caller: Caller = Depends(require_caller)):
    """The audit trail of one deployment, newest first."""
    deployment = _load(deployment_id, caller)
    return audit.for_deployment(deployment_id, deployment.business_unit)


@app.get("/events", response_model=list[audit.Event])
def events(
    business_unit: str | None = None,
    since: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
    caller: Caller = Depends(require_caller),
):
    """Audit events, newest first: your business units', or every unit's for auditors."""
    if not tenants.enabled():
        return audit.query(None, since, limit)
    if tenants.is_auditor(caller):
        return audit.query([business_unit] if business_unit else None, since, limit)
    mine = [unit.name for unit in tenants.units_for(caller)]
    if business_unit is not None and business_unit not in mine:
        raise HTTPException(403, f"you are not a member of business unit {business_unit!r}")
    return audit.query([business_unit] if business_unit else mine, since, limit)
