# Terraform execution identity — short version

The **human calls the API**. The **worker's service identity calls Azure**. Temporal stores orchestration state, not credentials. The worker checks both the caller's current application grant and the executor bound to the accepted request/saved plan. A developer does not need Azure provisioning rights to submit a permitted API request.

## Home lab: implemented and verified

- Dedicated app/service principal: **ForgeAPI Lab Terraform**. Its only requested Azure role is **Key Vault Contributor**, scoped to the test RG. No Graph permissions, API-caller permissions, tenant-wide consent or client password.
- Explicitly approved exception: a seven-day, self-signed RSA certificate. Its private key is unencrypted on disk, protected by a 0700 host directory and 0600 file. It is never uploaded to Entra, copied into Docker, passed to Temporal, or included in state/API responses. Only the public certificate is registered.
- Worker ARM calls use the existing MSAL certificate flow with an in-memory token cache; Terraform uses the same certificate/client ID. CLI, managed-identity, OIDC and environment-secret fallbacks are disabled for this lab path. Invalid/missing/expired credentials stop execution.
- The configured client ID, principal ID and certificate fingerprint are recorded with new intent. A changed executor/certificate cannot approve or execute an old saved plan. Original completed records/receipts/plans/state are retained unchanged; they are not converted into certificate-authenticated deployment evidence.

**Current certificate expiry:** September 14, 2026, 21:18:36 EDT (September 15, 01:18:36 UTC). It stops authenticating then; the role assignment/application do not automatically disappear. Rotation/revocation is an administrator task, not an automatic fallback.

Run the safe live check:

```sh
make keyvault-identity-check
```

It uses the actual service principal for an ARM GET and a Terraform **data-source-only plan** against the existing vault. No apply, import, new vault, data-plane access or existing-state update. A separate restricted `identity-check-*` workspace retains the read-only plan. This check passed live; a new vault create under the service principal has **not** been performed.

## Repeat only in an approved lab

An administrator needs Node, OpenSSL and an Azure CLI login to the confirmed tenant with permission to create the dedicated registration and assign the RG role. The API caller's normal Entra setup remains separate.

```sh
node scripts/setup-lab-executor.mjs \
  --tenant <approved-tenant-UUID> \
  --subscription <approved-subscription-UUID> \
  --resource-group <existing-approved-RG>
```

The script creates one certificate, registration, service principal and RG role; a repeat verifies/reuses recorded objects without rotating credentials or making duplicates. An uncertain Azure write leaves a pending operation in the ignored journal and stops retries for inspection. It does not search for or repurpose existing app registrations.

Ignored local files:

- `.local/lab-executor/client.pem`: private key plus certificate; do not display/share/copy to the work environment.
- `.local/lab-executor/setup.json`: bootstrap IDs and pending-operation journal.
- `config/terraform-executor.local.json`: worker-only identity configuration and certificate path. Contains no key/token; keep it private to the host.
- `config/deployment.local.json`: public target and `executor` binding; copy only `mode`, `client_id`, `principal_id`, `certificate_sha256` from the executor configuration. Obtain the current bundle digest with `FORGE_LOCAL_HELPER=keyvault-worker sh scripts/demo.sh -print-pattern-digest`. Never put the certificate path/key in accepted intent.

After editing the target, use `make up` to upgrade local API/compute binaries while preserving data. `make keyvault-worker` runs the certificate-authenticated infrastructure worker on the native host; it is not automatically started by Compose. The infrastructure worker is currently stopped because the approved vault task is complete. The read-only check does not require it running.

Do not delete setup journals or old deployment state to rotate. Renewing the certificate requires an explicitly reviewed public-key update and matching local fingerprint/configuration; pending plans need fresh review. Rollback must not restore human-CLI provisioning. Revoke the dedicated public certificate or role through an administrator if the lab credential is exposed.

## At work: managed identity remains the next integration

The intended executor is the **ACA worker's system-assigned managed identity**, or a UAMI if an identity independent of app lifetime is needed. Give the worker's identity the required target-RG role—not the API caller or API frontend. Platform administrators bootstrap roles; developers must not have unrestricted deployment/console control of the privileged worker. Anyone who can run arbitrary code there can use its identity. [ACA managed identities](https://learn.microsoft.com/en-us/azure/container-apps/managed-identity).

The home-lab certificate is **not** the production isolation boundary: whoever controls its private key can call Azure directly. Prefer managed identity for Azure-hosted execution and WIF for a trusted external runner. [Microsoft credential guidance](https://learn.microsoft.com/en-us/entra/identity-platform/security-best-practices-for-app-registration).

The UAMI and its RG role already exist in the lab but are not used by the certificate path. ACA managed-identity token acquisition, hosted worker connectivity and a non-human create/apply remain unverified. The current worker deliberately accepts only `lab_certificate`; do not claim that switching an environment variable enables production managed identity. Implement/test that explicit adapter and hosted connectivity as the next separately scoped integration, without a certificate or CLI fallback.
