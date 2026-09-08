GO ?= go
COMPOSE ?= docker compose
TEST_COMPOSE = $(COMPOSE) -f compose.test.yaml

.PHONY: up down deps run-api run-worker migrate demo keyvault-worker keyvault-demo keyvault-identity-check verify-local test test-verbose test-docker test-integration test-core vuln-docker check fmt fmt-check
up:
	$(COMPOSE) stop api worker
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
keyvault-worker:
	FORGE_LOCAL_HELPER=keyvault-worker sh scripts/demo.sh
keyvault-demo:
	sh scripts/demo.sh -key-vault
keyvault-identity-check:
	FORGE_LOCAL_HELPER=keyvault-worker sh scripts/demo.sh -verify-identity
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
test-core:
	$(TEST_COMPOSE) up -d --wait postgres temporal
	$(TEST_COMPOSE) run --build --rm --no-deps -e 'FORGE_TEST_DATABASE_URL=postgres://forge:test-fixture-only@postgres:5432/forge?sslmode=disable' -e FORGE_TEST_TEMPORAL_ADDRESS=temporal:7233 tests go test -race -count=1 -v -tags=integration ./internal/store ./internal/orchestration
vuln-docker:
	$(TEST_COMPOSE) run --build --rm --no-deps tests go run golang.org/x/vuln/cmd/govulncheck@v1.7.0 ./...
check: fmt-check
	$(GO) vet ./...
	$(GO) test -race -count=1 ./...
	$(GO) build ./...
fmt:
	$(GO) fmt ./...
fmt-check:
	test -z "$$(gofmt -l cmd internal)"
