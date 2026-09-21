# Work deployment brief (for Claude at work, using the Container Apps MCP server)

You are deploying **forgeapi** into an existing, private Azure Container Apps environment using the organisation's MCP server. Everything here was built and proven in a home lab first; your job is to reproduce a known-good shape, not to design. Read this whole file before calling any tool.

**Current as of:** `main` at image tag `v0.5.0` (2026-09-21), 113 tests. forgeapi is a **dev-environment** self-service API: there is no production approval workflow, by decision. Companion documents, all in `docs/`: `tenancy.md` (business units, sizes, budgets), `audit.md`, `outputs.md`, `hosting-plan.md`, and `progress.md` (every lab run with its evidence and gaps).

## Ground rules

- **Do not improvise architecture.** If the MCP server cannot do something this brief requires, stop and report what is missing. Do not substitute a workaround of your own.
- **Read before you write.** Do the checks in [Step 0](#step-0-discover-and-verify-read-only) before creating anything.
- **Never print, log or paste secrets** (private keys, tokens). Reference them by Key Vault secret name only.
- **Stop and ask the engineer** before: creating or changing role assignments, touching anything outside the three apps named here, deploying any real pattern (anything except `local-file` and `azure-identity-check`), or when a verification step fails twice.
- Do not run `uv`, tests or local Docker. Local development is deliberately skipped at work; the code is already tested (113 tests in the lab, including real Terraform, Temporal and storage-emulator runs).
- Report at the end using the [report format](#report-format). State plainly what was verified and what was not.

## What you are deploying

One repository, two images, three container apps in the existing environment:

| App | Image | Start command | Ingress | Replicas | Identity |
| --- | --- | --- | --- | --- | --- |
| `forgeapi-temporal` | built from `deploy/temporal/Dockerfile` | (image entrypoint) | **internal TCP 7233** | min 1, max 1 | none |
| `forgeapi-worker` | built from the repo root `Dockerfile` | `python -m app.worker` | **none** | min 1, max 1 to start (see below) | the user-assigned identity (below) |
| `forgeapi-api` | same image as the worker | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | HTTP 8000, private endpoint, Easy Auth (as the MCP server sets up) | min 1 (or 0), max 2 | the same user-assigned identity, or one with only table access |

How it works: the API identifies the caller (Easy Auth), maps their Entra groups to a business unit, checks the request against that unit's rules (allowed patterns, environments, regions, sizes, budget), adds the values callers must not set (subscription, cost centre, network IDs), writes an audit event and a record to Azure Table Storage, and starts a Temporal workflow. The worker fetches the Terraform pattern from its git repo at a pinned commit and runs `terraform plan`/`apply` against the business unit's subscription, with state in a blob container. Outputs come back on the record; secrets stay in the pattern's own Key Vault and only references are returned. Temporal starts on ACA for now; an AKS-hosted Temporal exists and can replace it later by changing one setting.

**Worker replicas.** Start with one. More than one is supported from image v0.3.2: plan and apply are separate steps and may land on different replicas, so apply verifies it holds the plan for exactly this request and otherwise rebuilds the workspace and re-plans (proven with two replicas: 8 of 8 deployments succeeded, 4 of them split across replicas). This relies on remote state, so it does **not** hold for patterns that keep local state (only the built-in `local-file` example). Never point two *different* worker deployments (for example a laptop and the hosted one) at the same Temporal queue.

## Inputs the engineer must give you

Ask for any that are missing. Do not guess.

| Input | Notes |
| --- | --- |
| User-assigned managed identity: resource ID, **client ID**, **principal (object) ID** | An existing identity (the organisation uses these for self-hosted GitHub runners). Terraform will act with exactly this identity's Azure rights. |
| Tenant ID and the **platform** subscription ID (the one holding the state storage account) | Target subscriptions come from the business-unit mapping, one per business unit and environment. The identity needs its deploy rights in **every** mapped subscription. |
| State storage: resource group, account name, blob container (default `tfstate`), table name (default `deployments`; the app also creates `deploymentslogs` and `deploymentsevents` itself) | Must be reachable from the ACA environment: private endpoints **and private DNS for both `blob` and `table`**. |
| Pattern repositories: GitHub host, org, repo names, and sub-directory if the root module is not at the repo root | Root modules with their own `provider` and `backend "azurerm" {}` blocks, versioned by semver tags. |
| GitHub access: either GitHub App ID + installation ID + Key Vault secret holding the App private key (PEM), **or** a Key Vault secret holding a read-only machine token | The MCP server gives the app a Key Vault; the secret is referenced, never copied. |
| Whether GitHub is github.com or GitHub Enterprise Server | GHES needs two extra settings (below). |
| Egress allowlist status | See [Step 1](#step-1-egress). |
| Business-unit mapping: for each BU its Entra group IDs, cost centre, allowed patterns and regions, and per (dev) environment the subscription ID, network IDs and optional `budget_monthly`; optionally top-level `auditors` group IDs | Format: `tenants.example.yaml`, rules: `docs/tenancy.md`. **Delivered as configuration, not baked into the image** (Step 5), so onboarding a team needs no rebuild. List `local-file` and `azure-identity-check` under at least one business unit's `patterns`, or verification cannot run. |
| For each pattern repo: confirmation it meets the [pattern checklist](#pattern-repo-checklist) | Sizes, cost estimates and secret handling live in the pattern repos, not here. |

## Step 0: discover and verify (read-only)

1. List the MCP server's tools. Confirm it can, per app: set a **custom start command**, set **environment variables**, reference **Key Vault secrets as env vars**, **disable ingress**, create **TCP ingress**, set **min/max replicas**, and **attach an existing user-assigned identity**. Record any it cannot do and stop if one is required above.
2. Confirm the identity's role assignments (ask the engineer to grant what is missing; do not grant them yourself):
   - `Storage Blob Data Contributor` on the state container;
   - `Storage Table Data Contributor` on the storage account (covers all three tables: records, logs, audit events; the app creates them on first use);
   - whatever the patterns need on the target scope (typically `Contributor`, plus `Role Based Access Control Administrator` if patterns assign roles, plus Microsoft Graph `Group.ReadWrite.All` and `User.Read.All` if patterns create Entra groups);
   - `Key Vault Secrets User` on the app's Key Vault, for the GitHub secret.
3. Confirm the `Microsoft.App` resource provider is registered (it will be, since the environment exists).

## Step 1: egress

Outbound internet is restricted. At **run time** the worker needs HTTPS to:

- the GitHub host (pattern and module fetches) and, for github.com, `codeload.github.com`;
- `registry.terraform.io`, `releases.hashicorp.com`, and `github.com` + `objects.githubusercontent.com` (provider downloads; non-HashiCorp providers such as `Azure/azapi` are served from GitHub releases);
- `login.microsoftonline.com`, `management.azure.com`, `graph.microsoft.com`;
- the storage account and Key Vault (through their private endpoints).

At **build time** the image pulls `hashicorp/terraform:1.15.9` (Docker Hub), `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, Debian apt packages (`git`), PyPI packages, and `temporalio/temporal:1.8.3` (Docker Hub) for the Temporal image. If the MCP server's build environment cannot reach those, stop and report: the engineer needs mirrored base images, and you should only change the `FROM` lines to the mirrors they name.

If the Terraform registry cannot be allowlisted, stop and report. The known fix (bake a provider mirror into the image with `terraform providers mirror` and a `filesystem_mirror` CLI config) is not built yet.

## Step 2: edit the catalog, then build

`patterns.yaml` is baked into the image. (The business-unit mapping is **not**: it is tenant-specific and gitignored, and is supplied as configuration in Step 5.) Replace the lab entries with the work pattern repos **before** building. Keep the two built-in examples; they are your verification tools.

```yaml
patterns:
  <pattern-name>:
    repo: <github-host>/<org>/<repo>
    path: <sub-dir>            # omit when the root module is at the repo root
    default_version: v1.2.3    # optional pin; omit to follow the newest tag

  local-file:
    local: examples/local-file
  azure-identity-check:
    local: examples/azure-identity-check
```

Build two images with the MCP server: the repo root `Dockerfile`, and `deploy/temporal/Dockerfile`. Do not change application code.

## Step 3: deploy Temporal

Create `forgeapi-temporal` from the Temporal image: internal **TCP** ingress, target and exposed port 7233, min/max replicas 1, no identity, no env vars. Easy Auth only applies to HTTP, so it does not cover (or block) this port.

The Temporal web UI listens on 8233 in the same container. Expose it **only** if the MCP server supports an HTTP ingress with Easy Auth **plus** an additional TCP port mapping for 7233 on the same app. If it cannot do both, leave the UI unexposed; do not make 7233 HTTP.

History is in memory: restarting this app forgets workflow history. Deployment records and Terraform state are not affected, and `retry` recovers a deployment interrupted by a restart.

## Step 4: deploy the worker

`forgeapi-worker`: app image, command `python -m app.worker`, **no ingress**, min 1 / max 1 to begin with, user-assigned identity attached. CPU 0.5 / memory 1Gi is enough.

Environment (worker **and** API get all of these; differences noted):

| Variable | Value |
| --- | --- |
| `FORGEAPI_DATA_DIR` | `/tmp/forgeapi` |
| `FORGEAPI_TEMPORAL_ADDRESS` | `forgeapi-temporal:7233` (the Temporal app's name inside the environment) |
| `FORGEAPI_TEMPORAL_NAMESPACE` | `default` |
| `FORGEAPI_DB_BACKEND` | `table` |
| `FORGEAPI_TABLE_STORAGE_ACCOUNT` | state storage account name |
| `FORGEAPI_TABLE_NAME` | `deployments` |
| `FORGEAPI_AZURE_USE_MANAGED_IDENTITY` | `true` |
| `FORGEAPI_AZURE_MANAGED_IDENTITY_CLIENT_ID` | the identity's **client ID** |
| `FORGEAPI_AZURE_TENANT_ID` | tenant ID |
| `FORGEAPI_AZURE_SUBSCRIPTION_ID` | the **platform** subscription ID (where the state storage account lives). Deployments target the business unit's subscription from the mapping; state always stays here. |
| `FORGEAPI_STATE_RESOURCE_GROUP` | state storage resource group |
| `FORGEAPI_STATE_STORAGE_ACCOUNT` | state storage account name |
| `FORGEAPI_STATE_CONTAINER` | `tfstate` |
| `FORGEAPI_AUTH_MODE` | `easyauth` (see Step 5) |
| GitHub App: `FORGEAPI_GITHUB_APP_ID`, `FORGEAPI_GITHUB_APP_INSTALLATION_ID`, and `FORGEAPI_GITHUB_APP_PRIVATE_KEY` **as a Key Vault secret reference** | preferred |
| …or machine token: `FORGEAPI_GITHUB_TOKEN` **as a Key Vault secret reference** | alternative |
| GHES only: `FORGEAPI_GITHUB_HOST` = `<host>`, `FORGEAPI_GITHUB_API_URL` = `https://<host>/api/v3` | omit for github.com |

**Do not set** `FORGEAPI_AZURE_FEDERATED_CLIENT_ID`, `FORGEAPI_AZURE_CLIENT_ID` or `FORGEAPI_AZURE_CLIENT_CERTIFICATE_PATH`. Those are lab-only sign-in modes and the first one takes precedence over managed identity. Also never set `FORGEAPI_DEV_GROUPS` (it hands group memberships to unauthenticated local callers; it is ignored under `easyauth`, but it has no place in a hosted app) or `FORGEAPI_TABLE_CONNECTION_STRING` (storage emulator, tests only; real access is by managed identity). `FORGEAPI_CATALOG_PATH`, `FORGEAPI_TASK_QUEUE` and `FORGEAPI_TERRAFORM_BIN` have working defaults; leave them alone.

Why managed identity works here: Terraform can only ask the VM metadata address for tokens, which Container Apps does not have. The worker runs a loopback token endpoint (`app/msi_shim.py`) backed by the Azure SDK and points Terraform at it. No app registration, federated credential or secret is involved; the attached identity's own role assignments apply.

## Step 5: deploy the API

`forgeapi-api`: same image, command `uvicorn app.main:app --host 0.0.0.0 --port 8000`, HTTP ingress on 8000 behind the private endpoint, Easy Auth as the MCP server configures it, same env vars, identity attached (it needs the table role and Key Vault secret access; it never runs Terraform).

Auth: set `FORGEAPI_AUTH_MODE=easyauth`. Easy Auth signs the caller in and passes their identity and Entra groups to the app in the `X-MS-CLIENT-PRINCIPAL` header (it strips any copy a client sends, which is why this mode is only safe behind Easy Auth; never use it on an app without it). The business-unit mapping then decides what that caller may deploy and where. **The Easy Auth app registration must emit group claims** (token configuration → groups claim → *Groups assigned to the application*, which also avoids the too-many-groups overage); confirm this with the engineer, and verify with `GET /me` that the caller's business units appear. (The app's own `entra` mode also exists: set `FORGEAPI_AUTH_MODE=entra`, `FORGEAPI_ENTRA_TENANT_ID`, and `FORGEAPI_ENTRA_AUDIENCE` = the **client ID** of the Easy Auth app registration, which requires that registration to issue v2 tokens. Only do this if asked.)

**Business-unit mapping (API app only; the worker does not need it).** Set `FORGEAPI_TENANTS_YAML` to the mapping's YAML text. Preferred: store the text as a secret in the app's Key Vault and reference it, because it is multi-line and changes whenever a team is onboarded; updating the secret and restarting the API revision is then all that onboarding takes. It contains group and subscription IDs, not credentials. (Alternative: bake a file into the image and set `FORGEAPI_TENANTS_PATH`; then every mapping change is a rebuild.) Without either setting the API runs single-tenant with **no** placement, ownership or budgets: do not deploy it that way at work.

`/healthz` needs no token and is safe for probes.

## Step 6: verify, in this order

Call the API through whatever authenticated path Easy Auth allows (ask the engineer how they obtain a token or session). Stop at the first failure and consult [Troubleshooting](#troubleshooting).

1. `GET /healthz` → `200 {"status":"ok"}`.
2. `GET /me` → the caller's business units, the environments they may deploy to, regions, patterns and **budget position**. `"business_units": null` means the mapping is not configured (stop: see Step 5). An empty list means group claims are not reaching the app or the mapping has the wrong group IDs.
2b. `GET /patterns` → only the patterns the caller's business units allow.
3. `GET /patterns/<a real pattern>` → versions (tags) and inputs. Proves GitHub access. `502` means it does not work yet.
4. `POST /deployments` `{"pattern":"local-file","environment":"<env>","inputs":{"filename":"hello.txt","content":"hi"}}` (the two example patterns must be listed under a business unit's `patterns` for this), poll `GET /deployments/<id>` until `succeeded`. Proves API → Table Storage → Temporal → worker → Terraform, with no Azure sign-in involved.
5. `POST /deployments` `{"pattern":"azure-identity-check","environment":"<env>","inputs":{"note":"work identity test"}}` → `succeeded`, and **`outputs.signed_in_object_id` equals the identity's principal (object) ID**. This pattern creates nothing; it proves Terraform signs in as the identity and can write remote state.
5b. `GET /deployments/<id>/logs` returns Terraform output (not empty), and `GET /deployments/<id>/events` shows `deployment.create accepted` followed by `deployment.state succeeded`. Proves the shared log and audit tables work across the two containers.
6. `DELETE /deployments/<id>` for both; each ends `destroyed`, and the budget position in `GET /me` returns to where it started.
7. `GET /patterns/<a real pattern>`: `placement` shows the sizes offered in the environment and their estimated monthly cost; platform-supplied inputs (environment, business unit, cost centre, network IDs, sized values) are **absent** from `inputs`. Then `POST /deployments?dry_run=true` with the engineer's inputs, `environment` and `size` → `{"valid": true}` with `injected` values and the budget impact. Creates nothing and is not audited.
8. **Only with the engineer's explicit go-ahead:** deploy one real, cheap pattern; verify the resource independently, **including that its tags carry the business unit and cost centre the caller never sent**; check `secret_references` / `withheld_outputs` if the pattern produces secrets; `DELETE` it.

## API quick reference

| Call | Purpose |
| --- | --- |
| `GET /patterns`, `GET /patterns/{name}?version=`, `GET /patterns/{name}/schema` | catalog, inputs with allowed values and an example request, JSON Schema |
| `GET /me` | the caller's business units, environments, regions, patterns, budgets |
| `POST /deployments` (`?dry_run=true` to validate only) | deploy `{"pattern","environment","size"?,"version"?,"business_unit"?,"inputs"}`. `business_unit` only when the caller belongs to several; `size` when the pattern defines sizes |
| `GET /deployments`, `GET /deployments/{id}`, `GET /deployments/{id}/logs` | the caller's units' deployments; state, outputs, `secret_references`, `withheld_outputs`, error; Terraform log |
| `GET /deployments/{id}/events`, `GET /events` | audit trail (read-only) |
| `PUT /deployments/{id}` | new inputs and/or version, applied against existing state |
| `POST /deployments/{id}/retry` | finish a failed deployment |
| `DELETE /deployments/{id}` | `terraform destroy`; record kept as `destroyed` |

## Troubleshooting

Every entry below actually happened in the lab.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `503 deployment records are temporarily unavailable`, or `AuthorizationPermissionMismatch` in API logs | Table role missing or granted under ~2 minutes ago | confirm the role; wait; retry |
| `ManagedIdentityAuthorizer … 169.254.169.254 … connection refused` | `FORGEAPI_AZURE_USE_MANAGED_IDENTITY` not `true`, or an image older than v0.1.3 | fix the env var / rebuild |
| `signed_in_object_id` is not the identity's principal ID | a federated or certificate setting is present | remove the "do not set" variables |
| `missing or corrupted provider plugins`, `cached package … does not match`, `text file busy` | an image older than v0.3.2 (parallel deployments corrupted Terraform's shared provider cache, even on one replica), or two different worker deployments on one Temporal queue | use v0.3.2+; one worker deployment per Temporal |
| `GET …/logs` is empty although the deployment ran | an image older than v0.3.2 (logs stayed on the worker's disk) | use v0.3.2+; logs are stored in the `deploymentslogs` table |
| `502` on `GET /patterns/{name}` or at POST | cannot reach/authenticate to GitHub | check egress, App installation covers the repo **and every module repo it references**, secret reference resolves |
| Container app revision fails: `Unable to get value using Managed identity … for secret` | identity lacks `Key Vault Secrets User`, or the role is too new | grant/wait, then redeploy the revision |
| `503 job engine unavailable` on POST | API cannot reach Temporal | Temporal app running? TCP ingress 7233 internal? address is `<app-name>:7233` |
| `Failed to query available provider packages` / registry timeouts in deployment logs | egress to the Terraform registry blocked | allowlist, or stop and report (provider mirror not built) |
| Deployment stuck in `planning`/`applying` after a Temporal restart | in-memory history lost | it will not resume; set nothing by hand, use `POST …/retry` once it is marked failed, or ask the engineer |
| `GET /me` shows `"business_units": null` | the mapping is not configured on the API app | set `FORGEAPI_TENANTS_YAML` (Step 5) |
| `GET /me` shows no business units for someone who should have one | Easy Auth is not passing group claims (or too many groups: overage), or wrong group IDs in the mapping | app registration → token configuration → groups claim → *Groups assigned to the application*; compare object IDs |
| `422 name an environment` / `name a size; <env> offers [...]` | request lacks `environment`, or the pattern defines sizes and none was chosen | add them; sizes offered per environment come from the pattern's `config.yaml` |
| `422 size '<x>' is not offered in <env>` with an **empty** list | the pattern's `config.yaml` sizing keys do not match the environment names (`dev`, `stg`, `prd`, …) | fix the pattern repo and tag a new version |
| `403 … declares no estimated cost` | the environment has a `budget_monthly` and the pattern's `config.yaml` has no `estimated_costs` for it | add estimates to the pattern (or `estimated_costs: 0` if it is genuinely free) |
| `403 this would exceed the estimated monthly budget` | working as designed; the response gives the figures | destroy unused deployments, choose a smaller size, or raise the budget in the mapping |
| `503 the audit trail is unavailable, so the request was not carried out` | the API could not write the audit event (Table access) | same causes as the first row; nothing was started |
| `withheld_outputs` is not empty | the pattern outputs a `sensitive` value instead of storing it in a vault | pattern bug: see `docs/outputs.md` |
| A poll returns a non-JSON body right after the API app was updated | seen in the lab within a minute or two of a revision change; not reproduced on demand, cause unconfirmed | retry the request; capture the status code if it persists |
| `MissingSubscriptionRegistration` for some namespace | target subscription has never used that resource type | engineer registers the provider |

## Known limits to tell the engineer about

- In-memory Temporal history. Patterns that keep local state need a single worker replica.
- Authorization is by business unit and environment (Entra groups), with estimated-cost budgets. Budgets use the pattern authors' estimates, not Azure billing, and two simultaneous requests can overshoot slightly. No deployment counts, expiry, approvals or per-pattern-version limits.
- **Choice of identity.** Reusing a GitHub-runner identity means every API caller deploys with that identity's reach, in every mapped subscription. Patterns that grant the deploying identity data access (the lab key-vault module makes it Secrets Officer on each vault it creates) also let that identity read those secrets. A dedicated identity with narrower rights is the better long-term choice.
- The audit trail is append-only through the API but the storage is not immutable, and there is no retention or export. Sensitive **inputs** are stored in clear on the deployment record; pass secrets as Key Vault references. Details: `docs/audit.md`.
- To stop the worker or Temporal, deactivate the app's revision. Setting min replicas to 0 does **not** stop an app that has no HTTP scale rule (found the hard way in the lab).
- GitHub App token support is unit-tested only; it has not been run against a real App. A machine token through a Key Vault reference **has** been proven end to end.
- The Terraform provider mirror for fully closed egress is not built.
- Moving to the AKS Temporal later: change `FORGEAPI_TEMPORAL_ADDRESS`/`NAMESPACE` on both apps and delete `forgeapi-temporal`. Connections needing mTLS or an API key are not supported by the code yet.

## Pattern repo checklist

Most behaviour callers see is defined in the pattern repositories, read at the pinned commit. For each pattern to be registered, confirm with the engineer (do not fix pattern repos yourself unless asked):

1. It is a **runnable root module** with its own `provider` blocks and `backend "azurerm" {}`, versioned with **semver tags** (`v1.2.3`). Every module it references is readable with the same GitHub credential.
2. `variable` blocks have accurate `description`s, `type`s and `validation` rules with clear `error_message`s: these are the API's user-facing documentation and validation.
3. To receive platform values it **declares variables with these exact names**: `environment`, `business_unit`, `cost_center`, `location`, plus any network keys used in the mapping (for example `private_endpoint_subnet_id`). Declared ones are filled by the platform and hidden from callers; undeclared ones are simply not passed.
4. `config.yaml` beside the root module (or at the repo root) has `description`, and where relevant `sizing.<size>.<environment>.<variable>` and `estimated_costs.<size>.<environment>` (or `.<environment>`, or one number). **Environment keys must match the mapping's environment names.** A size locks the inputs it sets. Without `estimated_costs` the pattern is refused wherever a budget exists.
5. Secrets follow `docs/outputs.md`: prefer Entra authentication and no secret; otherwise the pattern stores the secret in a Key Vault it creates and outputs the **versionless secret ID**, and grants read access itself (groups owned by the request's `owners`, or the workload's identity). Never output a secret value, even marked `sensitive`.

## Report format

```
Deployed: <apps, image tags, identity used>
Verified: <numbered Step 6 checks that passed, with deployment IDs>
Not verified / skipped: <which, and why>
Deviations from the brief: <none | list>
Open items for the engineer: <role grants, egress, decisions>
```
