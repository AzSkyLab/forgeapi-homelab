# FastAPI rewrite plan

**Branch:** `fastapi-rewrite`. **Status:** plan approved 2026-09-20; wipe executed; see [progress](progress.md) for the current milestone. `main` keeps the Go implementation and all its evidence.

## Why restart

Three days of setup in the work environment did not produce a running system. Two causes:

1. **Entra was mandatory for startup.** Nothing ran until tenant/app registration worked.
2. **Too much built before anything simple worked.** Retry classification, cancellation budgets, outbox, replay compatibility, OTel, grants and ETags landed before a second machine could run one request. 65 Go files, 7 compose services.

The language was not the main cause. Python/FastAPI helps only if scope is cut too. This plan cuts scope first.

## Rules for the rewrite

1. **One curl proves each milestone.** No milestone is done until its demo command works on the work machine, not just the home lab.
2. **Nothing is added until the previous milestone runs at work.**
3. **No auth, no Azure and no Docker are required to start.** They are added later as opt-in settings.
4. **Temporal stays, but boring.** One workflow, a few activities, each run once (a saved Terraform plan must not be applied twice). No custom retry classification, cancellation, Continue-As-New or replay-versioning work until a real failure demands it. Dev server is the single-binary `temporal server start-dev`, not a compose stack.
5. **Parked features need a written reason to come back.** See [Parked](#parked).
6. Size budget: M3 complete in roughly 500 lines of Python excluding tests.

## Target shape

```
pyproject.toml            uv-managed; fastapi, uvicorn, temporalio, pydantic-settings, pytest, httpx
app/
  main.py                 FastAPI app, routes
  models.py               Pydantic request/response + Deployment states
  db.py                   SQLite via stdlib sqlite3; one `deployments` table
  settings.py             env-driven settings (AUTH_MODE, TEMPORAL_ADDRESS, DATA_DIR)
  workflows.py            DeployWorkflow (deterministic, no I/O)
  activities.py           plan / apply / record-status activities
  terraform.py            subprocess wrapper: init, plan -out, apply, output -json
  worker.py               Temporal worker entrypoint
patterns/
  local-file/main.tf      hashicorp/local only; no cloud, no credentials
  key-vault/              existing main.tf + variables.tf, reused at M4
tests/
```

Run locally with three terminals: `temporal server start-dev`, `uv run python -m app.worker`, `uv run uvicorn app.main:app`.

## API (minimal)

```
POST /deployments            {"pattern": "local-file", "inputs": {...}}  -> 202 {id, state, links}
GET  /deployments/{id}       -> {id, pattern, state, outputs?, error?}
GET  /deployments/{id}/logs  -> text/plain terraform output            (M3)
GET  /healthz
```

States: `accepted -> planning -> applying -> succeeded | failed`. Nothing else yet.

## Milestones

| # | Deliverable | Demo that proves it |
| --- | --- | --- |
| **M0** | uv project, FastAPI app, `/healthz`, pytest + CI-free `make check` equivalent (`uv run pytest`) | `curl localhost:8000/healthz` |
| **M1** | `POST`/`GET /deployments` backed by SQLite. No worker; state stays `accepted`. Input validated against a per-pattern Pydantic model. | POST then GET returns the record |
| **M2** | Temporal dev server + worker. API starts `DeployWorkflow`; activities are stubs that sleep and update state. | POST, poll GET, watch `accepted -> succeeded`; workflow visible in Temporal UI on :8233 |
| **M3** | Real Terraform: per-deployment workspace under `DATA_DIR`, `init`/`plan -out`/`apply` of `patterns/local-file`, outputs and logs captured. | POST creates a real file via Terraform; GET shows outputs; `/logs` shows plan/apply text |
| **M4** | `key-vault` pattern against Azure. Terraform authenticates as the dedicated app registration with the approved short-lived certificate (see Decisions). | POST creates one empty vault in the approved RG; independent `az`/portal readback matches |
| **M5** | `AUTH_MODE=entra`: validate Entra bearer tokens (JWKS, issuer, audience). `AUTH_MODE=none` remains the local default, loopback-only. | 401 without token, 202 with token |

M0–M3 need no Azure, no Entra and no Docker. That is the POC. M4 and M5 are the first features added after it works.

## Parked

Deliberately not built until the POC runs at work and a concrete need appears:

- Idempotency keys, outbox, atomic accept+dispatch
- Cancellation, timeouts, custom retry classification, Continue-As-New, workflow versioning/replay tests
- Saved-plan human approval step
- Object-level authorization, grants, ownership
- OTel collector, tracing, audit tables
- PostgreSQL (SQLite until concurrency needs it), migrations framework
- ETags, cursors, long-polling, problem-details polish, Zalando lint
- Docker/compose packaging, ACA hosting
- The manager's compute-execution capability (Batch/VMSS provider). Note: that POC does not run Terraform per request; it will be a second job type behind the same API + Temporal skeleton, not a change to this one.

## Reference material kept from the Go work

- `docs/poc-intent-and-requirements.md` — manager's requirements; still the product intent.
- `docs/archive-go/design/openapi-deployments.yaml` — target contract to grow toward, not to implement now.
- `patterns/key-vault/*.tf` — reused unchanged at M4.
- `internal/keyvaultrunner/runner.go` on `main` — reference for Terraform subprocess handling.

## Wipe list

Executed 2026-09-20 on this branch (uncommitted). `README.md`/`AGENTS.md` originals were archived as `README-go.md`/`AGENTS-go.md`; `CONTRIBUTING.md` and `config/grants.example.json` were also removed.

- Remove: `cmd/`, `internal/`, `go.mod`, `go.sum`, `patterns/key-vault/pattern.go`, `Dockerfile`, `.dockerignore`, `compose.yaml`, `compose.test.yaml`, `Makefile`, `.github/workflows/core.yml`, `scripts/`, `config/collector.yaml`.
- Rewrite: `README.md`, `AGENTS.md`, `CONTRIBUTING.md`, `docs/handoff.md`, `.env.example`, `.gitignore`.
- Move to `docs/archive-go/`: `progress.md`, `demo.md`, `key-vault-demo.md`, `session-transfer.md`, `work-setup.md`, `terraform-identity.md`, `entra-local.md`, `TLDR.md`, `combined-build-prompt.md`, `adr/`, `design/`, `review/`.
- Untouched: untracked local state (`.local/`, `.env`, `config/*.local.json`). The live Key Vault and its Terraform state from the 2026-09-07 spike stay as they are.

## Decisions (2026-09-20)

1. **Language:** Python/FastAPI replaces Go. Go was not the main cause of the failed setup, but the rewrite is in Python; the scope cut is what makes it work.
2. **Work machine:** macOS. Temporal CLI and Terraform can be installed (`brew install temporal`, pinned Terraform 1.15.9).
3. **M4 Terraform identity:** a dedicated Entra app registration (service principal) in the work tenant, with a role scoped to the one approved resource group. Terraform reads it from `ARM_TENANT_ID` / `ARM_SUBSCRIPTION_ID` / `ARM_CLIENT_ID` plus one credential, supplied through untracked env only.
4. **First work demo includes Azure.** Demo target is M4. M0–M3 are still built and proven in order first; they are the path, not the demo.

5. **Credential (engineer-approved 2026-09-20):** short-lived certificate on the app registration, local development only (`ARM_CLIENT_CERTIFICATE_PATH`, untracked). No client secret. When the API moves to Azure Container Apps, Terraform switches to the app's system-assigned managed identity and the certificate is retired. Not needed for M0–M3.
6. **Build ahead on the home lab (2026-09-20):** the engineer lifted rules 1–2's work-machine gate to save work-environment effort. Milestones are built and proven here first; work-Mac verification is tracked in [progress](progress.md).
