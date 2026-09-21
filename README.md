# forgeapi

Minimal API that runs Terraform patterns as Temporal jobs. Python/FastAPI rewrite; the earlier Go implementation lives on `main` and its docs are in `docs/archive-go/`.

Plan, rules and milestones: [docs/rewrite-plan.md](docs/rewrite-plan.md). Current state and evidence: [docs/progress.md](docs/progress.md).

## Run with Docker (easiest)

```sh
docker compose up --build -d      # Temporal + worker + API; UI http://localhost:8233
curl localhost:8000/healthz
docker compose logs -f worker     # watch jobs
docker compose down               # stop; data in .local/data is kept
```

Then use the curl commands below. Run either this Docker stack or the native one, never both at once: two workers on one queue with different filesystem views break Terraform's provider cache. Only the worker container gets the Terraform certificate (`.local/executor`, read-only); the API container does not. Temporal history is in-memory and resets on `down`; deployments and Terraform state do not.

## Run natively

Needs [uv](https://docs.astral.sh/uv/) and the `terraform` binary. No Docker, Azure or Entra.

```sh
uv sync
uv run pytest                          # includes real Temporal + Terraform runs
```

Three terminals:

```sh
uv run python -m app.devserver         # Temporal dev server :7233, UI http://localhost:8233
uv run python -m app.worker
uv run uvicorn app.main:app --reload   # API :8000, docs http://localhost:8000/docs
```

`app.devserver` downloads the official Temporal dev-server binary on first use; `temporal server start-dev` works the same if you have the CLI.

```sh
curl -s -XPOST localhost:8000/deployments -H 'content-type: application/json' \
  -d '{"pattern":"local-file","inputs":{"filename":"hello.txt","content":"hi"}}'
curl -s localhost:8000/deployments/<id>          # accepted -> planning -> applying -> succeeded
curl -s localhost:8000/deployments/<id>/logs     # terraform output
```

## Patterns

No Terraform lives in this repo. Each pattern is a runnable root module (its own `provider` and `backend "azurerm" {}` blocks) in its own git repo, versioned by semver tags. `patterns.yaml` is the whole registration:

```yaml
patterns:
  key-vault:
    repo: github.com/AzSkyLab/terraform-azurerm-key-vault
    path: pattern            # omit when the root module is the repo root
    default_version: v1.1.3  # optional pin; omit to follow the newest tag
```

| Call | What it does |
| --- | --- |
| `GET /patterns` | catalog |
| `GET /patterns/{name}?version=v1.0.0` | everything needed to use it, read from the pattern repo at that tag: `about` (the repo's `config.yaml`: description, use cases, sizing, costs), `versions`, `inputs` (type, default, description, allowed values/ranges and the author's rule messages) and a ready-to-edit `example` request |
| `GET /patterns/{name}/schema` | JSON Schema for `inputs`, for portals, form builders and client-side validation |
| `POST /deployments?dry_run=true` | checks a request and creates nothing |
| `POST /deployments/{id}/retry` | re-runs a failed deployment against the same commit, inputs and state; Terraform finishes what is missing |
| `PUT /deployments/{id}` | change inputs and/or pattern version and apply the difference against the existing state (`inputs` replaces the whole set; omit to keep) |
| `DELETE /deployments/{id}` | `terraform destroy` from the deployment's state; the record is kept as `destroyed` |
| `POST /deployments {"pattern","version","inputs"}` | `version` optional (latest tag). The tag is resolved to a commit at acceptance; that commit is what runs, even if the tag later moves |

Inputs are checked at the API (422, all problems at once, never echoing the submitted value). `validation` blocks in the common shapes (`contains([...], var.x)`, `can(regex("...", var.x))`, numeric ranges, `length(var.x)` bounds, joined with `&&`) are lifted into the schema and answered with the author's own `error_message`. Any other rule is still enforced by Terraform at plan time and its message lands in the deployment's `error`.

**Pattern authoring convention:** `variable` descriptions, `validation` error messages and `config.yaml` are the user documentation. Write them for the person calling the API.

**Releasing a pattern change:** push a tag in the pattern repo. New deployments can use it immediately; nothing in this API is rebuilt or redeployed. Existing deployments stay on the commit they were created with.

**State:** patterns with `backend "azurerm" {}` store state as `deployments/<id>.tfstate` in the configured storage container (Entra auth, blob-lease locking, no keys). Workspaces under `.local/data/deployments/` are throwaway. `local-file` (in `examples/`) is an unversioned, no-cloud example with local state.

**Private repos:** natively git uses your own credential helper. For Docker: `FORGEAPI_GITHUB_TOKEN=$(gh auth token) docker compose up --build -d`.

## Business units: where things go and who may deploy

Optional (`FORGEAPI_TENANTS_PATH`). Callers say what they want; the platform decides where. A mapping ([tenants.example.yaml](tenants.example.yaml)) ties Entra groups to business units, and each business unit + environment to a subscription. It also supplies inputs callers must not set (business unit, cost centre, subnets), limits patterns and regions, and restricts who may deploy to which environment. T-shirt sizes come from the pattern's own `config.yaml` and lock the inputs they set. Deployments belong to their business unit; others cannot see them. Callers never see a subscription ID. Design and rules: [docs/tenancy.md](docs/tenancy.md).

```sh
curl -s localhost:8000/me                       # my business units, environments, patterns
curl -s -XPOST localhost:8000/deployments -H 'content-type: application/json' \
  -d '{"pattern":"key-vault","environment":"dev","size":"small","inputs":{"name":"kv1", ...}}'
```

## Hosting

**Deploying at work (existing ACA environment, MCP server, no local dev):** follow [docs/work-deployment.md](docs/work-deployment.md).

Plan for Azure Container Apps, free when idle: [docs/hosting-plan.md](docs/hosting-plan.md). Built so far: `FORGEAPI_DB_BACKEND=table` (Azure Table Storage, Entra auth) and `FORGEAPI_AZURE_USE_MANAGED_IDENTITY=true` (Terraform provider, state backend and Table Storage use the app's managed identity; no certificate).

## Auth

Default `FORGEAPI_AUTH_MODE=none` is for loopback development. `entra` validates bearer tokens (signature via tenant JWKS, issuer, audience, expiry) on every `/deployments` route.

## Layout

```
app/main.py        routes + Temporal dispatch      app/workflows.py   DeployWorkflow (no I/O)
app/catalog.py     git-tag catalog, reads variables
app/schema.py      rules -> JSON Schema, examples  app/activities.py  plan / apply / mark_failed
app/tenants.py     business-unit mapping
app/placement.py   injected inputs, sizes
app/db.py          records: SQLite or Table         app/terraform.py   CLI subprocess wrapper
app/auth.py        who is calling + groups         app/worker.py, app/devserver.py
```
