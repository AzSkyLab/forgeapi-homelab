GO ?= go
COMPOSE ?= docker compose

.PHONY: up down deps run-api run-worker migrate demo test test-verbose test-docker test-integration check fmt fmt-check
up:
	$(COMPOSE) up --build -d --wait
down:
	$(COMPOSE) down
deps:
	$(COMPOSE) up -d --wait postgres temporal
run-api:
	FORGE_MODE=local-demo $(GO) run ./cmd/forgeapi api
run-worker:
	FORGE_MODE=local-demo $(GO) run ./cmd/forgeapi worker
migrate:
	FORGE_MODE=local-demo $(GO) run ./cmd/forgeapi migrate
demo:
	$(COMPOSE) run --rm demo
test:
	$(GO) test ./...
test-verbose:
	$(GO) test -count=1 -v ./...
test-docker:
	$(COMPOSE) run --build --rm --no-deps tests
test-integration:
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) run --build --rm --no-deps -e 'FORGE_TEST_DATABASE_URL=postgres://forge:local-fixture-only@postgres:5432/forge?sslmode=disable' tests go test -race -count=1 -v -tags=integration ./internal/store
check: fmt-check
	$(GO) vet ./...
	$(GO) test -race -count=1 ./...
	$(GO) build ./...
fmt:
	$(GO) fmt ./...
fmt-check:
	test -z "$$(gofmt -l cmd internal)"
