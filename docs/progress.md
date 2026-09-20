# Progress

Plan and milestone definitions: [rewrite-plan.md](rewrite-plan.md). Go-era progress: [archive-go/progress.md](archive-go/progress.md).

## 2026-09-20 — Rewrite: M0–M5 built and run on the home lab

The engineer chose to build ahead on the home lab rather than gate each milestone on the work Mac. Nothing below has been run on the work machine.

**Changed:** Go implementation, compose stack, Makefile, CI and scripts removed on branch `fastapi-rewrite`; Go-era docs moved to `docs/archive-go/`. New Python app (~540 lines in `app/`):

| Milestone | Behavior |
| --- | --- |
| M0 | uv project, FastAPI, `GET /healthz` |
| M1 | `POST /deployments` (202), `GET /deployments/{id}`; SQLite; per-pattern input validation, unknown fields rejected |
| M2 | One Temporal workflow per deployment (`DeployWorkflow`: plan → apply, `mark_failed` on error). `python -m app.devserver` runs the dev server with no CLI install |
| M3 | Real Terraform per deployment workspace under `.local/data/deployments/<id>/`; saved plan then apply; outputs stored; `GET /deployments/{id}/logs` |
| M4 | `key-vault` pattern (unchanged `main.tf`); tenant/subscription/RG/location/identity are server settings, never caller input; 503 when unconfigured |
| M5 | `FORGEAPI_AUTH_MODE=entra`: RS256 signature via tenant JWKS, issuer, audience, expiry; 401 without echoing token or error. Default `none` |

**Evidence (home-lab Linux, Python 3.12, Terraform 1.15.9, temporalio 1.33.0):**

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 23 passed. Includes real dev-server + worker + Terraform success and failure runs, and `terraform validate` of both patterns |
| `uv run ruff check .` | PASS |
| Live three-process run, `local-file` | PASS: `accepted → planning → succeeded`; Terraform-written file content matched; Temporal UI 200 on :8233 |
| Live three-process run, `key-vault` | PASS: `dep_8746b23034224f578f3673faef382770` `accepted → planning → applying → succeeded` in ~27 s; created `kv-forgeapi-py-7f75` in `forgeapitestRG01` |
| Independent readback `az keyvault show -n kv-forgeapi-py-7f75` (human CLI identity, not the executor) | PASS: `Succeeded`, standard SKU, RBAC on, public network access `Disabled`, expected tags |
| Terraform log scanned for certificate/key material | PASS: none |

**Identity:** reused the existing dedicated app registration `ForgeAPI Lab Terraform` (Key Vault Contributor on the one RG only). Engineer-approved new seven-day certificate appended; **expires 2026-09-27 20:56 UTC**. Private key generated locally, only in `.local/executor/client.pfx` (0600, gitignored). The expired 2026-09-07 certificate entry and `.local/lab-executor/` evidence were left intact.

**Live resources now in `forgeapitestRG01`:** `kv-forgeapi-0907-a8c2` (Go spike, state under `.local/deployments/`), `kv-forgeapi-py-7f75` (this run, state under `.local/data/deployments/dep_8746…/work/`), `uami-forgeapi-terraform-01`. Both vaults have `prevent_destroy`, are empty and cost nothing. There is no delete endpoint; removal is a manual, separately approved step.

**Not verified / known limits:**

- Nothing run on the work Mac. M5 tested with a locally generated signing key only; no real Entra token has been validated by this code.
- Dev server history is in-memory: a deployment in flight when the dev server stops stays in its last state. API/worker restarts alone are survived by Temporal but this was not exercised.
- Activities run once (no retries) by design; a transient Azure error fails the deployment.
- No destroy/update, idempotency, cancellation, ownership/authorization, or tracing — all deliberately parked.

**Next task options (engineer to pick):** run M0–M3 on the work Mac; validate M5 with a real Entra token; or add `DELETE /deployments/{id}` (terraform destroy) so demos clean up after themselves.

## 2026-09-20 — Docker packaging

`Dockerfile` (uv Python 3.12 + Terraform 1.15.9 from the official image, runs as uid 1000) and `compose.yaml` (Temporal dev server, worker, API; loopback-only ports). `.local/data` is bind-mounted, so Docker and native runs share the database and Terraform state. The executor certificate is mounted read-only into the worker only.

| Check | Result |
| --- | --- |
| `docker compose up --build -d --wait` | PASS: all three services up, Temporal healthy |
| `local-file` deployment via `curl localhost:8000` | PASS: `succeeded`; file written on host as the host user |
| Earlier native deployment `dep_8746…` read through the containerized API | PASS: shared state visible |
| Certificate visible in worker, absent in API container | PASS |

**Not verified:** a `key-vault` deployment through the Docker stack (would create a third vault; not run). Certificate readability and settings inside the worker were checked; Azure sign-in from the container was not.

## 2026-09-20 — Patterns from git tags, remote state

**Engineer decisions:** patterns live only in their own GitHub repos (tags = versions); only composite, runnable root-module patterns are exposed; state in a dedicated storage account; executor gets Contributor + RBAC Administrator on the lab subscription.

**Changed:** `app/patterns.py` and `patterns/` removed. `app/catalog.py` + `patterns.yaml`: tags via `git ls-remote`, tag→commit pinned at acceptance, input schema parsed from the pattern's `variable` blocks at that commit (cache keyed by commit). Worker runs `terraform init -from-module=git::…?ref=<commit>`, then `init` with per-deployment azurerm backend config. `GET /patterns`, `GET /patterns/{name}`; `version` + `commit` on deployments; Terraform's own error text in `error`. Docker image has git; token passed at run time.

**Azure/GitHub changes made:**

- Storage account `stforgeapitf6c68` (forgeapitestRG01): shared-key off, no public blobs, TLS 1.2, versioning + 14-day soft delete; container `tfstate`.
- Executor SP roles added: Contributor and Role Based Access Control Administrator on the subscription; Storage Blob Data Contributor on the `tfstate` container only.
- `AzSkyLab/terraform-azurerm-resource-group`: added `pattern/` (naming + resource group, own provider/backend), tagged **v1.1.0**.

**Evidence:**

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 27. Real git repos with tags: newest-first versions, annotated tag → commit, per-version schema, moved tag still runs accepted commit, Terraform validation message surfaced, missing state settings fail clearly |
| Live `GET /patterns/key-vault` and `/resource-group` (private GitHub repos) | PASS natively and from the Docker API container |
| Live `resource-group` v1.1.0 deployment `dep_47d92300a5724c62a820eb68079e9493` | PASS: `succeeded`, commit `44c8607` recorded, created `rg-forgeapi-resource-group-dev` (centralus). ~3.5 min, mostly first-run azurerm provider registration |
| `az group show` (human identity) | PASS: Succeeded, pattern tags present |
| Remote state | PASS: log shows azurerm backend configured; `terraform state list` as the executor reads it back; no state file in the workspace |
| Token leakage | PASS: no token in compose logs; backend-config args not logged |

**Graph permissions (engineer-approved, done):** executor app has application permissions `Group.Create` and `User.Read.All`, admin-consented; both app-role assignments verified via Graph.

**Pending, engineer to run:** the first `key-vault` deployment. The assistant's attempt was blocked by the tool permission policy (IaC apply). Command is in the session notes / README curl form. There is still no destroy endpoint, so a partial failure leaves resources behind.

**Not verified:** `key-vault` deployment; any deployment through the Docker worker against Azure; the `csGIT34/...` module sources inside key-vault `pattern/` work only via GitHub's redirect.

**Live resources:** `rg-forgeapi-resource-group-dev` (remote state), `kv-forgeapi-py-7f75` (local state in `.local/data`), `kv-forgeapi-0907-a8c2` (Go spike), `stforgeapitf6c68`, `uami-forgeapi-terraform-01`.

**Next:** `DELETE /deployments/{id}` (terraform destroy from remote state); then key-vault once Graph consent exists; repoint key-vault `pattern/` sources to AzSkyLab and tag.

**Default version pin:** `default_version` in `patterns.yaml` sets the tag used when a request names none (explicit `version` still wins; a pin that does not exist is a 422). Shown as `default_version` on `GET /patterns/{name}`. Tests: 29 passing. Compose build race fixed (only `api` builds the shared image).

## 2026-09-20 — Input discovery

**Why:** callers could see names/types but not allowed values, what a pattern is for, or an example; bad values only failed minutes later in Terraform.

**Changed:** `app/schema.py`. `GET /patterns/{name}` now returns `about` (the pattern repo's `config.yaml` at that tag), per-input allowed values/ranges/regex with the author's `error_message`, and a generated `example`. `GET /patterns/{name}/schema` returns JSON Schema. Validation moved from generated Pydantic models to that schema (one source of truth); lifted rules are enforced at POST with the author's message, all problems reported together, submitted values never echoed. `POST /deployments?dry_run=true` validates only. Zero per-pattern code. Executor app Graph permissions granted (engineer-approved).

| Check | Result |
| --- | --- |
| `uv run pytest` | PASS: 44. Lifting tested on the exact condition strings from the real repos; `||`, ternaries, other-variable and invalid-regex rules are not lifted; operators inside string literals handled; an unliftable rule still fails in Terraform with its message |
| Live against private `key-vault` v1.1.3 (Docker API) | PASS: `about` from the repo's `config.yaml`; `environment`/`sku_name` allowed values, `tier` 1–4, `owners` minItems 1; example body generated |
| Live dry run with `environment=staging`, `owners=[]`, `tier=9` | PASS: one 422 listing all three with the pattern's own messages; nothing created |

**Finding for the pattern repo:** key-vault's `environment` description says "(dev, staging, prod)" but its rule allows `prototype, dev, tst, stg, prd`. The API now shows the real list; the description should be fixed in the pattern.

**Still pending:** first `key-vault` deployment (engineer to run); `DELETE /deployments/{id}`.

## 2026-09-20 — First key-vault composite run: partial failure (resolved below)

Pattern fix merged and tagged in the pattern repo (`AzSkyLab/terraform-azurerm-key-vault` PR #1, **v1.1.4**); the API served v1.1.4 as latest with no API change.

`dep_ad66998a7b284b40b942a9f158609a06` (key-vault v1.1.4, commit `4756f452`, **through the Docker worker**): `planning → applying → failed` after ~3.5 min.

- **Proved:** Docker worker → private GitHub fetch at pinned commit → azurerm remote state → Azure and Entra with the certificate identity.
- **Failure:** `azuread_group` created both groups, then Graph returned 403 `Authorization_RequestDenied` reading the groups' owners. `Group.Create` + `User.Read.All` is not enough for the azuread provider to read back group owners; it also needs `Group.Read.All` (or `Group.ReadWrite.All` instead of `Group.Create`). Terraform's error text reached the deployment's `error` field intact.
- **Left behind (all recorded in remote state `deployments/dep_ad66998a….tfstate`, verified with `terraform state list`):** `rg-forgeapi-keyvault-dev`, vault `kv-forgeapi-lab2d-d` (purge protection off), the executor's Secrets Officer role assignment, Entra groups `sg-forgeapi-dev-secrets-readers` and `sg-forgeapi-dev-secrets-admins`. Not created: the two group RBAC assignments.
- **No API path to recover:** there is no retry or destroy endpoint. State is intact, so either is possible once built.

**Next:** engineer decision on the extra Graph permission; build `DELETE /deployments/{id}` (destroy) and a retry so partial failures are recoverable through the API.

## 2026-09-20 — Retry and destroy; key-vault composite finished

**Changed:** `POST /deployments/{id}/retry` (failed only; same pinned commit, inputs and state, so Terraform finishes what is missing) and `DELETE /deployments/{id}` (succeeded/failed only; `DestroyWorkflow` runs `terraform destroy` from the deployment's state; record kept, states `destroying → destroyed`). A prepared workspace is reused; otherwise the pattern is re-fetched, which remote state makes safe on any worker. Each run gets its own Temporal workflow ID. Executor app now has Graph `Group.ReadWrite.All` (engineer instruction; `Group.Create` alone failed reading group owners with azuread 2.53.1).

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 48 / clean. Real end-to-end destroy (file removed, `Destroy complete`), real fail-then-retry, 409/404 guards |
| Retry of `dep_ad66998a…` through the Docker worker | PASS: `accepted → planning → applying → succeeded` in ~90 s; Terraform replaced the two tainted groups and added the two missing role assignments |
| Independent readback (human identity) | PASS: vault `kv-forgeapi-lab2d-d` Succeeded, RBAC on, purge protection off; three role assignments (executor Secrets Officer, readers group Secrets User, admins group Secrets Officer); exactly two `sg-forgeapi-dev-*` groups (no duplicates) |

**Incident worth remembering:** the first retry failed with "missing or corrupted provider plugins". A native worker left running on the host from an earlier session was polling the same Temporal queue as the Docker worker; one ran `plan`, the other `apply`, and provider-cache symlinks are absolute paths that differ between host and container. Run either the native stack or the Docker stack against one Temporal, never both.

**Live destroy (engineer-approved), through the Docker worker:**

| Check | Result |
| --- | --- |
| `DELETE` key-vault deployment `dep_ad66998a…` | PASS: `destroying → destroyed` in ~60 s; `Destroy complete! Resources: 7 destroyed` (3 role assignments, vault, 2 Entra groups, resource group) |
| `DELETE` resource-group deployment `dep_47d92300…` (created natively, destroyed from Docker via remote state) | PASS: `destroyed` in ~45 s |
| Independent readback (human identity) | PASS: both resource groups gone, zero `sg-forgeapi-dev-*` groups, vault not left soft-deleted |

**Live resources now:** `kv-forgeapi-py-7f75` (pre-catalog run, local state in `.local/data`), `kv-forgeapi-0907-a8c2` (Go spike), `stforgeapitf6c68` (state), `uami-forgeapi-terraform-01`, all in `forgeapitestRG01`.

**Not verified:** anything on the work Mac; M5 with a real Entra token; retry/destroy when the original workspace is gone (re-fetch path is covered only by the fresh-workspace deploy path).

**Next:** run on the work Mac with the work pattern repos; GitHub App token and managed identity when hosted on Container Apps.
