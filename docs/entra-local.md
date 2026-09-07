# Entra + local Docker

**Normal startup requires Entra. No opt-in auth profile or client-secret fallback.** Identity registration/testing stays in the engineer-confirmed tenant; do not create Azure hosting, licenses, paid resources or tenant-wide policy changes.

## Where identity lives

| Caller/component | Authentication |
| --- | --- |
| You and your colleague | Dedicated native public client; browser sign-in + PKCE, subject to tenant MFA/consent policies |
| Local Go API | Validates Entra API access tokens with public signing keys; needs no Azure credential |
| Local worker | Simulated compute only; makes no Azure calls and needs no cloud identity |
| Future Azure-hosted service | Prefer managed identity where supported; requires separate hosting approval |
| Future external CI/workload | Prefer WIF from an approved OIDC issuer; no long-lived client secret |

The sign-in helper runs on the laptop; API/worker/PostgreSQL/Temporal run in Docker. Entra does not need an inbound connection to the API. The browser returns to the helper's loopback listener. [Microsoft's PKCE flow](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow).

Plain laptop Docker has no Azure managed-identity endpoint. WIF requires a configured trust relationship and an external issuer's token; it is not an automatic laptop identity. Human developer sign-in is appropriate here. [Managed identities](https://learn.microsoft.com/en-us/entra/identity/managed-identities-azure-resources/overview), [WIF](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation).

## One-time tenant configuration

**Repeatable setup:** with Node.js 22+ and an authenticated Azure CLI, run `node scripts/setup-entra.mjs --tenant <confirmed-tenant-UUID>`. It creates the registrations/service principals and local config below without any client secret or admin-consent grant. See [tomorrow's exact LLM instructions](work-setup.md). The manual steps remain useful for enterprise-owned registrations or a second engineer's machine.

Confirm the exact tenant before creating anything. Use dedicated registrations, not an existing production application. No API key, password credential, certificate credential, ARM role or Microsoft Graph application permission is required by either runtime application.

1. Register **ForgeAPI Local API**, single tenant (`AzureADMyOrg`). Record its **Application (client) ID**. Expose `api://<API-client-ID>` with an enabled delegated scope **`executions.access`**. Use user consent if permitted by tenant policy; otherwise have an authorized administrator approve this specific API permission. Set the manifest's `api.requestedAccessTokenVersion` to **2**. Do not enable implicit token issuance.
2. Register **ForgeAPI Local Client**, also single tenant. Add **Mobile and desktop applications** redirect URI **`http://localhost`** (not SPA or web). The helper uses port 8400; Entra allows the native localhost port to vary. Add only the API's delegated `executions.access` permission. No Graph `User.Read` is needed by the helper; remove any default Graph permission on this new dedicated registration. **Do not create a client secret or enable password/device-code fallback.**
3. Record the **tenant ID**, **API client ID**, **native-client ID**, and each allowed person's **user object ID in this tenant**. Object ID is not an email address and is not the app-registration object ID. Consent permits the client to request tokens; ForgeAPI grants below separately authorize operations.

[Desktop registration guidance](https://learn.microsoft.com/en-us/entra/identity-platform/scenario-desktop-app-configuration), [API access-token validation](https://learn.microsoft.com/en-us/entra/identity-platform/claims-validation).

## Local configuration

```sh
cp .env.example .env
cp config/grants.example.json config/grants.local.json
chmod 600 .env
openssl rand -hex 32
```

Edit `.env` with the three client/tenant IDs and the generated value as `CURSOR_SIGNING_KEY`. That key protects pagination integrity; it is **not** an Entra client secret and grants no Azure access. Keep it local and stable across API restarts; rotating it invalidates existing cursors. Never paste it or tokens into tickets/logs.

Edit `config/grants.local.json`: set the confirmed `tenant_id` and allowed user's `object_id`. Add a separate seven-field entry for each person. Keep `principal_kind=human`, `application_id=software-factory`, `environment=development`, `classification=synthetic`. Each principal has one role:

- `developer`: submit/read/cancel **own** executions and read their data/catalog.
- `auditor`: read same-tenant execution metadata/events, but not logs/results/artifacts or submissions.
- `operator`: platform permission only; no workload read access or operator endpoints yet.

These are bounded local application grants, not Azure RBAC roles or a complete enterprise group/classification registry. The API reloads the file on every authenticated request; removal denies subsequent access, including requests with old ETags/cursors. A malformed/missing file fails closed. Revocation does not yet stop an already accepted simulated job.

`.env`, `config/*.local.json` and `.local/` are ignored by Git and Docker's build context. The grant directory mounts read-only into the API; allow the container's non-root UID to read the grant file. It contains real principal IDs granting access to synthetic workloads, never tokens, passwords or source data.

## Run and verify

```sh
docker compose up --build -d --wait
sh scripts/demo.sh
# Or run tests, startup and authenticated walkthrough together:
sh scripts/verify-local.sh
```

The helper uses [MSAL Go](https://learn.microsoft.com/en-us/entra/msal/go/), requests `api://<API-client-ID>/executions.access`, keeps the token cache only in memory, and sends the access token only to the loopback API. It never prints the token or follows API redirects. Sign in as a granted user; repeat separately for the second engineer. No account password passes through ForgeAPI.

Expected: tokenless/fixture-header requests return **401**; valid token but no grant gets an empty identity context and **403** for execution operations; another developer's execution returns **404**; malformed policy or unavailable signing keys returns **503**. A successful `/healthz` proves only process liveness, not working sign-in or dependencies.

## Limits and troubleshooting

Only commercial-cloud, single-tenant **v2** access tokens are supported. Audience must be the API's client-ID GUID, not `api://...`, Graph or ARM. Issuer/tenant, RS256 signature, token times, approved `azp`, `oid` and delegated scope are checked. Keys refresh on a five-minute cache with a 15-second refresh throttle; expired keys are not used after a fetch failure. No arbitrary token-supplied key URL is fetched.

App-only validation is unit-tested (`idtyp=app` plus the configured application role), but **no workload caller is provisioned or live-verified yet**. Before WIF/managed-identity testing, add the API application role `executions.access` for Applications, configure the API's optional `idtyp` access-token claim, assign the narrow role to the approved caller, allow its client ID, and add its service-principal object-ID grant with `principal_kind=application`. This is a later scoped step, not a reason to create a secret now.

For login failures, check the browser's Entra error, tenant consent, native redirect and free port 8400. For 401, check API/client IDs and v2 token configuration. For 403, check the current user's tenant object ID and grant. Do not disable tenant security policies, print tokens or use a secret as a workaround. Corporate TLS proxies may need approved CA installation in the image; never disable certificate verification.
