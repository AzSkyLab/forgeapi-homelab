# Tomorrow's demo — about ten minutes

## Before the meeting

Run the [README startup and tests](../README.md) once on the demonstration machine. Initial image/module downloads require approved network access. Use synthetic data only. Docker Desktop or an approved compatible runtime must support Linux containers and Compose. Ports 8080, 54329, 7233 and 8233 must be free.

The work demonstration will use a Mac. Follow the [Mac setup note](../README.md#on-your-work-mac), start Docker Desktop, and rehearse startup, demo and tests on that machine. The same commands apply to Intel and Apple silicon; no host Go installation is required for this walkthrough.

## 1. Explain the shape — one minute

Open [TLDR](TLDR.md). Say: “We are building the API first. The database and workflow engine are real; compute is simulated. We can develop and test without provisioning Azure infrastructure.”

## 2. Show the running system — three minutes

```sh
docker compose up --build -d --wait
docker compose ps
docker compose run --rm demo
```

Open <http://localhost:8233>. Search for the execution ID printed by the demo. Show workflow history: activities, timers, completion and cancellation. The executable checks real HTTP responses and prints `PASS` only after its assertions succeed.

For a manual API request (POSIX shell; in PowerShell use `curl.exe` with suitable quoting):

```sh
curl -i http://localhost:8080/executions \
  -H 'X-Demo-Principal: alice' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: meeting-demo-0001' \
  -d '{"application_id":"software-factory","environment":"development","template_id":"pr-validation-v1","template_version":"1.0.0","input_artifact_refs":["source-01"]}'
```

Copy the returned ID into `/executions/ID`, `/events`, `/logs` or `/results`, with the same identity header. Repeat the POST to show the same original acceptance. Change a field with the same key to show `409`. Use a fresh key when you want a new job.

## 3. Show how tests guide development — three minutes

```sh
docker compose run --build --rm --no-deps tests go test -count=1 -v ./internal/execution -run TestResolveTemplate
docker compose run --rm --no-deps tests go test -count=1 -v ./internal/orchestration -run TestWorkflow
docker compose run --rm --no-deps tests
```

Open `internal/execution/input_test.go` beside `input.go`. Explain the table: valid input passes; missing fields, unauthorized overrides, duplicate JSON keys and invalid timeouts are rejected. Workflow tests advance a virtual clock, so they don't need a live Temporal server or fixed sleeps. HTTP tests call the actual router using Go's `httptest`.

Show the co-development loop without making a throwaway change during the meeting: choose a behavior → add its failing test → implement → run checks → human review. A good next task is implementing bounded event long-polling, which this increment explicitly rejects.

## 4. Show local recovery — optional two minutes

```sh
docker compose stop worker
# Submit a NEW manual request with a fresh Idempotency-Key.
# Its status remains accepted; its dispatch intent is in PostgreSQL.
docker compose restart api
docker compose start worker
```

Poll that execution: it should progress. Repeating its original request/key still returns the original acceptance. This demonstrates a specific local recovery case, not complete disaster-recovery coverage. Keep both named volumes; do not erase them.

## 5. Close with the roadmap — one minute

“Next we finish the core API and enterprise identity/recovery tests. Then we add shared Azure hosting and the real compute provider. Persistent infrastructure operations follow as another API capability.”

If the stack fails, inspect `docker compose logs --tail=100 postgres temporal migrate api worker`. Do not present a successful build or simulated workload log as proof that real cloud execution works.
