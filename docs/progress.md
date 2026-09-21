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

## 2026-09-20 — forgeapi hosted on Container Apps (deployed through its own API)

`dep_a24111c44c6d49b39447762e7be82e73`, pattern `forgeapi-host` v0.1.0 (`3d1abc12`), engineer-approved.

- **First attempt failed in 35 s:** `MissingSubscriptionRegistration` for `Microsoft.App` (subscription had never used Container Apps). Only the resource group was created. Registered the provider (one-time, free), then `POST …/retry` finished the same deployment: `succeeded`. A second retry sent while it was already applying was correctly refused with 409.
- **Created:** `rg-forgeapi-forgeapi-host-dev`, environment `cae-forgeapi-host-dev` (Consumption, no Log Analytics), apps `ca-forgeapi-api-dev` (external), `ca-forgeapi-worker-dev` (no ingress), `ca-forgeapi-temporal-dev` (internal), all min 0 / max 1. Roles verified: API identity → Storage Table Data Contributor only; worker identity → table, `tfstate` blob container, Contributor + RBAC Administrator on the subscription.

| Check against the hosted API | Result |
| --- | --- |
| Public image pull from GHCR without a credential | PASS (app started) |
| `/healthz` from zero replicas | PASS: 200 in 0.29 s |
| `/patterns` without a token / with a real Entra token | PASS: 401 / 200 |
| Deployment lookup = Table Storage through the API's **managed identity** | PASS after propagation: first call 500 `AuthorizationPermissionMismatch` (role assignment seconds old), 404 on the next try ~1 min later; the identity created the `deployments` table itself |

**Proved:** `ManagedIdentityCredential` works inside Container Apps for the Python SDK. **Still unproven:** Terraform (`ARM_USE_MSI`) with the worker's identity, internal TCP to Temporal, `scripts/aca.sh`. Worker and Temporal are still at zero replicas; nothing can deploy from the hosted copy yet.

**Known gap:** the hosted API has no GitHub token, so `/patterns/{name}` for private repos will return 502 until the Key Vault token reference is set up.

**Pattern follow-up:** `forgeapi-host` should tolerate the role-propagation delay better (the API returned a 500 rather than a clear 503), and its README should list `Microsoft.App` registration as a prerequisite.

**Next:** Key Vault + read-only GitHub token reference; `scripts/aca.sh up`; deploy `resource-group` through the hosted API as the Terraform-managed-identity test; Graph permissions for the worker identity.

## 2026-09-20 — Hosted worker proven: update endpoint, federation, full hosted run

**Built:** `PUT /deployments/{id}` (new inputs and/or pattern version, applied against existing state; refused for local-state patterns on a version change); `examples/azure-identity-check` (creates nothing; proves Terraform sign-in + remote state without any repo access); Azure store errors → 503; Terraform sign-in through **workload identity federation** (`FORGEAPI_AZURE_FEDERATED_CLIENT_ID`); `scripts/aca.sh` starts Temporal before the worker. Images `v0.1.1`, `v0.1.2`. Pattern `forgeapi-host` **v0.2.0** (user-assigned worker identity, no deploy rights on it). Tests: 66 passing.

| Check (hosted unless noted) | Result |
| --- | --- |
| `PUT` image bump on the live host deployment (local API → Azure) | PASS: `0 to add, 4 to change, 0 to destroy` |
| `scripts/aca.sh up/status/down` against real apps | PASS |
| `local-file` through the hosted API | PASS: API → Table Storage → Temporal over internal TCP → worker → Terraform, `succeeded` in ~15 s |
| `azure-identity-check` with `ARM_USE_MSI` | **FAIL as feared:** `ManagedIdentityAuthorizer … 169.254.169.254:80: connection refused`. Terraform cannot read a Container Apps managed identity |
| `PUT` to pattern v0.2.0 + image v0.1.2 (version change in place) | PASS: `2 to add, 4 to change, 4 to destroy`; the 4 destroyed are the old worker identity's role assignments, including subscription Contributor and RBAC Administrator |
| Federated credential `forgeapi-worker-lab` on the Terraform app (subject = worker identity) | created (engineer asked for all steps) |
| `azure-identity-check` with federation | **PASS:** `signed_in_object_id` = the Terraform app's service principal, remote state written, no secret anywhere |
| `DELETE` of both hosted test deployments through the hosted API | PASS: `destroyed` |

**Security posture now:** API identity and worker identity can each touch only the deployments table. Deploy rights, state access and Graph permissions exist on exactly one principal (the Terraform app), reachable locally by the short-lived certificate and hosted by federation from one named identity.

**Created outside Terraform:** `Microsoft.App` provider registration; Key Vault `kv-forgeapi-host-38b9` in `forgeapitestRG01` (RBAC, empty) with Secrets Officer for the engineer, to hold a read-only pattern-repo token. Kept out of the host resource group on purpose so destroying the host does not trip over an unmanaged resource.

**Pending, engineer:** create a fine-grained read-only GitHub token and store it in the vault; then `PUT` the host deployment with `github_token_secret_id` + `github_token_key_vault_id`, `aca.sh up`, and deploy `resource-group` through the hosted API (first real pattern from a private repo, hosted). Worker and Temporal are currently **down** (free).

## 2026-09-20 — Last leg: private pattern deployed entirely from Azure

- Engineer stored a fine-grained, read-only, 30-day GitHub token in `kv-forgeapi-host-38b9` (`pattern-repo-token`); the assistant only ever listed the secret's metadata.
- First `PUT` with the Key Vault inputs **failed twice**: deadlock in pattern v0.2.0. The API's identity was system-assigned, so the Key Vault role depended on the app, and the app could not update without reading the secret. Fixed in **v0.2.1**: both identities user-assigned, roles granted first, 90 s propagation wait, apps last. `PUT {"version":"v0.2.1"}` from the failed state: `5 to add, 4 to change, 1 to destroy`, `succeeded`. Worker identity unchanged, so the federation trust still held.

| Check (hosted) | Result |
| --- | --- |
| `GET /patterns/{resource-group,key-vault,forgeapi-host}` | PASS: tags read from the private repos using the Key Vault-referenced token |
| `resource-group` v1.1.0 (`44c86077`) deployed through the hosted API | PASS: `succeeded` in ~65 s. Private repo + private module fetch, federated sign-in, remote state, all from Container Apps |
| `az group show` (human identity) | PASS: `rg-forgeapi-resource-group-dev` Succeeded with pattern tags |
| Token in hosted deployment logs | none |
| `DELETE` through the hosted API | PASS: `destroyed`; resource group gone |
| `aca.sh down` | worker and Temporal at 0 replicas (free) |

**Hosting plan status:** every step done and verified except a GitHub App (the lab uses a Key Vault-held read-only token instead). Open question for work: pattern-repo access without anyone holding a key; see the options given to the engineer (GitHub App owned by the platform team, or publishing pattern release artifacts to Azure storage from GitHub Actions via OIDC so the worker never talks to GitHub).

**Live now:** `rg-forgeapi-forgeapi-host-dev` (environment, 3 apps, 2 identities; idle ≈ $0), `stforgeapitf6c68`, `kv-forgeapi-host-38b9`, plus the two old demo vaults and unused UAMI the engineer still has to delete by hand. Certificate for local runs expires 2026-09-27; the token expires in 30 days.

## 2026-09-20 — Work deployment brief; direct managed identity; GitHub App tokens

Engineer decision: skip local dev at work; deploy with the organisation's MCP server into an existing private ACA environment (Easy Auth, Key Vault per app, MCP-built images). Temporal starts on ACA there too (AKS Temporal later). Wants to reuse an existing user-assigned identity (as used by self-hosted runners). Egress is restricted; a GitHub App or machine credential exists; a state storage account exists.

**Built:** `app/msi_shim.py` (loopback VM-metadata token endpoint so Terraform can use an attached managed identity directly; no app registration or federation needed); `app/github_app.py` (hourly installation tokens; GHES host/API settings); `deploy/temporal/Dockerfile`; **`docs/work-deployment.md`** (task brief for Claude at work: rules, inputs, steps, env vars, verification order, troubleshooting from real lab failures, known limits). Image `v0.1.3`. Tests: 71 passing.

| Check | Result |
| --- | --- |
| Real Terraform (azurerm provider **and** azurerm backend) through the shim, locally, shim backed by the lab certificate identity | PASS: init, plan, apply, remote state, destroy |
| Same inside Container Apps, worker in direct managed-identity mode (federation blanked, temporary Reader + state-blob roles on the worker identity) | PASS: `signed_in_object_id` = the worker's **user-assigned identity** `e7144186…`, remote state written, `succeeded` in ~45 s |
| Cleanup | test deployment destroyed; temporary roles removed (identity back to table + Key Vault only); host `PUT` back to declared config on `v0.1.3` (`0 add, 4 change, 0 destroy`, federation setting restored); worker and Temporal at 0 replicas |

**Not verified:** GitHub App tokens against a real App (unit tests only); anything in the work environment; Easy Auth in front of the API; provider downloads under restricted egress (provider mirror not built); Temporal UI behind Easy Auth.

**Newly documented limit:** the worker must run as exactly one replica, because plan and apply are separate activities sharing a local workspace.

## 2026-09-20 — Business units: placement, injected inputs, sizes, ownership (branch `tenancy`)

Engineer requirement: end users must not know where resources go; the platform maps callers to a business unit and the business unit + environment to a subscription, set up when access is granted. Decisions and rules: [tenancy.md](tenancy.md).

**Built:** `app/tenants.py` (YAML mapping behind functions, to move to a database at work), `app/placement.py`, caller identity with Entra groups from the token, from Easy Auth's `X-MS-CLIENT-PRINCIPAL` (`FORGEAPI_AUTH_MODE=easyauth`) or `FORGEAPI_DEV_GROUPS` locally. Request gains `business_unit` (only when the caller has several), `environment`, `size`. The platform injects BU values, network values, `environment`, the size's values and the BU's default region, but only into variables the pattern declares, and removes those from the caller's schema. `location` is limited to the BU's regions. Terraform runs with the environment's subscription; state stays in the platform subscription. Records carry BU, environment, subscription, size, injected values and requester in both stores. Other BUs get 404; changing a deployment needs the environment's deploy right; `PUT`/`retry` re-read the mapping but a deployment never moves subscription. New: `GET /me`, `GET /deployments`, filtered `GET /patterns`, caller-specific pattern page and schema. Subscription IDs are never returned. Off unless `FORGEAPI_TENANTS_PATH` is set.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 86 / clean. 13 tenancy tests incl. a real Terraform run proving injected values and the target subscription reach Terraform; store contract for the new fields and scoped listing on SQLite **and** Table Storage (Azurite) |
| Bugs the tests caught | subscription leaked as `null` in responses; one Terraform call ran without the target subscription; Table SDK filter parameter parsing |
| Live, local, real `key-vault` v1.1.4 with a lab mapping (nothing deployed) | PASS: `/me` correct; pattern page hides `environment`, `business_unit`, `cost_center`, `sku_name`; dry run shows them injected with `location` = BU default |

**Finding for the pattern repos:** key-vault's `config.yaml` sizes use `dev/staging/prod` but environments are `prototype/dev/tst/stg/prd`, so no size is offered in `stg`/`prd` until the keys match. Patterns also need to declare `private_endpoint_subnet_id` (etc.) to receive network values.

**Not verified:** real group claims from Entra or Easy Auth (unit tests only); two real subscriptions; anything hosted. **Not built:** quotas, prd approvals, per-version limits, admin API/database for the mapping.

## 2026-09-20 — Business units proven end to end on the hosted lab copy

**Setup:** image `v0.2.0`; host pattern **v0.3.0** (`tenants_yaml` → `FORGEAPI_TENANTS_YAML` on the API only; added because the mapping is gitignored and cannot be in a CI-built image); key-vault **v1.1.5** (PR #2: sizing keys `dev/stg/prd`); Entra security groups `forgeapi-lab-platform-devs` (the engineer), `forgeapi-lab-platform-release` (empty on purpose), `forgeapi-lab-hr-devs` (the lab service principal); API app registration `groupMembershipClaims = SecurityGroup`. Host updated in place with `PUT` (`0 add, 4 change, 0 destroy`). One subscription backs every environment in the lab.

| Check (hosted API, real Entra tokens) | Result |
| --- | --- |
| Token issued before group claims were enabled (Azure CLI cache) | no `groups` claim → `/me` empty, no patterns. Correct fail-closed behaviour; a fresh token carried 23 groups, no overage |
| `/me` and `/patterns` as the engineer | `platform`, deploy to `dev` only (not in release group), 4 patterns |
| Refusals | no environment 422; `prd` 403; caller-set `cost_center` 422; `westeurope` 422 with the BU's region message; `business_unit: hr` 403 |
| Real `resource-group` deployment, `dev` | `succeeded`; request contained no BU, cost centre or region. **Azure shows tags `BusinessUnit=platform`, `CostCenter=CC-0001`, location `centralus`** (BU default, not the pattern's `eastus`). Subscription absent from the API response |
| Second real caller: service principal token (app-only, `groups` claim present) | `/me` → `hr`; sees only `resource-group`; lists 0 deployments; GET / logs / DELETE of platform's deployment → **404**; `key-vault` → 403; dry run injects `hr` / `CC-2000` |
| Sizes, key-vault v1.1.5 | page offers `small, medium, large` in `dev` and hides `environment`, `business_unit`, `cost_center`, `sku_name`; no size 422; `xl` 422; size + own `sku_name` 422 |
| Real `key-vault` deployment, `size: small` | `succeeded`; **Azure shows SKU `standard`** and the platform tags |
| Cleanup through the API | both `destroyed`; resource groups and Entra `sg-` groups gone; worker and Temporal at 0 replicas |

**Observations:** Terraform outputs such as resource IDs naturally contain the subscription ID; only the platform's own fields hide it. One status poll during destroy returned a non-JSON body (next poll fine); not investigated. Machine callers work: a service principal in a BU group is a valid caller, which is how pipelines would use the API.

**Not verified:** two genuinely different subscriptions; Easy Auth's header in a real Easy Auth deployment; group overage handling against a real over-limit user; a caller in two BUs with real tokens (unit-tested).

**Left in the lab:** the three `forgeapi-lab-*` Entra groups and the group-claims setting on the API app registration (needed for further demos).

## 2026-09-20 — Merged to main; budgets (branch `quotas-budgets`)

**Housekeeping:** PR #1 merged `tenancy` (which contained `aca-hosting` and `fastapi-rewrite`) into `main`. `main` had one Go-era docs commit from another machine (`dc1ffdd`, Windows WSL handover); it is merged, with its progress notes applied to `docs/archive-go/progress.md`. Go implementation preserved at tag `go-archive`. The three merged branches were deleted after a containment check.

**Budgets (engineer decisions in [tenancy.md](tenancy.md#budgets)):** `app/budgets.py`; `budget_monthly` per BU/environment; cost from the pattern's `estimated_costs` at the pinned commit, stored on the record; committed = everything not `destroyed` (failed counts); over-budget requests refused 403 with numbers; dry run reports impact; `PUT`/`retry` do not double count; unpriced patterns refused where a budget exists; examples declare cost 0. Visible in `/me`, the pattern page and dry runs.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 92 / clean. Budget tests: refusal with exact figures, dry-run refusal and report, failed still counts, destroy frees, no double count on update/retry, unpriced pattern refused only where budgeted, budgets separate per BU and environment; cost field (including a real zero) round-trips on SQLite and Table Storage |

**Not verified:** budgets on the hosted copy or against the real key-vault pattern's `estimated_costs` (unit-level only so far). **Known limits:** estimates are not bills; concurrent requests can overshoot; no counts, per-pattern caps, expiry or actual-spend reporting.

**Also explained to the engineer, not built:** the single-worker-replica limit (plan and apply are separate activities sharing a local workspace). Recommended fix: make apply re-create its workspace and re-plan (small), and store the saved plan in blob storage once approvals exist.

## 2026-09-20 — Budgets proven on the hosted lab copy

Image `v0.3.0`; lab mapping gave `platform/dev` `budget_monthly: 1.5`; host updated in place (`0 add, 4 change, 0 destroy`). Costs came from the **real** key-vault v1.1.5 `config.yaml` (dev: small 1, medium 5, large 15). Real Entra token.

| Check (hosted API) | Result |
| --- | --- |
| `/me` and pattern page | budget 1.5 / committed 0 / available 1.5; cost per size shown |
| `resource-group` (its repo has no `config.yaml`) | 403: declares no estimated cost. **Finding:** that pattern needs `estimated_costs` before it can be used in any budgeted environment |
| key-vault `medium`, dry run | 403 with figures (this request 5, available 1.5) |
| key-vault `small`, dry run | 200, reports cost 1 and budget impact |
| Real key-vault `small` deployment | 202; budget shows committed 1 **while still in flight**; `succeeded` |
| Second `small` | 403 (committed 1, request 1, available 0.5) |
| `PUT {}` on the live deployment | 202 and `succeeded`: its own cost was not counted twice |
| `PUT {"size":"medium"}` | 403: the +4 does not fit |
| `DELETE` | budget stayed committed while `destroying`, freed to 0 once `destroyed`; Azure clean; worker and Temporal back to 0 replicas |

Follow-up from the run: an over-budget **update** reported `committed: 0`, which is correct (it excludes the deployment itself) but reads oddly; the response now also carries `this_deployment_now`. Tests: 92 passing.

## 2026-09-20 — Multi-replica workers, concurrency, hosted logs (branch `multi-worker`)

PR #2 (budgets) merged to `main`; this branch starts from there.

**Goal:** remove the single-worker-replica limit. **Fix:** `terraform.apply` checks for a saved plan made from exactly this source, variables and subscription (a fingerprint written at plan time); if this replica does not hold one, it rebuilds the workspace and re-plans. A saved plan is deleted once applied. This also closed a single-worker hazard: a plan left by a request that never reached apply could have been applied to a later request.

**Two further defects found by proving it on the hosted copy with two replicas and eight simultaneous deployments:**

1. **Terraform's shared provider cache is not safe under concurrency.** First run: 4 of 8 failed (`cached package … does not match`, `text file busy`). One deployment's `init` wrote the cache while another's `plan` executed the same provider. It affects a single worker too; every earlier test ran deployments one at a time. Fix: a reader/writer gate, `init` alone, everything else parallel, writers first. A local test with a cold cache and four parallel deployments failed most runs before and passed 12 of 12 after. (A first attempt at this fix silently did not apply because a text replacement did not match; the test caught it.)
2. **`GET /deployments/{id}/logs` was always empty when hosted**: the API read its own disk while the worker, a different container, wrote the file. The earlier hosted "no token in the logs" checks were therefore vacuous. Fix: `app/logs.py` also writes chunks to a second table (`<table>logs`) in the same storage account, and the API reads from the store.

Also: host pattern **v0.3.1** adds `worker_max_replicas` (scaling the worker by hand to 2 had made the next apply fail with `ContainerAppInvalidScaleSpec`, because Terraform pinned max 1 while ignoring min); `scripts/aca.sh` now changes only the minimum. Images `v0.3.1`, `v0.3.2`.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 97 / clean. New: apply on a replica that did not plan; stale plan never applied; cold-cache concurrency; logs in both stores incl. a 70 KB write |
| Hosted, 2 replicas, image v0.3.1 (before the cache and log fixes) | 4 of 8 failed; logs empty |
| Hosted, 2 fresh replicas (cold caches), image v0.3.2, 8 simultaneous deployments | **8 of 8 succeeded; 4 of 8 had plan and apply on different replicas** and re-planned on the applying one |
| Logs through the hosted API | present (71–131 lines each); scanned 8 real logs for tokens/keys: none |
| Cleanup: 16 destroys at once across 2 replicas | 15 × 202, 1 × 409 (already destroyed); all `destroyed`; budget back to 0; worker and Temporal at 0 replicas |

**Operator error worth recording:** a first cleanup attempt did nothing because zsh does not word-split unquoted variables, so one DELETE went to a garbage URL; the non-JSON reply seen in that run came from that. Two earlier non-JSON replies during status polling remain unexplained; status codes are now captured when polling.

**Still true:** patterns that keep local state need a single worker. In-memory Temporal history. Approvals would want the saved plan stored in blob storage so that what was reviewed is what is applied.

## 2026-09-20 — PR #3 merged; "down" did not actually stop the hosted worker and Temporal

PR #3 (`multi-worker`) merged to `main`; branch deleted after a containment check. 97 tests pass on `main`.

**Defect (cost):** asked whether the lab was off, the assistant found the worker (2 replicas) and Temporal (1) still **running** although `scripts/aca.sh down` had set min replicas to 0 each time. Those apps have no ingress-driven scale rule, so Container Apps never scales them in, and every `down`/`up` update also created a new revision with fresh replicas. Earlier "back to 0 replicas" statements in this log checked the **min-replicas setting, not running replicas**, and were wrong: those two apps most likely ran continuously from the first `up` until now (roughly seven hours, about 0.75 vCPU / 1.5 GiB in total). The API app did scale to zero (it has an HTTP scale rule).

**Fix:** `down` now sets min 0 **and deactivates the active revisions**; `up` activates the latest revision and sets min 1 (Temporal first); `status` reports replicas actually running. Verified with a real cycle: `up` → 1 + 1 running, `down` → 0 / 0 / 0 running.

**Cost:** the Cost Management query was rate-limited (429), and usage takes 8–24 h to appear, so the actual figure is unknown. The Container Apps monthly free grant (180,000 vCPU-s, 360,000 GiB-s) likely covers it: ~7 h × 0.75 vCPU ≈ 19,000 vCPU-s and ≈ 38,000 GiB-s. To be checked in the portal tomorrow.

**Everything in the subscription now:** host RG (environment, 3 apps at 0 running replicas, 2 identities: no charge while stopped); `forgeapitestRG01` (state storage account: pennies; three Key Vaults and one unused identity: no standing charge); another project's `rg-cg26091947f163` (FlexConsumption function app with 0 workers, two storage accounts, App Insights) and the default Log Analytics workspace (pay per GB ingested), none created by this work.

## 2026-09-20 — Audit trail (branch `audit-log`)

PR #4 (`aca.sh down` fix) merged to `main`. Engineer chose the audit log from the list of next features.

**Built:** `app/audit.py` and hooks in every mutating route, in access checks and in the worker. Design, guarantees and limits: [audit.md](audit.md). Accepted actions are recorded before dispatch and fail closed (503, nothing started); refusals, denied access and worker outcomes are recorded best-effort with a logged warning on failure; dry runs are not recorded; sensitive input values are redacted; auditors (mapping `auditors:` groups) read every unit's events and nothing else; `GET /deployments/{id}/events`, `GET /events`.

| Check | Result |
| --- | --- |
| `uv run pytest` / `ruff` | PASS: 109 / clean. 11 audit tests: event content and injected values, redaction of a `sensitive` variable, refusals with the caller-visible reason (permission, invalid input, budget figures), dry runs leave no trace, full lifecycle order, worker success and failure outcomes from real Terraform runs, probing another unit's deployment is filed under its owners and invisible to the prober, auditor scope, fail-closed when the store is down, every `events` route is GET-only; event store contract on SQLite **and** Table Storage (Azurite) |

**Known gaps recorded in audit.md:** storage is not immutable; deployment records still hold sensitive inputs in clear; cross-process ordering within milliseconds; no retention/export. **Not verified:** the hosted copy.

## 2026-09-20 — Audit trail proven on the hosted lab copy

Image `v0.4.0`; lab mapping gained `auditors:` (the hr group, so the lab service principal doubles as an auditor). Events are stored in the `deploymentsevents` table of the state storage account, written by two different containers (API and worker). Two real callers with real Entra tokens.

| Check (hosted) | Result |
| --- | --- |
| Refused create (`prd`, not in the release group) | recorded: 403, environment `prd`, reason exactly as the caller saw it |
| Dry run | 200 and **no event** |
| One deployment's trail, read by its owner | 8 events in order: create accepted (inputs, injected) → worker succeeded → two `deployment.access` refusals by the hr service principal (read, then change attempt; both saw 404) → update accepted (`inputs_changed: true`) → worker succeeded → destroy accepted → worker destroyed |
| `/events` as a business-unit member | own unit's events |
| `/events` as the auditor | sees `platform`'s events although not a member; **still 404 on the deployment itself** |
| Changing events | `DELETE /events` and `PUT …/events` → 405 |
| Shutdown | `aca.sh down`, then `status`: worker 0, Temporal 0 running replicas (API returns to 0 on its own after its idle cooldown) |

**Not verified:** the fail-closed path against a real storage outage (unit-tested only); retention/export; immutability at the storage layer (not built).
