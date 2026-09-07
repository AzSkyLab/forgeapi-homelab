#!/bin/sh
# Leaves local services/data intact; fails at the first failed prerequisite/test.
set -eu
cd "$(dirname "$0")/.."
docker compose version
docker info --format 'Docker engine {{.ServerVersion}} ({{.OSType}}/{{.Architecture}})'
docker compose config --quiet
docker compose -f compose.test.yaml config --quiet
docker compose -f compose.test.yaml run --build --rm --no-deps tests
docker compose -f compose.test.yaml up -d --wait postgres
docker compose -f compose.test.yaml run --rm --no-deps \
  -e 'FORGE_TEST_DATABASE_URL=postgres://forge:test-fixture-only@postgres:5432/forge?sslmode=disable' \
  tests go test -race -count=1 -v -tags=integration ./internal/store
docker compose up --build -d --wait
sh scripts/demo.sh "$@"
