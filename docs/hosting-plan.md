# Hosting plan: Azure Container Apps, free when idle

**Goal:** run forgeapi in Azure the way work would (managed identity, no secrets in config, API with zero Azure rights, Entra on the front door, remote state) for roughly $0 while idle. The lab subscription is Pay-As-You-Go, so the 12-month free offers do not apply; the design leans on the Container Apps monthly free grant and scale-to-zero instead.

The same layout is a candidate for work, where workers may run on ACA against the existing Temporal service. Lab-only shortcuts are marked **(lab)**.

## Shape

| Piece | Hosting | Identity and rights |
| --- | --- | --- |
| `api` | Container App, external ingress, min replicas 0 (wakes on HTTP), `FORGEAPI_AUTH_MODE=entra` | system-assigned MI: **Storage Table Data Contributor on the deployments table only**. No ARM rights. |
| `worker` | Container App, no ingress, min replicas 0 **(lab)** / ≥1 (work) | **user-assigned** MI with Table Data Contributor only. Terraform signs in as the Terraform app registration by presenting the MI's token as a federated credential (`ARM_USE_OIDC`); that app holds the deploy rights, state access and Graph permissions. |
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

- ✅ **Resolved — Terraform + ACA managed identity.** `ARM_USE_MSI` fails in Container Apps (`169.254.169.254: connection refused`); Terraform only knows the VM metadata address. Fix in use: the worker gets a managed-identity token with the Azure SDK (works) and Terraform uses it as a federated credential for the Terraform app registration. Requires a user-assigned identity and a one-time `az ad app federated-credential create`. Verified end to end.
- **Cold start vs. Temporal connect.** The API connects lazily, so a sleeping Temporal gives a clean 503 and a failed record, not a hang. `up` must run before demos.
- **In-memory Temporal history (lab).** Stopping Temporal mid-deployment strands that workflow; the deployment stays in its last state and `retry` recovers it because state is remote.
- **Azure Files is not used.** SQLite on SMB has locking problems; that is the reason for Table Storage.

## Not simulated

Work's Temporal on AKS (persistence, auth, namespaces), private networking/VNet-integrated environment, multi-replica workers, the approved registry and gateway.

## Future option: pattern artifacts instead of GitHub access (not built)

**Problem it solves:** at work nobody on the team may be allowed to create or hold a GitHub credential. Today the worker and API fetch patterns from GitHub at run time, which needs a token (lab: a fine-grained read-only token in Key Vault; usual enterprise answer: a GitHub App owned by the platform team). This option removes GitHub from the runtime entirely, so **no key exists anywhere**.

**Idea:** publish each pattern release to Azure storage; forgeapi reads patterns from there with its managed identity.

1. **In each pattern repo**, a GitHub Actions workflow on `v*` tags:
   - `terraform init -backend=false` so every nested module is downloaded into `.terraform/modules` (the package is then self-contained; the worker never needs GitHub, not even for modules);
   - optionally scan/validate here, before anything is published;
   - tar the root module (plus `config.yaml`) and upload it as `<pattern>/<tag>.tar.gz` to a storage container;
   - sign in to Azure with **GitHub OIDC federation** (`azure/login` with a federated credential on an app registration or user-assigned identity that has Storage Blob Data Contributor on that container). Same kind of trust as the hosted worker uses, in the other direction. No secret in GitHub either.
2. **In forgeapi**, a second catalog source type next to `repo:` and `local:`, e.g.
   ```yaml
   key-vault:
     artifacts: https://<account>.blob.core.windows.net/patterns/key-vault
   ```
   - versions = blob names (semver sorted), replacing `git ls-remote --tags`;
   - pin by blob **content hash/ETag** instead of commit SHA, so a re-uploaded tag cannot change what an accepted deployment runs; make the container immutable (version-level WORM) if available;
   - fetch = download + extract into the workspace (Python, `app/azure_identity.credential()`), then the existing `terraform init` / plan / apply. `-from-module` is not needed for this source type;
   - `variables()` and `config()` read from the extracted package exactly as they do from a git checkout, so discovery, JSON Schema, dry run, PUT, retry and DELETE are unchanged.
3. **Rights:** API and worker identities get Storage Blob Data **Reader** on the patterns container. Nothing else changes.

**Why it is attractive beyond "no key":** releases are immutable and can be gated before publication; deployments do not depend on GitHub being reachable; provider/module supply chain is fixed at release time rather than resolved at deploy time.

**Costs / things to decide:** one workflow file per pattern repo (~25 at work; a reusable workflow keeps it to a few lines each); who owns the publishing identity; whether the package should also vendor providers (bigger, fully offline) or keep using the registry; retention of old versions. Estimated forgeapi work: about a day including tests against Azurite.

**Alternatives considered:** GitHub App installation token (needs org admins once; a private key exists but lives in their vault); mirroring pattern repos to Azure DevOps Repos, which accepts Entra tokens from managed identities (only if work uses Azure DevOps). "Internal" repo visibility does not help: it still requires authentication.
