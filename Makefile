GO ?= go
COMPOSE ?= docker compose
TEST_COMPOSE = $(COMPOSE) -f compose.test.yaml

.PHONY: up down deps run-api run-worker migrate demo verify-local test test-verbose test-docker test-integration check fmt fmt-check
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
	sh scripts/demo.sh
verify-local:
	sh scripts/verify-local.sh
test:
	$(GO) test ./...
test-verbose:
	$(GO) test -count=1 -v ./...
test-docker:
	$(TEST_COMPOSE) run --build --rm --no-deps tests
test-integration:
	$(TEST_COMPOSE) up -d --wait postgres
	$(TEST_COMPOSE) run --build --rm --no-deps -e 'FORGE_TEST_DATABASE_URL=postgres://forge:test-fixture-only@postgres:5432/forge?sslmode=disable' tests go test -race -count=1 -v -tags=integration ./internal/store
check: fmt-check
	$(GO) vet ./...
	$(GO) test -race -count=1 ./...
	$(GO) build ./...
fmt:
	$(GO) fmt ./...
fmt-check:
	test -z "$$(gofmt -l cmd internal)"
