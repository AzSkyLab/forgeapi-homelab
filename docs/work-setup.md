# Tomorrow: give this to the coding assistant

**Outcome:** real Entra sign-in → local Docker API → simulated execution completes. No paid Azure resource, API key or client secret. Do not claim success from compilation, `/healthz` or synthetic-token tests alone.

**Proven 2026-09-07 on Linux:** this setup created the identity objects, a real browser sign-in completed, and the authenticated walkthrough exited successfully. Setup rerun reused the same objects. Mac execution and that tenant's permissions/consent still need to be verified tomorrow.

## Copy/paste instruction

> Set up this repository on this Mac using the currently authenticated Azure CLI tenant. Read AGENTS.md and this file; do not reread the design folder. Run the setup below, then complete a real Entra-authenticated walkthrough. Keep all real IDs/configuration in ignored local files. Do not create paid Azure resources, client secrets, broad Graph permissions, tenant-wide consent or security-policy exceptions. If sign-in needs the engineer's browser/MFA, ask them to complete that step; never request their password/token. Stop after real proof and record the results in docs/progress.md.

## Run these steps

1. Prerequisites: approved Docker Desktop (running), Azure CLI (signed in as the engineer), and Node.js 22+ **on the Mac**. Node is used only for this dependency-free setup script, not the API. Host Go is optional. Check `docker compose version`, `docker info`, `az account show --query tenantId -o tsv`, and `node --version`. If an approved tool/login is missing, resolve that prerequisite without changing tenant policy.
2. Show the current tenant to the engineer. Use that **work tenant**, not IDs from another machine. Then run:

   ```sh
   node scripts/setup-entra.mjs --tenant <confirmed-tenant-UUID>
   docker compose up --build -d --wait
   sh scripts/demo.sh
   ```

3. The engineer signs in through the browser as the same account used by Azure CLI. The helper uses Microsoft's PKCE flow with a localhost callback; tokens remain in memory. Consent should concern only the dedicated client's access to the ForgeAPI API, plus ordinary sign-in/profile/session scopes. If admin approval is required, stop and request that narrow approval—do not grant tenant-wide Graph access or create a secret.
4. Require the helper's final **`PASS: local walkthrough complete`**. It checks real-token API access, submission/replay/conflict, missing-token/former-demo-header denial, workflow completion, events/logs/results, artifact integrity, cancellation and timeout. Then run:

   ```sh
   make test-docker
   make test-integration
   ```

   Or rehearse the whole sequence with `sh scripts/verify-local.sh` after setup.

## What the setup creates

- **ForgeAPI Local API:** single tenant, v2 API access tokens, URI `api://<API-client-ID>`, delegated scope `executions.access`.
- **ForgeAPI Local Client:** single tenant, native redirect `http://localhost`, only that delegated API permission. No password/device-code fallback or implicit grant.
- Their two service principals. No app credentials, resource groups, ARM roles, hosted services or admin-consent grant.
- Ignored `config/entra.local.json`: object/client/service-principal IDs and resumable setup journal.
- Ignored `.env`: tenant/API/native-client IDs plus a newly generated **internal cursor integrity key**, not an Entra client secret. Never print its contents.
- Ignored `config/grants.local.json`: the signed-in engineer gets `developer` access to synthetic `software-factory/development` executions.

Successful reruns verify/reuse the journal's exact objects and preserve the cursor key and grant file. If a create's result is uncertain, a same-name collision appears, local configuration lacks a journal, or a journal belongs to another tenant/user, **stop and inspect the specific objects**. Do not loop creates, delete registrations, overwrite config or adopt unrelated apps. A setup permission error is not permission to grant yourself administrator roles.

## Second engineer / boss

Reuse the approved work-tenant API/native-client registrations. On their Mac, populate `.env` from `.env.example` with those three IDs and generate a **different** local cursor key. Populate `config/grants.local.json` from its example with their tenant user object ID, not email or app-registration object ID. See [the short manual configuration steps](entra-local.md#local-configuration). They sign in themselves. Do not share tokens, cursor keys or Azure CLI caches; no second pair of registrations is needed.

Each Mac runs its own Docker stack/database. To prove cross-user denial, both users must call the same approved local test instance under controlled access; separate local databases do not prove shared-instance isolation. Do not expose the API through a tunnel just to make this test convenient.

## Evidence and stopping point

Record actual connected results, an execution ID and test commands—not token contents. Mac execution, second-user isolation and workload identity tests must each be marked pending unless exercised. Managed identity/WIF are for future workload callers; they are not needed for human local sign-in. No further infrastructure or unrelated core features in this setup session.
