# Hosting direction

Current as of: 2026-09-30. The engineer selected Temporal behind the redesigned HTTP API.

See [agent architecture](agent-architecture.md) and [work deployment brief](work-deployment.md). The implementation supports a single host with local persistent disk, an API and a Python Temporal worker, plus a Temporal service (local development or external). It cannot use the previous separate stateless Container Apps plus Azure Table layout.

The current work simulation runs on `pve-desktop`, inside its existing `k3s-server-01` guest. One pod hosts the API, Temporal/worker and three Floci emulators. The engineer's desktop calls the API through SSH; cloud resources are provisioned inside the emulators. The deployment is pinned to the guest and uses durable local operation storage. See [deployment and access](../deploy/emulator/README.md). No home-lab infrastructure provider or separate PostgreSQL VM is needed.

Distributed work hosting would require a transactional remote operation ledger with resource fencing, durable exact-plan storage and a verified migration for existing resources. It must preserve atomic acceptance/audit, idempotency, pinned execution and explicit uncertain outcomes. That remains future work; the immediate verification target is the server-hosted Azure/AWS/GCP simulation.

The placement-enabled storage milestone runs alongside it in namespace `forgeapi-placement`, using the same existing guest and separate local state. It exercises platform-owned Azure subscription, AWS account and GCP project targets with real Terraform storage patterns. Desktop API port: 38000. See [storage demo](../deploy/emulator-placement/README.md).

The historical hosting plan is retained at [archive-temporal/hosting-plan.md](archive-temporal/hosting-plan.md) for the old release.
