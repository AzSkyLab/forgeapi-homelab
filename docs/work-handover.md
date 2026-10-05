# Work handover: bring this repository to the work environment

**Current as of:** 2026-10-04, `main` at or after `57942b4` (agent-v1 operations API; default
suite 1181 passed, hosted CI passing). Audience: the AI assistant at work that takes this
repository and implements it there, working with the engineer. Paste [work-prompt.md](work-prompt.md)
to start; it points here.

The work environment already runs an **earlier ForgeAPI** built from this repository's early
Python version (most likely a `v0.5.0`–`v0.7.1` tag: the `/deployments` API on Azure Container
Apps, with Easy Auth and Azure Table Storage). This guide takes you from that version to the
current one **without disturbing what already runs**. Everything here was built and proven in a
home lab first, on emulators and a lab Entra tenant. Your job is to reproduce a known-good
shape, not to redesign it.

## 1. Read first, in this order

1. `AGENTS.md`: repository rules. They apply at work too.
2. This file, end to end, before running any command.
3. [work-deployment.md](work-deployment.md): the reference for every setting, endpoint and
   limit. Look things up there; it is long and is not meant to be read top to bottom.
4. [deploy/aks/README.md](../deploy/aks/README.md): the deployment base and its **work-day
   runbook** (§7 below follows it).
5. As needed: [agent-architecture.md](agent-architecture.md) (design and failure boundaries),
   [tenancy.md](tenancy.md) (business units, placement, budgets), [pattern-onboarding.md](pattern-onboarding.md),
   [outputs.md](outputs.md), [audit.md](audit.md), [api-versioning.md](api-versioning.md),
   [progress.md](progress.md) (evidence for every claim; newest first).

## 2. Ground rules at work

- **Do not improvise architecture.** If a step cannot be done as written, stop and report what is
  missing. Do not substitute a workaround of your own.
- **Read-only first.** Change nothing until §4 is done and the engineer has answered §6.
- **Stop and ask the engineer** before you: create or change app registrations, federated
  credentials, role assignments or Graph consent; create cloud resources; deploy any pattern
  other than the agreed verification pattern; change, migrate or delete anything belonging to
  the existing deployment; or retry a failed verification step a second time.
- **Never print, log, paste or commit** tokens, private keys, certificates, tenant/subscription
  IDs, the tenant mapping (`tenants.yaml`) or Terraform state. Refer to secrets by Key Vault
  secret or Kubernetes Secret name.
- **No local development on the work Mac** (no `uv sync`, no local test runs). The code is tested:
  hosted CI runs lint, the default suite and an image scan on every push to `main`. When you need
  one of the repository's CLIs (`python -m app.team_check`, `python -m app.pattern_check`), run it
  inside the deployed pod (`kubectl exec forgeapi-0 -c api -- python -m ...`), as the runbook shows.
- **Do not change application code** to make something work. If code seems wrong, report it with
  evidence; the fix belongs in this repository with a test.
- Never automatically retry an apply, force-unlock Terraform state, edit the ledger or replan an
  `uncertain` operation. An operator resolves uncertainty with `POST /v1/operations/{id}/reconcile`
  after reading state and provider evidence.
- **Report honestly** (§9): what you verified, how, and what you did not verify.

## 3. What changed since the version at work

| Area | Version at work (≈ v0.7.x) | This repository now |
| --- | --- | --- |
| API | `/deployments` (POST/PUT/DELETE apply immediately), `dep_` IDs | Operations API `/v1/...`: intent → saved plan → explicit execute of the exact `plan_digest`; `op_`/`res_` IDs; discovery at `/v1/agent`, OpenAPI at `/v1/openapi.json`. The old API is still in the repo as `app.legacy:app`. |
| Records | Azure Table Storage (`FORGEAPI_DB_BACKEND=table`) | SQLite ledger on **one durable local disk** shared by API and worker (`FORGEAPI_DB_BACKEND=sqlite`; with `table` every operation request fails with 503). |
| Hosting | Two Container Apps (API, engine) via the MCP server | One pod (API + worker containers, `replicas: 1`, Azure Disk) on **AKS** with Temporal already in the cluster: `deploy/aks/`. Stateless Container Apps cannot hold the ledger and saved plans. |
| Temporal | Dev server inside the engine app | External Temporal (the organisation's AKS Temporal); optional TLS/mTLS (`FORGEAPI_TEMPORAL_TLS*`). |
| Caller auth | Easy Auth in front of the app | `FORGEAPI_AUTH_MODE=entra` (bearer tokens validated by the API itself); `easyauth` still exists for a trusted proxy. |
| Worker identity to Azure | System-assigned managed identity / federation | AKS workload identity (`FORGEAPI_AZURE_USE_AKS_WORKLOAD_IDENTITY=true`); no secrets. |
| Terraform | Applied straight from the request | Plan saved and digest-locked; one apply attempt; Terraform 1.16.5 in the image; deadlines; `uncertain` needs an operator. |
| Teams | Tenant mapping file/text | The same mapping (`tenants.yaml` format unchanged), or teams in the database edited by operators (`FORGEAPI_TENANTS_SOURCE=db`, `/v1/admin/teams`, portal Teams screens). |
| New features | none of these | Developer portal (`/console`), guardrails, budgets discovery and history, labels, `input_refs`, drift checks and sweep, version upgrades, promotion, multi-cloud HA apps (`/v1/apps`), pattern contract checker, failure diagnostics, plan expiry, live Entra group re-checks. |
| Settings | — | **No setting was removed or renamed**; only new ones were added (see work-deployment.md "Settings"). Old values keep their meaning, except `FORGEAPI_DB_BACKEND`, which must be `sqlite`. |

The tenant mapping format, the pattern repository rules (root modules in their own repos, semver
tags, `patterns.yaml`), the secret-output rules and the GitHub App settings are unchanged, so the
existing mapping and pattern repos carry over.

## 4. Step 0: find the baseline of the work repository (read-only)

The work copy may contain changes made at work. Find out what they are before replacing anything.

1. In the work repository, check `git log --oneline -20` and `git tag`. If it shares history with
   this repository, run `git merge-base HEAD <this-repo>/main` and `git describe --tags` to find
   the baseline tag. If it does not share history, compare its files against this repository's
   tags `v0.5.0`, `v0.6.0`, `v0.7.0` and `v0.7.1` (`git diff --stat v0.7.1 -- app/ deploy/ docs/`
   from a checkout of this repository, with the work files copied into a scratch directory), and
   take the closest tag.
2. List every change made at work since that baseline and sort each one into:
   - **configuration** (`patterns.yaml`, tenant mapping, environment values, manifests):
     carry it over as configuration, never as a code change;
   - **a fix**: check whether this repository already contains it (search `docs/progress.md`
     and the code); if not, report it so it can be ported here with a test;
   - **a feature**: report it; the engineer decides whether it is ported here.
3. Inventory the running deployment without changing it: app names and image tags, settings
   **names** (not secret values), the Table Storage account and tables, the Terraform state
   container, the patterns and versions in use, the number of live deployments per business unit,
   and the Key Vault secret names it references.

Report this inventory before going further.

## 5. Strategy: run side by side, then move teams over

Recommended (and the only path proven in the lab):

- **Leave the existing deployment exactly as it is.** It keeps serving and managing everything it
  already created.
- **Deploy the new version separately** on AKS, with its own disk, Temporal namespace or task
  queue, and ingress. New work goes to the new API.
- **Existing deployments stay on the old API** until they are destroyed there or until an adoption
  path exists. The new API cannot see, change or count them. Two consequences: budgets are tracked
  separately (the new API counts only its own resources, so set new budgets with the old spend in
  mind), and the old audit trail stays in Table Storage.
- Retire the old deployment only when it manages nothing the teams still need, and only when the
  engineer decides to.

**Adoption of existing deployments is not built.** Do not improvise it. Facts for a future design:
both versions key Terraform state as `deployments/<id>.tfstate` in the same azurerm backend
settings, so an adoption step could register a new-API resource whose state key is the old one,
then run a read-only plan or drift check that must show no changes before anything executes. That
needs code, tests and a lab proof in this repository first. Ask the engineer if it is wanted.

## 6. Decisions the engineer makes (ask; record the answers)

| Decision | Recommendation |
| --- | --- |
| Hosting target | `deploy/aks` on the AKS cluster that already runs Temporal. Container Apps is not supported for this version. |
| Temporal namespace/queue | A namespace registered for ForgeAPI by the Temporal owners (the ConfigMap ships `default`), plus its own `FORGEAPI_TASK_QUEUE`; ask them for address, port and TLS material (runbook step 3a). |
| Terraform state | Reuse the existing state account and container (new keys are `res_…`, old ones `dep_…`, so they never collide) or a new one; set `FORGEAPI_STATE_*` and `FORGEAPI_AZURE_SUBSCRIPTION_ID` (the state account's subscription). |
| Ingress | How callers reach the API (internal ingress controller namespace in the NetworkPolicy, TLS there), or port-forward for verification only (runbook step 3b). |
| Secret delivery | Tenant mapping and GitHub App key from Key Vault through the AKS secrets-provider add-on, never as local files. |
| Temporal TLS | On if the cluster's Temporal requires it (mount PEMs; see the runbook). |
| Caller app registration | Reuse the existing ForgeAPI API registration only if it can issue **v2** tokens with `groups` (`requestedAccessTokenVersion: 2`, Application ID URI, Azure CLI pre-authorized); otherwise create one (runbook step 1). |
| Worker identity | New user-assigned identity with a federated credential for `system:serviceaccount:<ns>:forgeapi`; same RBAC as the old engine identity on the mapped subscriptions and state account. |
| Teams source | Start with `file` (the existing mapping as the `forgeapi-tenants` Secret); move to `db` later with `POST /v1/admin/teams/import`. |
| Operators | Entra group IDs for `FORGEAPI_OPERATOR_GROUPS` (db mode) or `operators` in the mapping (file mode). |
| Live group re-checks | `FORGEAPI_LIVE_GROUP_CHECKS=true` only after Graph `GroupMember.Read.All` is consented for the worker identity. |
| Drift sweep | `FORGEAPI_DRIFT_SWEEP_MINUTES=360` after the first week. |
| Plan expiry | `FORGEAPI_PLAN_MAX_AGE_HOURS=24` (already in the ConfigMap). |
| Image source | Publish by pushing a `v*` tag here (GHCR, with SBOM/provenance), or import that build into the work registry. |
| Verification pattern | The cheapest real pattern in a dev subscription (for example `resource-group`), plus `azure-identity-check`. |
| Existing deployments | Side by side (§5). Adoption only as a separate, approved milestone. |

## 7. Implementation plan (each phase ends at a gate)

| Phase | Do | Gate: stop and report when |
| --- | --- | --- |
| 0 | Baseline and inventory (§4). | Inventory and the list of work-only changes are reported. |
| 1 | Get the engineer's decisions (§6). Prepare configuration only: the work `patterns.yaml` (existing patterns plus the verification pattern; keep each entry's `path:`) for the `forgeapi-catalog` ConfigMap, the tenant mapping and GitHub App key in Key Vault, ConfigMap values. | The configuration is reviewed by the engineer. |
| 2 | Prerequisites (runbook steps 1–2): caller app registration, worker identity, federated credential, RBAC, optional Graph consent, network reachability of the state storage account and Key Vault from AKS (private endpoints and DNS for `blob`). **Engineer performs or approves each change.** | Every prerequisite is confirmed, by reading it back. |
| 3 | Image (runbook step 3); put the digest in `kustomization.yaml`. | The image digest is recorded. |
| 4 | Apply `deploy/aks` with placeholders filled (runbook steps 4–5), in a new namespace. The old Container Apps stay untouched. Then run the in-pod checks (runbook step 5): `team_check` on the mapping and `GET /v1/patterns/{name}/check` for every pattern. | The pod has every container Ready, 0 restarts, `/readyz` 200; checks report 0 errors (warnings explained). |
| 5 | Verify in the runbook's order (step 6): 401s, group scoping, a real plan → exact-digest apply → readback in Azure → drift check → destroy → readback absent, and no tokens in the logs. | Every check passes, with operation IDs and readbacks recorded. Any failure: stop, do not retry twice. |
| 6 | Onboard: register the remaining patterns (contract checks), confirm each team's discovery (`/v1/agent`), give agents and people the API URL and `/console`. | The engineer confirms the teams are ready. |
| 7 | Later and separate: move teams over, decide on adoption (§5), retire the old deployment. | Engineer decision only. |

## 8. What is proven, and where

| Proven in the home lab | How |
| --- | --- |
| Operations API end to end on Azure/AWS/GCP **emulators** (Floci) | Real API → Temporal → worker → Terraform → emulator, with independent readbacks (`tests/test_floci*.py`) |
| `deploy/aks` base | kind rehearsals: read-only root filesystems, NetworkPolicy enforced, a saved plan survives a pod restart, emulator clouds |
| Entra caller auth | Real lab-tenant tokens, both on a host and through the pod with the ConfigMap as shipped |
| Live group re-checks | Real Microsoft Graph |
| AKS workload identity | Provider and backend paths against the emulator, Terraform 1.15.9 and 1.16.5 |
| Temporal TLS/mTLS | Real handshake against a real Temporal server |
| Hosted CI | GitHub Actions `Check` passes on `main` |

**Not proven, only provable at work:** the workload-identity token exchange with real Entra; Azure
Disk; real Azure RBAC and Policy; the work Temporal and its certificates; private networking to
the state account; Graph consent for the worker identity; a published image; real patterns
against real subscriptions. Treat each of these as unverified until you have read it back.

## 9. Report format (at every gate and at the end)

- Revision of this repository and image digest used.
- What you did, as a list of commands or portal actions (no secret values).
- Evidence: operation IDs, states, readbacks (`az` output summarised), pod status.
- Decisions taken and by whom.
- Everything unverified or skipped, said plainly. Never call a skipped or failed check passed.
- Proposed next step.

When something here turns out to be wrong at work, report it so this file, work-deployment.md and
the code are corrected in this repository, with a test where behaviour changes.
