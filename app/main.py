import uuid

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from temporalio.client import Client

from app import catalog, db, schema, terraform
from app.auth import require_caller
from app.models import DeploymentCreate, DeploymentOut, State
from app.settings import settings
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


@app.get("/patterns")
def list_patterns(_caller: str = Depends(require_caller)):
    return [
        {"name": p.name, "repo": p.repo, "links": {"self": f"/patterns/{p.name}"}}
        for p in catalog.load().values()
    ]


def _variables(resolved: catalog.Resolved) -> list[dict]:
    try:
        return catalog.variables(resolved)
    except catalog.CatalogError as err:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(err)) from None


@app.get("/patterns/{name}")
def get_pattern(name: str, version: str | None = None, _caller: str = Depends(require_caller)):
    """Everything a caller needs to use a pattern, read from the pattern repo at that version."""
    resolved = _resolve(name, version)
    variables = _variables(resolved)
    suffix = f"?version={resolved.version}" if resolved.version else ""
    return {
        "name": name,
        "repo": resolved.pattern.repo,
        "version": resolved.version,
        "commit": resolved.commit,
        "default_version": resolved.pattern.default_version or "latest",
        "versions": list(catalog.versions(resolved.pattern)),
        "about": schema.about(catalog.config(resolved)),
        "inputs": [schema.describe(v) for v in variables],
        "example": schema.example(name, resolved.version, variables),
        "links": {"schema": f"/patterns/{name}/schema{suffix}", "deploy": "/deployments"},
    }


@app.get("/patterns/{name}/schema")
def get_pattern_schema(
    name: str, version: str | None = None, _caller: str = Depends(require_caller)
):
    """JSON Schema for `inputs`, for form builders, portals and client-side validation."""
    resolved = _resolve(name, version)
    return schema.json_schema(name, _variables(resolved))


@app.post("/deployments", status_code=status.HTTP_202_ACCEPTED)
async def create_deployment(
    body: DeploymentCreate,
    response: Response,
    dry_run: bool = False,
    dispatch=Depends(get_dispatcher),
    _caller: str = Depends(require_caller),
):
    """Deploy a pattern. `?dry_run=true` checks the request and creates nothing."""
    resolved = _resolve(body.pattern, body.version)
    problems = schema.errors(body.pattern, _variables(resolved), body.inputs)
    if problems:
        raise HTTPException(422, problems)
    inputs = body.inputs
    if dry_run:
        response.status_code = status.HTTP_200_OK
        return {
            "valid": True,
            "pattern": body.pattern,
            "version": resolved.version,
            "commit": resolved.commit,
            "inputs": inputs,
        }

    deployment = db.create(body.pattern, inputs, resolved.version, resolved.commit)
    await _start(dispatch, deployment, "deploy")
    return DeploymentOut.of(deployment)


async def _start(dispatch, deployment, action: str) -> None:
    try:
        await dispatch(deployment.id, action)
    except Exception:
        db.update(deployment.id, State.failed, error="could not reach Temporal to start the job")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"job engine unavailable; {deployment.id} failed"
        ) from None


def _load(deployment_id: str):
    deployment = db.get(deployment_id)
    if deployment is None:
        raise HTTPException(404, "deployment not found")
    return deployment


@app.get("/deployments/{deployment_id}", response_model=DeploymentOut)
def get_deployment(deployment_id: str, _caller: str = Depends(require_caller)):
    return DeploymentOut.of(_load(deployment_id))


@app.get("/deployments/{deployment_id}/logs", response_class=PlainTextResponse)
def get_logs(deployment_id: str, _caller: str = Depends(require_caller)):
    _load(deployment_id)
    path = terraform.log_path(deployment_id)
    return path.read_text() if path.exists() else ""


@app.post(
    "/deployments/{deployment_id}/retry",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
)
async def retry_deployment(
    deployment_id: str, dispatch=Depends(get_dispatcher), _caller: str = Depends(require_caller)
):
    """Re-run plan and apply for a failed deployment: same pattern commit, inputs and state, so
    Terraform finishes what is missing instead of starting over."""
    deployment = _load(deployment_id)
    if deployment.state != State.failed:
        detail = f"only a failed deployment can be retried; this one is {deployment.state}"
        raise HTTPException(409, detail)
    db.update(deployment_id, State.accepted)
    await _start(dispatch, deployment, "deploy")
    return DeploymentOut.of(_load(deployment_id))


@app.delete(
    "/deployments/{deployment_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DeploymentOut,
)
async def destroy_deployment(
    deployment_id: str, dispatch=Depends(get_dispatcher), _caller: str = Depends(require_caller)
):
    """`terraform destroy` everything the deployment's state records. The record itself is kept."""
    deployment = _load(deployment_id)
    if deployment.state not in (State.succeeded, State.failed):
        raise HTTPException(409, f"cannot destroy a deployment that is {deployment.state}")
    db.update(deployment_id, State.destroying)
    await _start(dispatch, deployment, "destroy")
    return DeploymentOut.of(_load(deployment_id))
