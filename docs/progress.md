# Current work — local core increment

**Authorization, 2026-09-07:** the requesting engineer approved starting local API implementation and a runnable demonstration with tests/TLDR. This is scoped approval to build; it does not assert manager, enterprise-security or cloud-deployment signoff. Infrastructure provisioning remains out of scope.

## Implemented

- Go API and separate worker; local Docker Compose with PostgreSQL and persistent Temporal development server/UI.
- One immutable fixture template; five required inputs, governed defaults, strict JSON parsing and bounded requests.
- Atomic PostgreSQL acceptance + idempotency + outbox; stable Temporal workflow ID; retryable dispatch.
- Submit/alias, status, cancellation, event/log pages, results, artifact download and identity/catalog routes.
- Clearly simulated lifecycle/output; fixture owner/auditor policy; same-origin URLs, signed resource/principal-bound cursors and sanitized errors.
- Native Go unit/HTTP tests, Temporal virtual-clock tests, tagged PostgreSQL tests and an executable HTTP demonstration.

## Verification

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

No Windows/macOS/ARM machine, real Entra tenant, enterprise Temporal connection, cloud provider, vulnerability scanner, long-running fuzz campaign, or full failure/replay suite was verified. The runtime stack remains running locally at handoff; `docker compose down` stops it while retaining data.

## Explicit limits / next tasks

1. **Next small task:** complete event long-polling with bounded wait, tail-cursor and cancellation tests. Catalog pagination, full content negotiation and tracestate propagation also remain incomplete; unsupported inputs are rejected, not silently accepted.
2. Replace selectable fixture identities with tested Entra/JWKS validation, current grants/classification and ownership policy. No production mode or real token handling exists now.
3. Harden lifecycle/recovery: bounded cancellation/dispatch attention, workflow failure reconciliation, independent cleanup/delivery retries, replay histories, controlled crash windows, telemetry/audit, retention and cross-store recovery. Current fake effects complete atomically in PostgreSQL; they do not prove external-effect recovery.
4. Add versioned incremental migrations beyond the initial additive local schema, CI gates, vulnerability scanning and full runtime OpenAPI conformance. The M0 OpenAPI is the **target** contract; the local slice is not fully conformant.
5. Complete required local and connected M1 evidence in the design delivery plan. Then separately approve shared ACA hosting and live provider work. Do not deploy the current local fixture binary.

The demo has no workload failure injection, real image execution, input uploads, secret delivery, cost enforcement or live cloud effects. The published `sbom` is synthetic JSON, not a real SPDX document. Existing ADRs remain Proposed pending individual decision records; this local increment does not silently ratify all M0 proposals.
