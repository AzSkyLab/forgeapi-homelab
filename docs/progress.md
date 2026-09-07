# Current work — local core increment

**Authorization, 2026-09-07:** the requesting engineer approved starting local API implementation and a runnable demonstration with tests/TLDR. This is scoped approval to build; it does not assert manager, enterprise-security or cloud-deployment signoff. Infrastructure provisioning remains out of scope.

**Identity update, 2026-09-07:** the engineer requested Entra as the normal configuration (not opt-in), then explicitly authorized setup in the current Azure CLI tenant. Two dedicated app registrations and their service principals were created; the engineer completed browser sign-in and the real-token Docker API walkthrough passed. Paid Azure resources remain prohibited. Local human sign-in uses PKCE; future hosted/external workloads should use managed identities/WIF. No client secret, API key, ARM role or tenant-wide consent was created.

## Implemented

- Go API and separate worker; local Docker Compose with PostgreSQL and persistent Temporal development server/UI.
- One immutable fixture template; five required inputs, governed defaults, strict JSON parsing and bounded requests.
- Atomic PostgreSQL acceptance + idempotency + outbox; stable Temporal workflow ID; retryable dispatch.
- Submit/alias, status, cancellation, event/log pages, results, artifact download and identity/catalog routes.
- Clearly simulated lifecycle/output; owner/auditor policy; same-origin URLs, signed resource/principal-bound cursors and sanitized errors.
- Native Go unit/HTTP tests, Temporal virtual-clock tests, tagged PostgreSQL tests and an executable HTTP demonstration.
- Entra-only runtime authentication: tenant/issuer/audience, RS256/JWKS, token times, approved client, delegated scope or explicit app-only role. No fixture startup/fallback; former demo headers are rejected.
- Current file-backed grants for the synthetic application/environment, tenant/object-ID ownership, owner/auditor data separation and per-request revocation even with old ETags/cursors. This is not the full enterprise policy registry or dispatch-time revocation.
- MSAL browser PKCE helper with in-memory tokens and a Docker-built host-native executable; required default Compose Entra settings, separate credentials-free automated-test project, Mac/Linux rehearsal script and compact [coding handoff](handoff.md).
- Repeatable identity-only setup using the signed-in Azure CLI (`scripts/setup-entra.mjs`, Node built-ins only). Generated IDs/journal, current grants and cursor key stay in ignored local files. Exact [work setup instructions for the next assistant](work-setup.md) are ready.

## Current authentication verification

| Check | Result |
| --- | --- |
| `make check` | PASS: formatting, vet, race tests and build, including signed-token HTTP/ownership/revocation tests |
| `docker compose -f compose.test.yaml run --build --rm --no-deps tests` | PASS: credential-free containerized unit/auth/HTTP/workflow/race suite |
| `make test-integration` | PASS: atomic concurrent acceptance, cancellation ownership/replay and reconnect durability against isolated Docker PostgreSQL |
| `sh scripts/demo.sh -h` | PASS on Linux: classic Docker builder, host-native executable extraction, help-only invocation and temporary build-container cleanup; no sign-in attempted |
| Native helper cross-builds for `darwin/arm64` and `darwin/amd64` | PASS: both binaries compiled; actual Mac execution remains unverified |
| `docker build --target runtime -t forgeapi-local:dev .`; network-disabled startup-denial checks | PASS: non-root runtime image builds with public CA certificates; missing Entra settings and explicit `AUTH_MODE=fixture` both exit unsuccessfully as required |
| Normal Compose without `.env`; example-values `config --quiet`; test Compose config | PASS: normal configuration refuses missing Entra values; both files validate with appropriate synthetic configuration inputs |
| `python3 docs/design/tools/validate_design.py`; `git diff --check` | PASS: source hashes/design regressions and whitespace |
| Local onboarding links; shell syntax; config/build artifact exclusions | PASS: 21 local links resolve, both scripts parse, local config and generated helper are gitignored |
| Real tenant registration + browser token + local API walkthrough | **PASS, 2026-09-07:** the engineer completed MSAL browser PKCE; the Docker API validated a real Entra access token and the full authenticated walkthrough passed |
| `node scripts/setup-entra.mjs --tenant <confirmed-tenant>` | PASS: created dedicated API/client registrations + service principals, generated config, then a second run verified/reused the same objects without creating duplicates or overwriting local grants/key |
| Read back the dedicated client's actual delegated consent | PASS: user-specific (`Principal`) consent only for `executions.access` on ForgeAPI and ordinary `openid offline_access profile` sign-in scopes; no tenant-wide grant |
| Second real user, managed identity/WIF caller, Mac execution | **NOT VERIFIED** |

The API startup intentionally fails without valid Entra settings and a readable grant file. `/healthz` alone cannot prove authentication works. Old fixture-owned database rows remain intact but are not readable by new Entra principals. No schema reset/data migration was needed: ownership and idempotency now use namespaced tenant/object IDs.

Handoff state: the Entra-configured API and worker are running with PostgreSQL and Temporal, all using the existing preserved application volumes. Host `/healthz` succeeds and tokenless identity access returns 401. The separate test database is disposable. No static Azure credential or paid resource has been created. Read [work-setup.md](work-setup.md) to repeat this on another approved tenant/Mac; do not reuse this machine's tenant-specific config or credentials.

Connected evidence: `exec_fb9e5342a8a72dc70e0ef9a8204bc477` completed successfully after real Entra sign-in. Alias/original idempotency replay stayed stable, changed input returned 409, missing token and former fixture header returned 401, events/logs/results and synthetic artifact digest/size checks passed. `exec_0d7390d4182d4b9d97a58c205a940a0b` was cancelled while running; `exec_8b8930e138c20503c4fd04902e16e51a` timed out. The helper exited 0 with `PASS: local walkthrough complete`. These are local synthetic execution IDs, not reusable fixtures. No token was printed or persisted by the helper.

## Earlier fixture baseline — historical evidence, not current Entra proof

Verified 2026-09-07 on Linux amd64: Go 1.27.1, Docker Engine 29.7.2, Compose 5.5.1, PostgreSQL 16.14, Temporal CLI/server image 1.8.3 and Go SDK 1.48.0. Full M1 acceptance is **not** claimed.

| Check | Result |
| --- | --- |
| `make check` | PASS: formatting, `go vet`, race-enabled unit/HTTP/workflow tests, build |
| `docker compose run --build --rm --no-deps tests` | PASS: Docker-only unit/HTTP/workflow tests with race detector |
| README's tagged PostgreSQL test command | PASS: 20 concurrent identical submissions create one execution/outbox/acceptance; conflict/replay and cancellation ownership; reconnect preserves accepted intent |
| `docker compose up --build -d --wait` | PASS: local stack starts; host API health and Temporal UI each return HTTP 200 |
| `docker compose run --rm demo` and host `go run ./cmd/demo` | PASS: submission/alias replay, 409 conflict, owner/data denial, lifecycle, logs/events, artifact digest/size, running-job cancellation, timeout |
| Manual worker-stop/API-restart sequence in `demo.md` | PASS: accepted intent survived API restart and completed after worker resumed; original acceptance replayed unchanged |
| Recreate containers/network without deleting volumes | PASS: prior API result and completed Temporal history retained |
| `python3 docs/design/tools/validate_design.py`; `git diff --check` | PASS: design regressions/source hashes and whitespace |
| Pinned Redocly 2.51.2 lint; onboarding document link check | PASS: target OpenAPI description and 13 local onboarding links |
| Installed `docker compose version`, `docker compose config --quiet`, `docker compose ps` | PASS: Compose 5.5.1 is now installed and recognizes the running local stack |
| Pinned Go/PostgreSQL/Temporal `docker manifest inspect` checks | PASS: all three image indexes publish Linux amd64 and arm64 variants; no platform override is needed for Intel/Apple-silicon Mac builds |

Integration findings fixed before handoff: initialized the Temporal volume for its non-root UID; used a standard project bridge so host ports publish correctly on this Docker engine; canonicalized stored JSONB responses so first acceptance and replay match. Published ports remain explicitly loopback-only; the bridge is not an outbound-network sandbox.

Recovery sample: `exec_b24f39b4c4b4a3790f000df2e97586a5` was accepted with worker stopped, remained accepted after API restart, then succeeded on worker resume. The earlier `exec_58157bab0d72c7f7c9314aaeb3807bf7` retained its completed Temporal run/history and API results after container recreation. These synthetic IDs are local evidence, not fixtures required on another machine.

Initial verification used checksum-verified temporary Go/Compose binaries because neither command was on PATH. The user subsequently installed Compose; `docker compose version` now reports 5.5.1 and the installed command passes the configuration/status checks above. The work demonstration target is macOS; use the organization's approved Docker Desktop installation and the README's Mac preflight. Native Go remains optional.

No Windows/macOS/ARM machine, real Entra tenant, enterprise Temporal connection, cloud provider, vulnerability scanner, long-running fuzz campaign, or full failure/replay suite was verified by this earlier baseline. Commands containing `docker compose run ... demo` or the old test service apply only to that earlier revision; use the current README commands now.

## Explicit limits / next tasks

1. **Next environment task:** repeat the proven setup on the Mac in its approved tenant using [work-setup.md](work-setup.md). Connected second-user ownership/revocation and workload-identity evidence remain pending; the successful single-user walkthrough does not cover them.
2. Complete event long-polling with bounded wait, tail-cursor and cancellation tests, then catalog pagination, content negotiation and tracestate. Broader group/classification/app-owner policy and dispatch-time grant rechecks remain incomplete; unsupported inputs are rejected, not silently accepted.
3. Harden lifecycle/recovery: bounded cancellation/dispatch attention, workflow failure reconciliation, independent cleanup/delivery retries, replay histories, controlled crash windows, telemetry/audit, retention and cross-store recovery. Current fake effects complete atomically in PostgreSQL; they do not prove external-effect recovery.
4. Add versioned incremental migrations beyond the initial additive local schema, CI gates, vulnerability scanning and full runtime OpenAPI conformance. The M0 OpenAPI is the **target** contract; the local slice is not fully conformant.
5. Complete required local and connected M1 evidence in the design delivery plan. Then separately approve shared ACA hosting and live provider work. Do not deploy the current local fixture binary.

The demo has no workload failure injection, real image execution, input uploads, secret delivery, cost enforcement or live cloud effects. The published `sbom` is synthetic JSON, not a real SPDX document. Existing ADRs remain Proposed pending individual decision records; this local increment does not silently ratify all M0 proposals.
