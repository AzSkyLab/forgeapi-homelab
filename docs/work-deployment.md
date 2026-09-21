# Work deployment brief (for Claude at work, using the Container Apps MCP server)

You are deploying **forgeapi** into an existing, private Azure Container Apps environment using the organisation's MCP server. Everything here was built and proven in a home lab first; your job is to reproduce a known-good shape, not to design. Read this whole file before calling any tool.

## Ground rules

- **Do not improvise architecture.** If the MCP server cannot do something this brief requires, stop and report what is missing. Do not substitute a workaround of your own.
- **Read before you write.** Do the checks in [Step 0](#step-0-discover-and-verify-read-only) before creating anything.
- **Never print, log or paste secrets** (private keys, tokens). Reference them by Key Vault secret name only.
- **Stop and ask the engineer** before: creating or changing role assignments, touching anything outside the three apps named here, deploying any real pattern (anything except `local-file` and `azure-identity-check`), or when a verification step fails twice.
- Do not run `uv`, tests or local Docker. Local development is deliberately skipped at work; the code is already tested (97 tests in the lab).
- Report at the end using the [report format](#report-format). State plainly what was verified and what was not.

## What you are deploying

One repository, two images, three container apps in the existing environment:

| App | Image | Start command | Ingress | Replicas | Identity |
| --- | --- | --- | --- | --- | --- |
| `forgeapi-temporal` | built from `deploy/temporal/Dockerfile` | (image entrypoint) | **internal TCP 7233** | min 1, max 1 | none |
| `forgeapi-worker` | built from the repo root `Dockerfile` | `python -m app.worker` | **none** | min 1, max 1 to start (see below) | the user-assigned identity (below) |
| `forgeapi-api` | same image as the worker | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | HTTP 8000, private endpoint, Easy Auth (as the MCP server sets up) | min 1 (or 0), max 2 | the same user-assigned identity, or one with only table access |

How it works: the API validates a request, writes a record to Azure Table Storage and starts a Temporal workflow. The worker picks it up, fetches the Terraform pattern from its git repo at a pinned commit, and runs `terraform plan`/`apply` with state in a blob container. Temporal starts on ACA for now; an AKS-hosted Temporal exists and can replace it later by changing one setting.

**Worker replicas.** Start with one. More than one is supported from image v0.3.2: plan and apply are separate steps and may land on different replicas, so apply verifies it holds the plan for exactly this request and otherwise rebuilds the workspace and re-plans (proven with two replicas: 8 of 8 deployments succeeded, 4 of them split across replicas). This relies on remote state, so it does **not** hold for patterns that keep local state (only the built-in `local-file` example). Never point two *different* worker deployments (for example a laptop and the hosted one) at the same Temporal queue.

## Inputs the engineer must give you

Ask for any that are missing. Do not guess.

| Input | Notes |
| --- | --- |
| User-assigned managed identity: resource ID, **client ID**, **principal (object) ID** | An existing identity (the organisation uses these for self-hosted GitHub runners). Terraform will act with exactly this identity's Azure rights. |
| Tenant ID and default target subscription ID | One subscription per worker for now. |
| State storage: resource group, account name, blob container (default `tfstate`), table name (default `deployments`) | Must be reachable from the ACA environment: private endpoints **and private DNS for both `blob` and `table`**. |
| Pattern repositories: GitHub host, org, repo names, and sub-directory if the root module is not at the repo root | Root modules with their own `provider` and `backend "azurerm" {}` blocks, versioned by semver tags. |
| GitHub access: either GitHub App ID + installation ID + Key Vault secret holding the App private key (PEM), **or** a Key Vault secret holding a read-only machine token | The MCP server gives the app a Key Vault; the secret is referenced, never copied. |
| Whether GitHub is github.com or GitHub Enterprise Server | GHES needs two extra settings (below). |
| Egress allowlist status | See [Step 1](#step-1-egress). |
| Business-unit mapping: for each BU its Entra group IDs, cost centre, allowed patterns and regions, and per environment the subscription ID and network IDs | Goes in `tenants.yaml` (see `tenants.example.yaml`, `docs/tenancy.md`). Baked into the image like `patterns.yaml`. The worker identity needs its deploy rights in **every** mapped subscription. |

## Step 0: discover and verify (read-only)

1. List the MCP server's tools. Confirm it can, per app: set a **custom start command**, set **environment variables**, reference **Key Vault secrets as env vars**, **disable ingress**, create **TCP ingress**, set **min/max replicas**, and **attach an existing user-assigned identity**. Record any it cannot do and stop if one is required above.
2. Confirm the identity's role assignments (ask the engineer to grant what is missing; do not grant them yourself):
   - `Storage Blob Data Contributor` on the state container;
   - `Storage Table Data Contributor` on the storage account (the app creates the table itself on first use);
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

`patterns.yaml` and `tenants.yaml` are baked into the image (add `COPY tenants.yaml ./` next to the `patterns.yaml` line in the `Dockerfile` and `!tenants.yaml` to `.dockerignore`; the file is gitignored because it holds tenant-specific IDs, so the engineer supplies it). Replace the lab entries with the work pattern repos **before** building. Keep the two built-in examples; they are your verification tools.

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
| `FORGEAPI_AZURE_SUBSCRIPTION_ID` | default target subscription ID |
| `FORGEAPI_STATE_RESOURCE_GROUP` | state storage resource group |
| `FORGEAPI_STATE_STORAGE_ACCOUNT` | state storage account name |
| `FORGEAPI_STATE_CONTAINER` | `tfstate` |
| `FORGEAPI_AUTH_MODE` | `easyauth` (see Step 5) |
| `FORGEAPI_TENANTS_PATH` | `tenants.yaml` |
| GitHub App: `FORGEAPI_GITHUB_APP_ID`, `FORGEAPI_GITHUB_APP_INSTALLATION_ID`, and `FORGEAPI_GITHUB_APP_PRIVATE_KEY` **as a Key Vault secret reference** | preferred |
| …or machine token: `FORGEAPI_GITHUB_TOKEN` **as a Key Vault secret reference** | alternative |
| GHES only: `FORGEAPI_GITHUB_HOST` = `<host>`, `FORGEAPI_GITHUB_API_URL` = `https://<host>/api/v3` | omit for github.com |

**Do not set** `FORGEAPI_AZURE_FEDERATED_CLIENT_ID`, `FORGEAPI_AZURE_CLIENT_ID` or `FORGEAPI_AZURE_CLIENT_CERTIFICATE_PATH`. Those are lab-only sign-in modes and the first one takes precedence over managed identity.

Why managed identity works here: Terraform can only ask the VM metadata address for tokens, which Container Apps does not have. The worker runs a loopback token endpoint (`app/msi_shim.py`) backed by the Azure SDK and points Terraform at it. No app registration, federated credential or secret is involved; the attached identity's own role assignments apply.

## Step 5: deploy the API

`forgeapi-api`: same image, command `uvicorn app.main:app --host 0.0.0.0 --port 8000`, HTTP ingress on 8000 behind the private endpoint, Easy Auth as the MCP server configures it, same env vars, identity attached (it needs the table role and Key Vault secret access; it never runs Terraform).

Auth: set `FORGEAPI_AUTH_MODE=easyauth`. Easy Auth signs the caller in and passes their identity and Entra groups to the app in the `X-MS-CLIENT-PRINCIPAL` header (it strips any copy a client sends, which is why this mode is only safe behind Easy Auth; never use it on an app without it). The business-unit mapping then decides what that caller may deploy and where. **The Easy Auth app registration must emit group claims** (token configuration → groups claim → *Groups assigned to the application*, which also avoids the too-many-groups overage); confirm this with the engineer, and verify with `GET /me` that the caller's business units appear. (The app's own `entra` mode also exists: set `FORGEAPI_AUTH_MODE=entra`, `FORGEAPI_ENTRA_TENANT_ID`, and `FORGEAPI_ENTRA_AUDIENCE` = the **client ID** of the Easy Auth app registration, which requires that registration to issue v2 tokens. Only do this if asked.)

`/healthz` needs no token and is safe for probes.

## Step 6: verify, in this order

Call the API through whatever authenticated path Easy Auth allows (ask the engineer how they obtain a token or session). Stop at the first failure and consult [Troubleshooting](#troubleshooting).

1. `GET /healthz` → `200 {"status":"ok"}`.
2. `GET /me` → the caller's business units, environments and patterns. Empty means group claims are not reaching the app or the mapping has the wrong group IDs.
2b. `GET /patterns` → only the patterns the caller's business units allow.
3. `GET /patterns/<a real pattern>` → versions (tags) and inputs. Proves GitHub access. `502` means it does not work yet.
4. `POST /deployments` `{"pattern":"local-file","environment":"<env>","inputs":{"filename":"hello.txt","content":"hi"}}` (the two example patterns must be listed under a business unit's `patterns` for this), poll `GET /deployments/<id>` until `succeeded`. Proves API → Table Storage → Temporal → worker → Terraform, with no Azure sign-in involved.
5. `POST /deployments` `{"pattern":"azure-identity-check","environment":"<env>","inputs":{"note":"work identity test"}}` → `succeeded`, and **`outputs.signed_in_object_id` equals the identity's principal (object) ID**. This pattern creates nothing; it proves Terraform signs in as the identity and can write remote state.
6. `DELETE /deployments/<id>` for both; each ends `destroyed`.
7. `POST /deployments?dry_run=true` with a real pattern and the engineer's inputs → `{"valid": true}`. Creates nothing.
8. **Only with the engineer's explicit go-ahead:** deploy one real, cheap pattern; verify the resource independently; `DELETE` it.

## API quick reference

| Call | Purpose |
| --- | --- |
| `GET /patterns`, `GET /patterns/{name}?version=`, `GET /patterns/{name}/schema` | catalog, inputs with allowed values and an example request, JSON Schema |
| `POST /deployments` (`?dry_run=true` to validate only) | deploy `{"pattern","version"?,"inputs"}` |
| `GET /deployments/{id}`, `GET /deployments/{id}/logs` | state/outputs/error, Terraform log |
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
| `MissingSubscriptionRegistration` for some namespace | target subscription has never used that resource type | engineer registers the provider |

## Known limits to tell the engineer about

- In-memory Temporal history. Patterns that keep local state need a single worker replica.
- Authorization is by business unit and environment (Entra groups). There are no quotas, approvals or per-pattern-version limits yet. Reusing a GitHub-runner identity means API callers inherit that identity's reach. A dedicated identity with narrower rights is the better long-term choice.
- GitHub App token support is unit-tested only; it has not been run against a real App. A machine token through a Key Vault reference **has** been proven end to end.
- The Terraform provider mirror for fully closed egress is not built.
- Moving to the AKS Temporal later: change `FORGEAPI_TEMPORAL_ADDRESS`/`NAMESPACE` on both apps and delete `forgeapi-temporal`. Connections needing mTLS or an API key are not supported by the code yet.

## Report format

```
Deployed: <apps, image tags, identity used>
Verified: <numbered Step 6 checks that passed, with deployment IDs>
Not verified / skipped: <which, and why>
Deviations from the brief: <none | list>
Open items for the engineer: <role grants, egress, decisions>
```
