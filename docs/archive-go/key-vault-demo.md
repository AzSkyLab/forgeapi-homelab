# One empty Key Vault, through the API

This is a separately authorized **local, human-operated infrastructure spike**, not the general Terraform platform or completion of M1/M4. The API and Temporal remain local. The original compute demonstration still uses synthetic workloads.

## What happens

```text
Entra caller → POST /deployments → PostgreSQL acceptance + outbox
                                  ↓
                     Native host worker → Temporal plan workflow
                                  ↓
                     pinned Terraform → saved, guarded plan
                                  ↓
Caller reviews → POST /deployments/{id}/approvals (exact plan digest)
                                  ↓
                     Temporal apply workflow → Terraform → Azure ARM
                                  ↓
                     ARM verification → API status: succeeded
```

The target is one configured subscription/RG/name/region, one explicitly authorized API caller and a separately bound infrastructure executor. Current caller policy, target, pattern digest and executor/certificate binding are rechecked before Terraform. Browser authentication is for the API; the [dedicated lab service principal](terraform-identity.md) authorizes ARM with a seven-day certificate. No client password, human CLI fallback, host credential mounts or API-token-to-ARM forwarding. The original successful create used the earlier human-CLI runner; the current certificate identity has separately passed real read-only ARM/Terraform verification.

The trusted runner executes only [this embedded pattern](../patterns/key-vault/main.tf), not arbitrary repositories, module URLs, commands or submitted HCL. Terraform **1.15.9**, AzAPI **2.11.0**, API version **2025-05-01**; provider checksums include Linux amd64 and both Mac architectures. AzAPI uses ARM only, avoiding Key Vault data-plane reads/writes. [Microsoft resource reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.keyvault/2025-05-01/vaults), [AzAPI provider authentication](https://registry.terraform.io/providers/Azure/azapi/2.11.0/docs).

## Resource and cost boundaries

- One **empty Standard** vault in the existing approved RG; RBAC authorization enabled, no role assignments/access policies, secrets, keys or certificates.
- Public network access disabled, trusted-service bypass off; no private endpoint, networking, diagnostics, storage backend or hosting resources.
- Seven-day soft delete; purge protection not enabled for this disposable demonstration. Its request field is **omitted**: ARM rejects explicit `enablePurgeProtection=false`, even on creation. `prevent_destroy` is set. There is no destroy/purge endpoint and success retains the vault. [Microsoft property contract](https://learn.microsoft.com/en-us/dotnet/api/azure.resourcemanager.keyvault.models.keyvaultproperties.enablepurgeprotection).
- No Defender enablement/configuration is authorized. The engineer instructed proceeding without further Defender checks; do not repeat those checks or change existing subscription security settings. Existing subscription coverage/billing is unverified, not a guarantee of zero cost.
- Standard vault pricing has no fixed vault rental line; charges apply to data operations. We expect an unused empty vault and ARM-only operations to have no direct Key Vault usage charge, **not a guaranteed zero subscription bill**. Existing subscription policies/services can add costs outside this plan. Do not add data operations or paid dependencies under this authorization. [Azure pricing](https://azure.microsoft.com/en-us/pricing/details/key-vault/).

## Run locally / repeat on another approved machine

1. Complete normal [Entra setup](work-setup.md) and the separately approved [lab executor bootstrap](terraform-identity.md). Install **Terraform 1.15.9** on the host; Docker builds the native Go helpers, so host Go is optional. Azure CLI is needed by the bootstrap administrator, not the runtime worker. Confirm the target RG already exists and `Microsoft.KeyVault` is registered; the runner does not register providers/create RGs.
2. Set up **ignored** `config/deployment.local.json`, using only the newly approved environment's identifiers:

   ```json
   {
     "tenant_id": "<approved-tenant-UUID>",
     "subscription_id": "<approved-subscription-UUID>",
     "resource_group_id": "/subscriptions/<approved-subscription-UUID>/resourceGroups/<existing-RG>",
     "location": "<RG-region>",
     "name": "<approved-globally-unique-vault-name>",
     "owner": "entra:<approved-tenant-UUID>:<approved-human-object-UUID>",
     "pattern_digest": "<digest-from-command-below>",
     "executor": {
       "mode": "lab_certificate",
       "client_id": "<dedicated-executor-client-UUID>",
       "principal_id": "<dedicated-executor-principal-UUID>",
       "certificate_sha256": "<public-certificate-fingerprint-from-executor-config>"
     }
   }
   ```

   Obtain the digest with `FORGE_LOCAL_HELPER=keyvault-worker sh scripts/demo.sh -print-pattern-digest`. Configuration contains public binding identifiers, not credentials: it must be readable by the non-root API container (for example mode 0644, directory 0755). Keep it out of Git. State/plan and certificate directories, by contrast, are restricted to the host owner. The human needs a current developer grant in `config/grants.local.json`; the executor needs the Azure target permissions. Do not give callers Azure roles or automatically create broader role assignments.
3. `make up` upgrades the API/compute binaries with writers stopped. In a separate terminal run `make keyvault-worker`; it connects only to local PostgreSQL/Temporal and uses the explicit certificate executor. Do not run it in Docker or mount host credentials into containers. For the already-created vault, use `make keyvault-identity-check` instead; no new provisioning is needed.
4. `make keyvault-demo` signs into the API, requests a plan and prints its target/digest. Review the exact saved-plan change set before typing `APPLY <plan-digest>`. Approval expires after 30 minutes. The CLI polls the API to the verified result. To print the sign-in link manually: `sh scripts/demo.sh -print-login-url -key-vault`.
5. Stop the native worker with Ctrl-C **after** the operation reaches a terminal state. The vault and `.local/deployments/<deployment-id>/` remain. Do not delete state or submit a new target to recover an interrupted apply.

## Show the completed demonstration again

The 2026-09-07 live run succeeded: deployment `dep_000b66b69f5db372949a27ae80b806e4`, vault `kv-forgeapi-0907-a8c2`. Independent ARM readback matched the approved settings. With the same local configuration, database and human identity, this command replays that successful request and prints its existing result without a new plan/apply; the native Key Vault worker is not needed for this completed-result replay:

```sh
sh scripts/demo.sh -print-login-url -key-vault -key-vault-request-key key-vault-corrected-20260907-01
```

This machine's default request key belongs to the earlier rejected attempt, not the successful one. Keep both records/workspaces. The vault has public data-plane access disabled: this demonstrates governed resource creation, not reading/writing secrets.

## Narrow API contract

All routes reuse Entra/local-boundary middleware. Only the configured human with a current developer grant can use them. Unknown fields/target overrides are rejected; the original execution OpenAPI is unchanged. These four spike routes are documented here, not claimed as part of its existing conformance coverage.

| Route | Request / response |
| --- | --- |
| `GET /deployment-patterns` | One pattern, approved target/digest and create-only capabilities |
| `POST /deployments` | `{"pattern_id":"azure-key-vault-v1"}` plus `Idempotency-Key`; 202 immutable acceptance receipt and Location |
| `GET /deployments/{id}` | Current state, timestamped transitions, target, plan digest/expiry and sanitized verified outputs |
| `POST /deployments/{id}/approvals` | `{"plan_digest":"sha256:..."}` plus `Idempotency-Key`; 202 stable acknowledgement of that exact plan; duplicate approval is naturally idempotent |

States: `accepted → planning → planned → apply_queued → applying → succeeded`; plan failure ends `failed`, failed/uncertain apply ends `recovery_required`. Repeated create requests with the same owner/key/body replay the original receipt. Conflicting input or another record for the same case-insensitive resource ID returns 409. Wrong owner cannot read or approve another deployment. Changed/expired plans return 409. The demo's stable create key makes it a **single-deployment** walkthrough, not a bulk vault creator.

## Recovery and limitations

Acceptances/approvals and their dispatch intents commit atomically. Each phase has a stable Temporal ID. Terraform work is an activity, not workflow I/O. Apply gets **one attempt**: lost acknowledgement, worker termination or uncertain state never causes an automatic second apply, destroy, import or force-unlock. Reconciliation marks unfinished projections for inspection after the phase workflow closes. No cancellation endpoint is provided for this spike; cancellation is not rollback.

Local state, saved plans and provider metadata stay in `.local/deployments/<id>` (0700 directory; raw contents never returned by the API). Do not print state/plan JSON into meeting logs. The API returns only known target fields, a plan digest, fixed error codes and verified resource ID/URI. Retain state even if Azure succeeded but the state/DB acknowledgement failed. Before any recovery, stop competing writers and inspect the original process, Temporal history, local state and ARM resource; recovery mutation needs explicit review. Migration 005 is additive; rolling back the API does not roll back Terraform or remove the Azure vault. Prefer a forward fix.

The original pre-creation rejection was recovered through the operator-only `-confirm-rejection` path, preserving its receipt/history and recording `rejected_no_effect` after checking failed Temporal history, empty unlocked state, unchanged plan, ARM absence and exact Azure rejection evidence. That historical recovery is complete. After the identity transition, the old human-bound attempt cannot be rebound to a different executor; do not rerun recovery against the existing vault. Recovery reads now use the configured executor, not the administrator's CLI, and fail closed if activity-log permissions are unavailable. No extra log role was granted.

This is not a hardened untrusted-worker sandbox: anyone controlling the local certificate or worker can exercise its RG-scoped vault-management authority outside the one-create application guard. No automatic retention/credential rotation, managed-identity integration, remote encrypted/locked backend, credential federation, multi-user approvals, recurring drift management, import/update/destroy, enterprise policy/cost guarantees, full crash-injection matrix or generic private-repo checkout is claimed. After this proof, the next repo integration is an approved commit-pinned bundle from an existing pattern repo, with its provider/module lock, input schema, target binding and reviewed plan. Do not replace the fixed pattern with an arbitrary Git URL/shell endpoint.

Tests: `make check` / `make test-docker` cover guard/auth/environment/workflow behavior; `make test-integration` covers concurrent acceptance, resource ownership, stable replay, digest/target/expiry approvals and atomic outbox writes. `make test-core` also retains the original compute crash/replay gates. Terraform `validate` is not live evidence; only the real API → Terraform → ARM result proves deployment.
