# ForgeAPI — local-first execution API

A Go API for requesting and tracking asynchronous jobs. This first increment runs **locally**, with real PostgreSQL and Temporal and **simulated compute**. No Azure account or infrastructure deployment is needed.

Read the [TLDR and roadmap](docs/TLDR.md) first. Use the [demo walkthrough](docs/demo.md) for a meeting. The larger [design package](docs/design/README.md) is reference material, not required onboarding.

## Start everything with Docker

Prerequisite: an approved Docker installation with Linux containers (Engine 28+), and `docker compose` (Compose v2.20+ or later). First startup needs access to Docker Hub and Go's module proxy; prebuild before the meeting. Pinned tools/images are recorded in `go.mod`, `go.sum`, and `Dockerfile`/`compose.yaml`. The tested environment is recorded in [progress](docs/progress.md).

```sh
docker compose up --build -d --wait
docker compose run --rm demo
```

API: <http://localhost:8080/healthz> · Temporal UI: <http://localhost:8233>

### On your work Mac

Use your organization's approved [Docker Desktop for Mac](https://docs.docker.com/desktop/setup/install/mac-install/) installation for Apple silicon or Intel, and start Docker Desktop before running the commands above. No host Go installation is needed for the Docker-only demo or tests. The shell examples work in macOS Terminal's zsh.

The pinned Go, PostgreSQL and Temporal image indexes include both `linux/arm64` and `linux/amd64`. Docker selects the native variant; build the API locally with `--build` as shown above. Do not force `linux/amd64` on Apple silicon. See [Docker's architecture selection guidance](https://docs.docker.com/build/building/multi-platform/).

Before the meeting, check `docker compose version` and `docker info`, then run startup, demo and tests on the Mac. Image architecture availability has been checked; execution on an actual Mac has not yet been verified.

### Demo and shutdown

The demo checks submission, retry/conflict handling, fixture permissions, events/logs, artifact integrity, cancellation, and timeout. It exits nonzero on failure. It creates only synthetic local records; repeated runs use new request keys.

```sh
docker compose logs -f api worker
docker compose down
```

`down` stops the stack and preserves its named database volumes. Do not use `down -v` unless you intend to erase the local execution and workflow history. Never mount a work repository, cloud credential, or Docker socket into a workload; this simulator launches no workloads at all.

## Run tests

No Go installation is required for the Docker test path:

```sh
docker compose run --build --rm --no-deps tests
```

With Go 1.27.1 installed, unit/HTTP/workflow tests need **neither Docker nor Azure**:

```sh
go test ./...
go test -v ./internal/execution -run TestResolveTemplate
go test -v ./internal/orchestration -run TestWorkflow
```

`make check` adds formatting, vet, race detection, and build. Native race detection needs a supported platform and C compiler; the Docker test image includes one. `make test-integration` runs PostgreSQL concurrency/transaction tests in isolated test schemas. Those tests fail, rather than skip, if their required database is unavailable.

Without Make, the equivalent integration commands are:

```sh
docker compose up -d --wait postgres
docker compose run --build --rm --no-deps -e 'FORGE_TEST_DATABASE_URL=postgres://forge:local-fixture-only@postgres:5432/forge?sslmode=disable' tests go test -race -count=1 -v -tags=integration ./internal/store
```

## Faster debugging: dependencies in Docker, Go on the host

```sh
docker compose up -d --wait postgres temporal
FORGE_MODE=local-demo go run ./cmd/forgeapi migrate
FORGE_MODE=local-demo go run ./cmd/forgeapi api
# In a second terminal:
FORGE_MODE=local-demo go run ./cmd/forgeapi worker
# In a third terminal:
go run ./cmd/demo
```

Stop containerized `api` and `worker` first if already running. On PowerShell, set `$env:FORGE_MODE="local-demo"` before running Go commands instead of the shell prefix. Default host ports: API 8080, PostgreSQL 54329, Temporal 7233, Temporal UI 8233. Each engineer should run a separate local stack; no shared cloud dev environment is needed yet.

## Boundaries

This is an **M1 local development increment**, not full M1 acceptance or a deployable service. `X-Demo-Principal` is an openly selectable fixture identity, **not authentication**. Only synthetic data belongs here. All published ports bind to loopback, browser-origin API calls are rejected, and non-demo startup is rejected. Do not expose this stack through tunnels or deploy it to ACA.

PostgreSQL stores API records/outbox/results; the Temporal development server stores workflow history in its own SQLite-backed named volume. That SQLite is Temporal's local server implementation, **not a replacement for the application's PostgreSQL**. Both volumes must be retained together. The project bridge permits outbound networking; it is not a workload sandbox. No production HA, cross-store restore guarantee, Entra/JWKS validation, live provider, or Terraform is implemented.

See [current implementation and next work](docs/progress.md) and [how we collaborate](CONTRIBUTING.md).
