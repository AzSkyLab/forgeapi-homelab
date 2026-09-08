# ForgeAPI — local-first execution API

A Go API for requesting and tracking asynchronous jobs. **Entra sign-in is required.** The API, PostgreSQL, Temporal and worker run locally in Docker; compute is simulated. No Azure hosting or paid resources are needed for this local slice.

**Separately authorized live spike:** [one empty Standard Key Vault through the API and Terraform](docs/key-vault-demo.md). The trusted native worker now uses a [dedicated lab service principal and seven-day certificate](docs/terraform-identity.md), with no human CLI fallback or credentials in Docker. Exact saved-plan approval is required. `make keyvault-identity-check` safely verifies actual executor access against the existing vault without applying anything. This is not general repository execution or shared deployment.

Read the [TLDR and roadmap](docs/TLDR.md). For tomorrow's machine/tenant setup, give the assistant [these exact instructions](docs/work-setup.md). For co-development, start with the short [handoff](docs/handoff.md); the larger design package is reference material, not required onboarding.

## Set up and run

1. Complete the one-time [Entra configuration](docs/entra-local.md): two app registrations, three IDs and a local grant. **No client secret.**
2. Start Docker, then run:

```sh
make up
sh scripts/demo.sh
```

The helper opens browser sign-in using Microsoft's library and PKCE. It runs the authenticated submission/replay/status/results/cancellation/timeout walkthrough without printing or saving tokens. It exits nonzero on failure. On a headless terminal use `sh scripts/demo.sh -print-login-url` and open the URL in a browser on the **same machine**.

API liveness: <http://localhost:8080/healthz> · Temporal UI: <http://localhost:8233>

After configuration, rehearse with **`sh scripts/verify-local.sh`**: Docker checks → unit/race/contract tests → real PostgreSQL/Temporal recovery tests → startup → browser sign-in and demo. No cloud objects are created by these commands. `make up` stops API/worker for versioned migrations; it preserves both data volumes. Without Make, run `docker compose stop api worker` before `docker compose up --build -d --wait`.

### On your Mac

Use your organization's approved [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/), with Linux containers and Compose. No host Go installation is needed. Docker builds a native Mac sign-in helper into the ignored `.local/bin` directory; it runs on the host so the browser callback reaches the right localhost.

The pinned Go, PostgreSQL and Temporal image indexes include both `linux/arm64` and `linux/amd64`. Do not force amd64 on Apple silicon. [Docker architecture selection](https://docs.docker.com/build/building/multi-platform/). Image/build availability is not proof of execution on an actual Mac; rehearse on that machine.

Prerequisites: Docker Engine 28+, Compose v2.20+ or later, ports 8080/54329/7233/8233 and the temporary sign-in callback port 8400 available. The helper build supports both classic Docker and BuildKit; no separate Buildx installation is required. First builds need Docker Hub/Go module access; sign-in and API signing-key refresh need outbound HTTPS to Entra. Prebuild before the meeting.

## Tests need no tenant or credentials

```sh
docker compose -f compose.test.yaml run --build --rm --no-deps tests
make test-integration
make test-core
make vuln-docker
```

The separate test project contains ephemeral PostgreSQL/Temporal and test processes, with no host ports or tenant configuration. `test-core` kills/restarts a real worker, verifies independent cleanup/delivery recovery and replays old/new Temporal histories. `vuln-docker` scans reachable Go vulnerabilities. It does not start an API with selectable identities. Without Make, the PostgreSQL-only check is:

```sh
docker compose -f compose.test.yaml up -d --wait postgres
docker compose -f compose.test.yaml run --build --rm --no-deps -e 'FORGE_TEST_DATABASE_URL=postgres://forge:test-fixture-only@postgres:5432/forge?sslmode=disable' tests go test -race -count=1 -v -tags=integration ./internal/store
```

With Go 1.27.1 installed, `make check` runs formatting, vet, race tests and build. Native race detection needs a C compiler; the Docker dev image includes one. Targeted examples:

```sh
go test -v ./internal/auth -run TestEntraTokenValidation
go test -v ./internal/httpapi -run TestEntraHTTPAuthorization
go test -v ./internal/execution -run TestResolveTemplate
go test -v ./internal/orchestration -run TestWorkflow
```

Auth tests generate synthetic signing keys and replace only the external JWKS response; they do not prove real tenant configuration. Unit/HTTP/workflow tests need neither Docker nor Azure. Tagged database tests fail, rather than skip, if PostgreSQL is unavailable.

## Stop and inspect

```sh
docker compose logs --tail=100 api worker
docker compose logs --tail=100 collector
docker compose down
docker compose -f compose.test.yaml down
```

Normal `down` preserves the application's named PostgreSQL and Temporal volumes. Do not use `down -v` unless you intend to erase execution/workflow history. The separate **test** database uses tmpfs and is discarded when stopped. Configuration is needed for Compose to resolve the normal stack even when stopping it; retain `.env` until shutdown.

## Boundaries

This is an **M1 local development increment**, not full M1 acceptance or a deployable service. Entra is the only runtime authenticator; `X-Demo-Principal` is rejected. Only synthetic workload data belongs here. All published ports bind to loopback; browser-origin API calls are rejected. Do not expose the stack through tunnels or deploy it to ACA.

PostgreSQL stores API records/outbox/results. Temporal's development server stores its own history in a SQLite-backed named volume, not the application's PostgreSQL. Keep both volumes. The project bridge permits outbound networking; it is not a workload sandbox. PostgreSQL and Temporal's local development interfaces do **not** gain Entra protection from the HTTP API's auth layer.

No production HA, cross-store restore guarantee, live compute provider or general-purpose Terraform execution is implemented. The separate one-vault Terraform spike has passed live API-to-Azure verification. No static Azure API key/client secret is used. Managed identities/WIF are the direction for future workloads, not credentials already available in plain local Docker. See [implementation evidence and remaining work](docs/progress.md) and [the meeting walkthrough](docs/demo.md).

Local limits: 10 outstanding executions per caller, 100 total/backlogged intents; unknown/unfinished cleanup holds its slot. Replays consume no new slot. Event waits are 0–25 seconds with 64 concurrent waiters. Logs are bounded to 256 KiB/page. The private readiness/liveness listener is `127.0.0.1:8081` inside the API container, not published on the host. OTLP traces go only to the local collector, without bodies/tokens/baggage; database audit events survive process restarts. CI is defined in `.github/workflows/core.yml` but is not a remotely verified GitHub run until pushed by a human.
