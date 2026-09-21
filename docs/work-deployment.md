# Work deployment brief (for Claude at work, using the Container Apps MCP server)

You are deploying **forgeapi** into an existing, private Azure Container Apps environment with the organisation's MCP server. Everything here was built and proven in a home lab first; your job is to reproduce a known-good shape, not to design. Read this whole file before calling any tool.

**Current as of:** `main` at image tag `v0.6.0` (2026-09-21), 121 tests. forgeapi is a **dev-environment** self-service API: there is no production approval workflow, by decision. Companion documents, all in `docs/`: `tenancy.md` (business units, sizes, budgets), `audit.md`, `outputs.md`, `hosting-plan.md`, and `progress.md` (every lab run with its evidence and gaps).

## How the MCP server shapes this

The MCP server takes a repository, **builds one image and deploys it as one HTTP container app with Easy Auth in front**. It decides everything about the app. Through it you can set **environment variables** and **Key Vault secret references**; nothing else. The app gets a **system-assigned managed identity**; anything else about identity is manual. So:

- forgeapi runs **all in one container**: the image's default command (`python -m app.allinone`) starts a Temporal dev server, the worker and the API, and listens on port **8000** (or `$PORT` if the platform sets it). If any of the three stops, the container exits so the platform restarts it. Do not override the command.
- A few things are **manual**, done by the engineer (or by you only when asked): role grants for the app's identity, the Easy Auth app registration's token settings, and (recommended) keeping one replica running. They are listed in [Step 4](#step-4-manual-configuration-after-the-first-deploy).

How a request flows: Easy Auth signs the caller in → the API maps their Entra groups to a business unit and checks the request against that unit's rules (allowed patterns, environments, regions, sizes, budget) → it adds the values callers must not set (subscription, cost centre, network IDs) → it writes an audit event and a record to Azure Table Storage and starts a workflow on the Temporal server **inside the same container** → the worker fetches the Terraform pattern from its git repo at a pinned commit and runs `plan`/`apply` against the business unit's subscription, with state in a blob container. Outputs come back on the record; secrets stay in the pattern's own Key Vault and only references are returned.

Everything that matters lives **outside** the container (records, Terraform state, logs, audit events), so replicas are disposable and several can run side by side: each has its own Temporal, so a deployment's plan and apply always meet the same worker.

## Ground rules

- **Do not improvise architecture.** If something here cannot be done, stop and report what is missing. Do not substitute a workaround of your own.
- **Read before you write.** Do [Step 0](#step-0-discover-and-verify-read-only) before creating anything.
- **Never print, log or paste secrets** (private keys, tokens). Reference them by Key Vault secret name only.
- **Stop and ask the engineer** before: creating or changing role assignments or app registrations, deploying any real pattern (anything except `local-file` and `azure-identity-check`), or when a verification step fails twice.
- Do not run `uv`, tests or local Docker. Local development is deliberately skipped at work; the code is already tested (121 tests in the lab, including real Terraform, Temporal and storage-emulator runs).
- Do not change application code. The only file you edit is `patterns.yaml`.
- Report at the end using the [report format](#report-format). State plainly what was verified and what was not.

## Inputs the engineer must give you

Ask for any that are missing. Do not guess.

| Input | Notes |
| --- | --- |
| Tenant ID and the **platform** subscription ID (the one holding the state storage account) | Target subscriptions come from the business-unit mapping, one per business unit and environment. |
| State storage: resource group, account name, blob container (default `tfstate`), table name (default `deployments`; the app also creates `deploymentslogs` and `deploymentsevents` itself) | Must be reachable from the ACA environment: private endpoints **and private DNS for both `blob` and `table`**. |
| Pattern repositories: GitHub host, org, repo names, and sub-directory if the root module is not at the repo root | Runnable root modules versioned by semver tags. See the [pattern checklist](#pattern-repo-checklist). |
| GitHub access: GitHub App ID + installation ID + a Key Vault secret holding the App private key (PEM), **or** a Key Vault secret holding a read-only machine token | Stored in the Key Vault the MCP server gives the app; referenced, never copied. |
| Whether GitHub is github.com or GitHub Enterprise Server | GHES needs two extra settings (below). |
| Business-unit mapping: for each BU its Entra group IDs, cost centre, allowed patterns and regions, and per (dev) environment the subscription ID, network IDs and optional `budget_monthly`; optionally top-level `auditors` group IDs | Format: `tenants.example.yaml`; rules: `docs/tenancy.md`. Supplied as configuration (Step 3). List `local-file` and `azure-identity-check` under at least one business unit's `patterns`, or verification cannot run. |
| Egress allowlist status | See [Step 1](#step-1-egress). |
| Who will do the manual steps in Step 4, and whether you are allowed to | Role grants and app registration changes usually need someone with more rights than the MCP server has. |

## Step 0: discover and verify (read-only)

1. List the MCP server's tools and their options. Confirm how to set **environment variables** and **Key Vault secret references**, which **port** it expects the app to listen on (forgeapi uses 8000 and honours `$PORT`), and whether a **redeploy updates the same app** or recreates it. If it recreates it, the system-assigned identity changes and every role grant in Step 4 is lost on each deploy: stop and tell the engineer, because a manually attached user-assigned identity is then the only workable choice.
2. Confirm the state storage account, container and private endpoints/DNS exist.
3. Confirm the pattern repositories and tags exist and that the GitHub credential covers **every** repo involved, including modules the patterns reference.

## Step 1: egress

Outbound internet is restricted. At **run time** the app needs HTTPS to:

- the GitHub host (pattern and module fetches) and, for github.com, `codeload.github.com`;
- `registry.terraform.io`, `releases.hashicorp.com`, and `github.com` + `objects.githubusercontent.com` (provider downloads; non-HashiCorp providers such as `Azure/azapi` are served from GitHub releases);
- `login.microsoftonline.com`, `management.azure.com`, `graph.microsoft.com`;
- the storage account and Key Vault (through their private endpoints).

At **build time** the image pulls `hashicorp/terraform:1.15.9` and `temporalio/temporal:1.8.3` (Docker Hub), `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`, Debian apt packages (`git`) and PyPI packages. If the MCP server's build environment cannot reach those, stop and report: the engineer needs mirrored base images, and you should only change the `FROM` lines to the mirrors they name.

If the Terraform registry cannot be allowlisted, stop and report. The known fix (bake a provider mirror into the image) is not built yet.

## Step 2: edit the catalog

`patterns.yaml` is baked into the image. Replace the lab entries with the work pattern repos. Keep the two built-in examples; they are your verification tools.

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

## Step 3: deploy with the MCP server

Deploy the repository as one app. Leave the command alone. Settings:

| Variable | Value |
| --- | --- |
| `FORGEAPI_DATA_DIR` | `/tmp/forgeapi` (throwaway; nothing important is kept on the container's disk) |
| `FORGEAPI_DB_BACKEND` | `table` |
| `FORGEAPI_TABLE_STORAGE_ACCOUNT` | state storage account name |
| `FORGEAPI_TABLE_NAME` | `deployments` |
| `FORGEAPI_AZURE_USE_MANAGED_IDENTITY` | `true` |
| `FORGEAPI_AZURE_TENANT_ID` | tenant ID |
| `FORGEAPI_AZURE_SUBSCRIPTION_ID` | the **platform** subscription ID (where the state storage account lives). Deployments target the business unit's subscription from the mapping; state always stays here. |
| `FORGEAPI_STATE_RESOURCE_GROUP` | state storage resource group |
| `FORGEAPI_STATE_STORAGE_ACCOUNT` | state storage account name |
| `FORGEAPI_STATE_CONTAINER` | `tfstate` |
| `FORGEAPI_AUTH_MODE` | `easyauth` |
| `FORGEAPI_TENANTS_YAML` | the business-unit mapping as YAML text, **as a Key Vault secret reference** (it is multi-line and changes whenever a team is onboarded; it holds group and subscription IDs, not credentials). Onboarding is then: update the secret, restart the app. |
| GitHub App: `FORGEAPI_GITHUB_APP_ID`, `FORGEAPI_GITHUB_APP_INSTALLATION_ID`, and `FORGEAPI_GITHUB_APP_PRIVATE_KEY` **as a Key Vault secret reference** | preferred |
| …or machine token: `FORGEAPI_GITHUB_TOKEN` **as a Key Vault secret reference** | alternative |
| GHES only: `FORGEAPI_GITHUB_HOST` = `<host>`, `FORGEAPI_GITHUB_API_URL` = `https://<host>/api/v3` | omit for github.com |

**Leave unset:**

- `FORGEAPI_TEMPORAL_ADDRESS` and `FORGEAPI_TEMPORAL_NAMESPACE`. Unset means "run Temporal inside this container". (Setting the address switches the local server off; see [Moving to the AKS Temporal](#moving-to-the-aks-temporal-later).)
- `FORGEAPI_AZURE_MANAGED_IDENTITY_CLIENT_ID`. Only for a user-assigned identity; the system-assigned one needs no ID.
- `FORGEAPI_AZURE_FEDERATED_CLIENT_ID`, `FORGEAPI_AZURE_CLIENT_ID`, `FORGEAPI_AZURE_CLIENT_CERTIFICATE_PATH`: lab-only sign-in modes; the first takes precedence over managed identity.
- `FORGEAPI_DEV_GROUPS` (hands group memberships to unauthenticated local callers) and `FORGEAPI_TABLE_CONNECTION_STRING` (storage emulator, tests only).
- `FORGEAPI_TENANTS_PATH` (a baked-in mapping file; every change would need a rebuild), `FORGEAPI_ENTRA_TENANT_ID` and `FORGEAPI_ENTRA_AUDIENCE` (only for `FORGEAPI_AUTH_MODE=entra`, where the app validates tokens itself instead of trusting Easy Auth).
- `FORGEAPI_CATALOG_PATH`, `FORGEAPI_TASK_QUEUE`, `FORGEAPI_TERRAFORM_BIN`, `FORGEAPI_STALE_AFTER_SECONDS`: working defaults.

Without the mapping the API runs single-tenant with **no** placement, ownership or budgets. Do not leave it that way at work.

Why managed identity works: Terraform can only ask the VM metadata address for tokens, which Container Apps does not have. The worker runs a loopback token endpoint (`app/msi_shim.py`) backed by the Azure SDK and points Terraform at it. No app registration, federated credential or secret is involved; the identity's own role assignments apply.

## Step 4: manual configuration after the first deploy

The system-assigned identity only exists once the app does, so the first start will answer `/healthz` but cannot do anything else yet. Get the identity's **principal (object) ID** from the app, then the engineer (or you, only if asked) sets up:

1. **Roles for the app's identity**
   - `Storage Table Data Contributor` on the state storage account (covers the three tables: records, logs, audit events);
   - `Storage Blob Data Contributor` on the state container;
   - `Key Vault Secrets User` on the app's Key Vault, if the MCP server has not already granted it (needed for the secret references);
   - the deploy rights the patterns need **in every subscription in the mapping**: typically `Contributor`, plus `Role Based Access Control Administrator` if patterns assign roles;
   - Microsoft Graph application permissions `Group.ReadWrite.All` and `User.Read.All` **if** patterns create Entra groups. Granting Graph app roles to a managed identity is a directory-admin action.
   Role assignments take a few minutes to take effect; restart the app afterwards so the secret references resolve.
2. **The Easy Auth app registration** (the "custom config for the SPN used by Easy Auth")
   - **Emit group claims:** token configuration → groups claim → *Groups assigned to the application*, and assign each business unit's group (and any auditors group) to the enterprise application. forgeapi decides the business unit from these claims; this setting also avoids the too-many-groups overage. Verify with `GET /me`.
   - **Pipelines and service principals:** they must be able to get a token for this app registration and be let through Easy Auth (allowed client applications / token audience), and the service principal must be a member of a business unit's group. A caller with no recognised group gets 403 and sees nothing.
   - **Browsers:** people sign in through Easy Auth and can use the interactive API page at `/docs`.
3. **Keep one replica running (strongly recommended).** An HTTP app scales to zero after a few idle minutes, and an apply can run much longer than that while nobody is calling the API. Ask the engineer to set **minimum replicas to 1** on the app. See the next section for what happens without it.

## Interruptions: what happens when the app is stopped mid-deployment

Scale-in, a platform restart, a new revision or a crash can stop the container while Terraform is running. This is handled, and was proven in the lab by killing a container mid-apply:

- A running job beats on its record every 30 seconds. A deployment that claims to be running but has not moved for 5 minutes is marked `failed` with `interrupted: the worker stopped while this was running. Retry to continue…`, and an audit event `deployment.state interrupted` is written.
- `POST /deployments/{id}/retry` continues from the existing Terraform state. The killed run leaves its **state lock** behind (`state blob is already locked`); forgeapi releases it once with `terraform force-unlock` and carries on. This is safe because forgeapi runs one job per deployment at a time. `DELETE` recovers the same way.
- Temporal's history lives in the container and is lost with it. That is fine: nothing depends on it after a restart.

So an interruption costs a retry, not a stuck or orphaned deployment. With minimum replicas at 1 it should be rare. Without it, expect long applies to be interrupted whenever the API goes quiet.

## Step 5: verify, in this order

Call the API through whatever authenticated path Easy Auth allows. Stop at the first failure and consult [Troubleshooting](#troubleshooting).

1. `GET /healthz` → `200 {"status":"ok"}`.
2. `GET /me` → the caller's business units, the environments they may deploy to, regions, patterns and **budget position**. `"business_units": null` means the mapping is not configured (stop: see Step 3). An empty list means group claims are not reaching the app or the mapping has the wrong group IDs.
3. `GET /patterns` → only the patterns the caller's business units allow. `GET /patterns/<a real pattern>` → versions (tags) and inputs; proves GitHub access. `502` means it does not work yet.
4. `POST /deployments` `{"pattern":"local-file","environment":"<env>","inputs":{"filename":"hello.txt","content":"hi"}}`, poll `GET /deployments/<id>` until `succeeded`. Proves API → Table Storage → Temporal → worker → Terraform with no Azure sign-in involved.
5. `POST /deployments` `{"pattern":"azure-identity-check","environment":"<env>","inputs":{"note":"work identity test"}}` → `succeeded`, and **`outputs.signed_in_object_id` equals the app identity's principal (object) ID**. This pattern creates nothing; it proves Terraform signs in as the identity and can write remote state.
6. `GET /deployments/<id>/logs` returns Terraform output (not empty), and `GET /deployments/<id>/events` shows `deployment.create accepted` followed by `deployment.state succeeded`.
7. `DELETE /deployments/<id>` for both; each ends `destroyed`, and the budget position in `GET /me` returns to where it started. `GET /deployments` lists them.
8. `GET /patterns/<a real pattern>`: `placement` shows the sizes offered in the environment and their estimated monthly cost; platform-supplied inputs (environment, business unit, cost centre, network IDs, sized values) are **absent** from `inputs`; `GET /patterns/<name>/schema` returns JSON Schema. Then `POST /deployments?dry_run=true` with the engineer's inputs, `environment` and `size` → `{"valid": true}` with `injected` values and the budget impact. Creates nothing and is not audited.
9. **Only with the engineer's explicit go-ahead:** deploy one real, cheap pattern; verify the resource independently, **including that its tags carry the business unit and cost centre the caller never sent**; check `secret_references` / `withheld_outputs` if the pattern produces secrets; `PUT /deployments/<id>` with one changed input to see an in-place update; `DELETE` it. `GET /events` shows the whole history.

## Moving to the AKS Temporal later

Set `FORGEAPI_TEMPORAL_ADDRESS` (and `FORGEAPI_TEMPORAL_NAMESPACE`) to the existing service. The container then starts only the worker and the API, workflow history survives restarts, and every replica shares one queue (supported: apply checks it holds the plan for exactly this request and otherwise re-plans). Two limits today: connections that need **mTLS or an API key are not supported by the code yet**, and patterns that keep local state (only the built-in `local-file` example) need a single replica in that setup.

## API quick reference

| Call | Purpose |
| --- | --- |
| `GET /me` | the caller's business units, environments, regions, patterns, budgets |
| `GET /patterns`, `GET /patterns/{name}?version=`, `GET /patterns/{name}/schema` | catalog, inputs with allowed values and an example request, JSON Schema |
| `POST /deployments` (`?dry_run=true` to validate only) | deploy `{"pattern","environment","size"?,"version"?,"business_unit"?,"inputs"}`. `business_unit` only when the caller belongs to several; `size` when the pattern defines sizes |
| `GET /deployments`, `GET /deployments/{id}`, `GET /deployments/{id}/logs` | the caller's units' deployments; state, outputs, `secret_references`, `withheld_outputs`, error; Terraform log |
| `GET /deployments/{id}/events`, `GET /events` | audit trail (read-only) |
| `PUT /deployments/{id}` | new inputs, size and/or version, applied against existing state |
| `POST /deployments/{id}/retry` | finish a failed or interrupted deployment |
| `DELETE /deployments/{id}` | `terraform destroy`; record kept as `destroyed` |
| `GET /healthz` | liveness; needs no sign-in |

## Troubleshooting

Every entry below actually happened in the lab.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `503 deployment records are temporarily unavailable`, or `AuthorizationPermissionMismatch` in the app's logs | Table role missing or granted under ~2 minutes ago | confirm the role; wait; retry |
| `503 the audit trail is unavailable, so the request was not carried out` | same cause, hit while writing the audit event | same fix; nothing was started |
| App revision fails: `Unable to get value using Managed identity … for secret` | identity lacks `Key Vault Secrets User`, or the role is too new | grant/wait, then restart the revision |
| `ManagedIdentityAuthorizer … 169.254.169.254 … connection refused` | `FORGEAPI_AZURE_USE_MANAGED_IDENTITY` is not `true` | fix the setting |
| `signed_in_object_id` is not the app identity's principal ID | a federated, certificate or client-ID setting is present | remove the "leave unset" variables |
| The container keeps restarting; logs show `[allinone] … exited` | one of the three processes died; the supervisor stops the rest on purpose | read the lines just before it. Usual causes: a wrong setting, or storage/Key Vault unreachable |
| A deployment shows `failed` with `interrupted: the worker stopped…` | the container was stopped mid-run (scale-in, restart, new revision) | `POST …/retry`. Set minimum replicas to 1 if it keeps happening |
| `Error acquiring the state lock` as the **final** error of a deployment | the automatic unlock was already tried once and the lock is still held: something other than forgeapi holds it (a person running Terraform against that state) | find out who; never unlock by hand without knowing |
| `GET /me` shows `"business_units": null` | the mapping is not configured | set `FORGEAPI_TENANTS_YAML` (Step 3) |
| `GET /me` shows no business units for someone who should have one | Easy Auth is not passing group claims, the group is not assigned to the enterprise application, or wrong group IDs in the mapping | Step 4.2; compare object IDs |
| `422 name an environment` / `name a size; <env> offers [...]` | request lacks `environment`, or the pattern defines sizes and none was chosen | add them |
| `422 size '<x>' is not offered in <env>` with an **empty** list | the pattern's `config.yaml` sizing keys do not match the environment names | fix the pattern repo and tag a new version |
| `403 … declares no estimated cost` | the environment has a `budget_monthly` and the pattern's `config.yaml` has no `estimated_costs` for it | add estimates to the pattern (`estimated_costs: 0` if genuinely free) |
| `403 this would exceed the estimated monthly budget` | working as designed; the response gives the figures | destroy unused deployments, choose a smaller size, or raise the budget |
| `502` on `GET /patterns/{name}` or at POST | cannot reach or authenticate to GitHub | egress; the credential covers the repo **and every module repo it references**; the secret reference resolves |
| `Failed to query available provider packages` / registry timeouts in deployment logs | egress to the Terraform registry blocked | allowlist, or stop and report (provider mirror not built) |
| `withheld_outputs` is not empty | the pattern outputs a `sensitive` value instead of storing it in a vault | pattern bug: see `docs/outputs.md` |
| A poll returns a non-JSON body right after the app was updated | seen in the lab within a minute or two of a revision change; cause unconfirmed | retry the request |
| `MissingSubscriptionRegistration` for some namespace | target subscription has never used that resource type | engineer registers the provider |

## Known limits to tell the engineer about

- **The identity.** The system-assigned identity is tied to the app: if the app is ever recreated, every grant in Step 4 must be redone. Whatever identity Terraform runs as, every API caller deploys with its reach in every mapped subscription, and patterns that grant the deploying identity data access (the lab key-vault module makes it Secrets Officer on each vault it creates) let that identity read those secrets.
- **Temporal in the container** keeps history in memory. Interruptions are recovered (above) but cost a retry. There is no Temporal web UI in this setup.
- Authorization is by business unit and environment (Entra groups), with estimated-cost budgets. Budgets use the pattern authors' estimates, not Azure billing, and two simultaneous requests can overshoot slightly. No deployment counts, expiry, approvals or per-pattern-version limits.
- The audit trail is append-only through the API but the storage is not immutable, and there is no retention or export. Sensitive **inputs** are stored in clear on the deployment record; pass secrets as Key Vault references. Details: `docs/audit.md`.
- GitHub App token support is unit-tested only; it has not been run against a real App. A machine token through a Key Vault reference **has** been proven end to end.
- The Terraform provider mirror for fully closed egress is not built.
- **Never proven anywhere:** Easy Auth's identity header and group claims reaching forgeapi (`FORGEAPI_AUTH_MODE=easyauth` is unit-tested only; the lab used the app's own token validation), a system-assigned identity running Terraform (the lab proved a user-assigned one through the same code path), private endpoints, restricted egress, and this MCP server.

## Pattern repo checklist

Most behaviour callers see is defined in the pattern repositories, read at the pinned commit. For each pattern to be registered, confirm with the engineer (do not fix pattern repos yourself unless asked):

1. It is a **runnable root module** with its own `provider` blocks and `backend "azurerm" {}`, versioned with **semver tags** (`v1.2.3`). Every module it references is readable with the same GitHub credential.
2. `variable` blocks have accurate `description`s, `type`s and `validation` rules with clear `error_message`s: these are the API's user-facing documentation and validation.
3. To receive platform values it **declares variables with these exact names**: `environment`, `business_unit`, `cost_center`, `location`, plus any network keys used in the mapping (for example `private_endpoint_subnet_id`). Declared ones are filled by the platform and hidden from callers; undeclared ones are simply not passed.
4. `config.yaml` beside the root module (or at the repo root) has `description`, and where relevant `sizing.<size>.<environment>.<variable>` and `estimated_costs.<size>.<environment>` (or `.<environment>`, or one number). **Environment keys must match the mapping's environment names.** A size locks the inputs it sets. Without `estimated_costs` the pattern is refused wherever a budget exists.
5. Secrets follow `docs/outputs.md`: prefer Entra authentication and no secret; otherwise the pattern stores the secret in a Key Vault it creates and outputs the **versionless secret ID**, and grants read access itself. Never output a secret value, even marked `sensitive`.

## Report format

```
Deployed: <app name, image, identity principal ID>
Manual steps: <done by whom | still open>
Verified: <numbered Step 5 checks that passed, with deployment IDs>
Not verified / skipped: <which, and why>
Deviations from the brief: <none | list>
Open items for the engineer: <role grants, app registration, egress, minimum replicas, decisions>
```
