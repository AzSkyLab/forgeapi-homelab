# Hosting plan: Azure Container Apps, free when idle

**Goal:** run forgeapi in Azure the way work would (managed identity, no secrets in config, API with zero Azure rights, Entra on the front door, remote state) for roughly $0 while idle. The lab subscription is Pay-As-You-Go, so the 12-month free offers do not apply; the design leans on the Container Apps monthly free grant and scale-to-zero instead.

The same layout is a candidate for work, where workers may run on ACA against the existing Temporal service. Lab-only shortcuts are marked **(lab)**.

## Shape

| Piece | Hosting | Identity and rights |
| --- | --- | --- |
| `api` | Container App, external ingress, min replicas 0 (wakes on HTTP), `FORGEAPI_AUTH_MODE=entra` | system-assigned MI: **Storage Table Data Contributor on the deployments table only**. No ARM rights. |
| `worker` | Container App, no ingress, min replicas 0 **(lab)** / ≥1 (work) | system-assigned MI: Contributor + RBAC Administrator on the target scope, Storage Blob Data Contributor on `tfstate`, Table Data Contributor on the deployments table, Graph `Group.ReadWrite.All` + `User.Read.All` |
| `temporal` | Container App running the dev server, internal ingress :7233, min replicas 0 **(lab)**. At work: the existing Temporal service; this app does not exist. | none |
| Deployment records | Azure Table Storage in the existing state storage account | Entra auth, no keys |
| Terraform state | existing `tfstate` container, one blob per deployment | Entra auth, no keys |
| Image | GitHub Container Registry (free). ACR Basic is ~$5/month. | **(lab)** public GHCR package, so no pull credential exists at all (this repo is already public and the image holds no secrets); at work, the approved registry with MI pull |
| Pattern repos | GitHub App installation token. **(lab)** interim: a read-only token in a Key Vault secret, referenced by the apps through their managed identities; the value never enters Terraform state or the API. | read-only on pattern repos |

`up`/`down` scripts set worker and Temporal replicas to 1 or 0. A few hours a week stays inside the free grant (~180k vCPU-s, ~360k GiB-s per month). Always-on worker + Temporal would cost a few dollars a month.

## Why Table Storage, not Postgres

One small key-value table, read by ID. The cheapest Postgres Flexible Server is ~$13/month and cannot scale to zero; Table Storage costs fractions of a cent, lives in the account we already have, and uses the same managed identity (no connection string). SQLite stays the default for local runs. If work needs queries across deployments (by owner, by pattern), Postgres becomes the right call; the store sits behind three functions (`create`, `get`, `update`), so the swap stays small.

## Build order

1. ✅ **Table Storage store** behind `app/db.py` (`FORGEAPI_DB_BACKEND=sqlite|table`). Same contract tests run against both. *Local; tested against the Azurite emulator.*
2. ✅ **Managed identity for Terraform**: `FORGEAPI_AZURE_USE_MANAGED_IDENTITY=true` hands `ARM_USE_MSI` to the provider and backend instead of the certificate; the Table store uses the same identity choice. *Local: settings and env mapping only. Cannot be proven off-Azure.*
3. **GitHub App token** for pattern repos (replaces `gh auth token`). Needs the engineer to create the App.
4. ✅ written, ⏳ not applied: **`AzSkyLab/terraform-pattern-forgeapi-host`** (v0.1.0), registered in `patterns.yaml`, so the platform is deployed through the API like any other pattern. Applying it creates the first hosted resources and needs explicit approval. Image comes from `.github/workflows/image.yml` (GHCR, on `v*` tags or manual run); the package must be made public once.
5. ✅ `scripts/aca.sh up|down|status <resource-group>` (untested against Azure).

## Risks to prove first once hosted

- **Terraform + ACA managed identity.** Container Apps exposes identity through `IDENTITY_ENDPOINT`/`IDENTITY_HEADER`, not the VM metadata address. The azurerm/azuread providers and the azurerm backend must all obtain tokens that way. If any cannot, fallback is a federated credential on the app registration that trusts the worker's managed identity (still no secret). This is the first thing to test, with the `resource-group` pattern.
- **Cold start vs. Temporal connect.** The API connects lazily, so a sleeping Temporal gives a clean 503 and a failed record, not a hang. `up` must run before demos.
- **In-memory Temporal history (lab).** Stopping Temporal mid-deployment strands that workflow; the deployment stays in its last state and `retry` recovers it because state is remote.
- **Azure Files is not used.** SQLite on SMB has locking problems; that is the reason for Table Storage.

## Not simulated

Work's Temporal on AKS (persistence, auth, namespaces), private networking/VNet-integrated environment, multi-replica workers, the approved registry and gateway.
