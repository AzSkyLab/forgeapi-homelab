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

## 2026-09-20 — Hosting groundwork (branch `aca-hosting`, nothing created in Azure)

Plan: [hosting-plan.md](hosting-plan.md) — Container Apps, scale-to-zero, managed identity, Table Storage for records, GHCR for the image. Engineer note: work may host its Temporal workers on ACA too, so lab-only shortcuts are marked in the plan.

**Built (steps 1–2 of the plan):**

- `app/db.py` is now a three-function facade over a store; SQLite unchanged and default. `app/db_table.py` stores one entity per deployment in Azure Table Storage with Entra auth (`FORGEAPI_DB_BACKEND=table`).
- `app/azure_identity.py`: managed identity when `FORGEAPI_AZURE_USE_MANAGED_IDENTITY=true`, else the approved certificate, else the default chain. With it set, Terraform gets `ARM_USE_MSI=true` and the backend `use_msi=true`, and the certificate is not handed over.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 57 / clean |
| Store contract (round trip, missing record, outputs kept across later updates, error replaced) against SQLite **and** Table Storage | PASS. Table Storage = Azurite emulator in Docker, started per test session and removed after; visibly skipped if Docker is absent |
| Identity → Terraform env/backend mapping | PASS (unit level) |

**Not verified, cannot be off-Azure:** Table Storage against the real account with Entra auth; Terraform obtaining tokens from a Container Apps managed identity (`IDENTITY_ENDPOINT`, not the VM metadata address). That is the first thing to prove once hosted; fallback is a federated credential trusting the worker's identity.

**Next (needs the engineer):** create the GitHub App for pattern-repo reads (step 3); approve writing and applying the `terraform-pattern-forgeapi-host` pattern (step 4), the first step that creates hosted resources.

## 2026-09-20 — Host pattern written (not applied)

- New private repo `AzSkyLab/terraform-pattern-forgeapi-host`, tag **v0.1.0**: Consumption Container Apps environment (no Log Analytics), `api` (public HTTPS, 0–1 replicas), `worker` (no ingress, 0–1), optional lab `temporal` (internal TCP 7233; omitted when `temporal_address` is given, the work case). System-assigned identities; API → Storage Table Data Contributor only; worker → table, `tfstate` container, and `worker_roles` on `worker_scope`. Optional pattern-repo token via Key Vault reference, never through Terraform state or the API. Terraform ignores `min_replicas` on worker/temporal so the on/off switch is not fought.
- Registered as `forgeapi-host` in `patterns.yaml`. `.github/workflows/image.yml` publishes the image to GHCR on `v*` tags or manual run. `scripts/aca.sh up|down|status`.

| Check | Result |
| --- | --- |
| `terraform fmt` / `init -backend=false` / `validate` on the pattern | PASS |
| `GET /patterns/forgeapi-host` (Docker API, private repo) | PASS: v0.1.0, `about` and costs from its `config.yaml`, 7 required inputs, generated example |
| Dry run with `image=forgeapi:latest` | PASS: 422 with the pattern's own message; nothing created |
| `uv run pytest` | PASS: 57 |

**Not done / not verified:** no `terraform plan` or apply of the host pattern; image never built or pushed (workflow not yet on the default branch or run); `scripts/aca.sh` only syntax-checked; ACA managed-identity token acquisition by Terraform and internal TCP ingress for Temporal remain the two hosting unknowns; Graph permissions for the worker identity are a manual directory-admin step after apply.

**Next (engineer):** push `aca-hosting`, publish an image (tag or manual workflow run, then make the GHCR package public), then approve deploying `forgeapi-host`.

## 2026-09-20 — Image published

`aca-hosting` pushed; tag **v0.1.0** triggered `.github/workflows/image.yml` (run 35542407062, success). Published `ghcr.io/azskylab/forgeapi` with tags `v0.1.0`, `latest`, `sha-5ab4ae7…`. Manual dispatch is not available until the workflow is on the default branch.

**Open:** the package is **private** (org default); anonymous `docker manifest inspect` returns `unauthorized`. GitHub has no API for package visibility, so the engineer must set it to public once in the package settings before Container Apps can pull it without a credential.

## 2026-09-20 — Entra auth set up and verified with a real token

GHCR package made public by the engineer; anonymous `docker manifest inspect` of `ghcr.io/azskylab/forgeapi:v0.1.0` now succeeds.

App registration **ForgeAPI Lab API** (single tenant, no secrets, no certificates): application ID URI `api://<client-id>`, access token version 2, one delegated scope `access_as_user`, Azure CLI pre-authorized so `az account get-access-token --scope api://<client-id>/access_as_user` works without a consent prompt. With v2 tokens `aud` is the client ID, so `FORGEAPI_ENTRA_AUDIENCE` / the pattern's `entra_audience` is the **client ID**, not the URI. IDs are in the engineer's tenant, not in this public repo.

First real-token test of `FORGEAPI_AUTH_MODE=entra` (throwaway local API, no worker):

| Request | Result |
| --- | --- |
| `/healthz`, no token | 200 (open by design) |
| `/patterns`, no token / garbage token | 401 / 401 |
| `/patterns`, valid Entra token for a **different** audience (ARM) | 401 |
| `/patterns`, real token for this API (`ver 2.0`, correct `iss`, `scp access_as_user`) | 200 |
| API log scanned for token or validation detail | none |

Closes the long-standing "M5 tested with a local key only" gap. Any signed-in user in the tenant can obtain this token; per-user authorization is still parked.

Dry run of the final `forgeapi-host` request (with `entra_audience`) is valid. **Pending, engineer to send:** the real POST.
