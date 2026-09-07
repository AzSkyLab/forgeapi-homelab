# Meeting demo — about ten minutes

## Before the meeting

Complete [Entra setup](entra-local.md) for the approved tenant and each engineer's local grant. Start Docker Desktop on the Mac, then rehearse **`sh scripts/verify-local.sh`**. This runs tests, starts the real local dependencies, and opens browser sign-in. First builds need network access; live sign-in needs Entra. No host Go or client secret is required.

Normal startup is Entra-only. There is no offline fixture-auth API to switch to if consent fails. Unit/HTTP/workflow/database tests remain credentials-free.

## 1. Explain the shape — one minute

Open [TLDR](TLDR.md). Say: “We are building the API first. Entra authenticates us. The database and workflow engine are real; compute is simulated. We can develop without provisioning Azure hosting.”

## 2. Show the running system — three minutes

```sh
docker compose up --build -d --wait
docker compose ps
sh scripts/demo.sh
```

Sign in with your own granted account. The helper sends an API access token from process memory, not from a pasted command or secret. It checks authenticated submission, idempotency replay/conflict, lifecycle, events/logs, artifact integrity, cancellation and timeout. It also proves missing-token and former demo-header requests fail. Cross-user/auditor isolation is covered by automated tests; a one-user walkthrough is not a two-user connected test.

Open <http://localhost:8233>. Search for the execution ID printed by the demo. Show activities, timers and completion. `PASS` is printed only after the walkthrough assertions succeed. Artifacts and workload logs are explicitly synthetic.

A safe manual denial demonstration (no token needed):

```sh
curl -i http://localhost:8080/identity-context
curl -i http://localhost:8080/identity-context -H 'X-Demo-Principal: alice'
```

Both should return **401**. Never paste a real bearer token into meeting notes, a terminal recording or chat.

## 3. Show how tests guide development — three minutes

```sh
docker compose -f compose.test.yaml run --build --rm --no-deps tests go test -count=1 -v ./internal/auth -run TestEntraTokenValidation
docker compose -f compose.test.yaml run --rm --no-deps tests go test -count=1 -v ./internal/httpapi -run TestEntraHTTPAuthorization
docker compose -f compose.test.yaml run --rm --no-deps tests
```

Open `internal/auth/entra_test.go`: forged, expired, wrong-tenant/audience/client and ID tokens are denied. Then `internal/httpapi/entra_test.go`: the actual verifier/router enforce ownership and current grants, including after revocation with an old cursor/ETag. Synthetic signing keys keep this deterministic; they are not accepted by the running API.

Input tests exercise strict request validation. Temporal tests advance a virtual clock without a server or fixed sleeps. `make test-integration` exercises transactions and concurrency in a separate ephemeral PostgreSQL database.

## 4. Show the co-development loop — two minutes

Open [handoff](handoff.md). Choose one behavior → add its failing test → implement → run checks → human review. Each session gets an exact code map and bounded task, not a mandate to reread the design folder. After connected identity evidence, a suitable next slice is bounded event long-polling.

## 5. Close with the roadmap — one minute

“Next we finish core semantics, authorization/recovery, telemetry and CI. Then we add reviewed shared Azure hosting and a real compute provider. Azure services should use managed identities; external workloads should use WIF. Infrastructure operations through the API come later.”

If startup/sign-in fails, inspect sanitized API logs and the browser's Entra error. Do not turn off authentication or claim a successful unit suite proves connected sign-in. Keep the application's named volumes; see README for shutdown. The Temporal UI and local database remain development interfaces, not Entra-protected endpoints.
