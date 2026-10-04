# Multi-cloud storage and placement demo

Current as of: 2026-10-01, object consumption verified. Runs on `k3s-server-01` (10.0.20.10), the existing guest on
`pve-desktop`. This desktop calls the remote HTTP API.

This overlay reuses `../emulator` and adds a platform-owned tenant mapping. It uses namespace
`forgeapi-placement`, local data `/var/lib/forgeapi-placement`, image `forgeapi:floci-placement`,
and its own Temporal history/emulators. The earlier demo and its retained resources stay in
`forgeapi-emulator`. This is isolated validation of the new version; existing state is not migrated.

## Pattern contract

| Pattern | Cloud resources | Hidden platform inputs |
| --- | --- | --- |
| `floci-azure` | Resource group, Standard LRS Storage account, private `data` blob container | subscription ID, region |
| `floci-aws` | S3 bucket, provider account guard | AWS account ID, region |
| `floci-gcp` | GCP storage bucket | project ID, region |

All use `environment: dev`; the sole BU `work` is inferred. The only caller input is `name`.
Use 3–24 lowercase letters/digits for an Azure storage name. Target identifiers and region are
injected from `targets.example.yaml`, containing only dummy emulator values. Real target config
is private. The API rejects overrides; outputs containing target IDs are withheld. AWS checks
the executor account via `allowed_account_ids` and emulated STS; it does not assume a role.

## Connect from this desktop

Keep this command running, or use the tunnel already established:

```sh
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:38000:127.0.0.1:38000 \
  -L 127.0.0.1:38233:127.0.0.1:38233 \
  -L 127.0.0.1:34566:127.0.0.1:34566 \
  -L 127.0.0.1:34577:127.0.0.1:34577 \
  -L 127.0.0.1:34588:127.0.0.1:34588 ubuntu@10.0.20.10 \
  'sudo k3s kubectl -n forgeapi-placement port-forward --address 127.0.0.1 deployment/forgeapi-emulator 38000:8000 38233:8233 34566:4566 34577:4577 34588:4588'
```

API: <http://127.0.0.1:38000/docs>. Temporal UI: <http://127.0.0.1:38233>.
SSH is the access boundary. The API binds pod loopback; there is no public/LAN Service or
Ingress. Do not expose the unauthenticated demo directly to a network.

```sh
curl -fsS 'http://127.0.0.1:38000/patterns/floci-azure?environment=dev'
curl -fsS http://127.0.0.1:38000/operations \
  -H 'Content-Type: application/json' -H 'Idempotency-Key: storage-example-1' \
  -d '{"pattern":"floci-azure","version":"v1.0.0","environment":"dev","inputs":{"name":"forgeexamplestorage01"}}'
```

Poll the returned operation to `planned`, inspect the changes, then POST its `plan_digest`
to the returned execute link. No creation occurs before execution. Destroy uses a new operation
with the same resource ID and its own reviewed plan.

## Verify the complete lifecycle

```sh
python3 deploy/emulator/verify.py --api http://127.0.0.1:38000 \
  --cloud-port-base 34500 --environment dev \
  --evidence .local/pve-placement/evidence/lifecycle.json
```

This checks hidden schema inputs, refused overrides, duplicate requests, real Terraform plan,
explicit apply, output withholding, audit order, independent resource readback and destroy.
Azure readback checks both its ARM storage account and blob data-plane container. `--keep`
retains newly created resources for inspection. Evidence contains operation IDs and safe outputs.

## Verify object consumption

After confirming the namespace/image, healthy pod, and retained examples, use a new evidence
filename for each run:

```sh
python3 deploy/emulator/verify.py --consume-objects \
  --api http://127.0.0.1:38000 --cloud-port-base 34500 --environment dev \
  --evidence .local/pve-placement/evidence/consumption-UNIQUE.json
```

This opt-in mode requires the placement loopback ports and rejects `--keep` and an existing
evidence file. For each cloud it creates new storage through the API, inspects the saved plan,
executes its digest, and derives storage names from safe outputs. It sends mixed text/binary
bytes directly to the emulator, reads them independently, compares exact bytes, length and
SHA-256, then deletes the object and confirms HTTP 404. Only then does it plan and execute
destruction of that run's resource and independently check infrastructure absence.

The standard-library client uses Azure path-style BlockBlob requests, S3 path-style object
requests, and GCP JSON media upload/read/delete. These protocols were checked against the
pinned Floci implementations; emulator requests use no cloud credentials. This does not prove
real-cloud authorization or storage feature parity.
Protocol references: [Azure Blob handler 0.13.0](https://github.com/floci-io/floci-az/blob/0.13.0/src/main/java/io/floci/az/services/blob/BlobServiceHandler.java),
[AWS S3 2.1.0](https://github.com/floci-io/floci/tree/2.1.0),
[GCP media upload 0.9.0](https://github.com/floci-io/floci-gcp/blob/0.9.0/src/main/java/io/floci/gcp/services/gcs/GcsUploadController.java)
and [object read/delete](https://github.com/floci-io/floci-gcp/blob/0.9.0/src/main/java/io/floci/gcp/services/gcs/GcsObjectController.java).

Evidence saves safe request bodies/keys, operation/resource IDs, plan digests and byte hashes
incrementally; object payloads and credentials are excluded. Ambiguous API dispatch retries
use the same request identity within a bounded window. An uncertain operation, mismatched
readback, or failed deletion stops mutation and leaves a recovery handoff in the evidence.
Inspect the recorded operation and emulator state before recovery; never start replacement
work or destroy infrastructure merely because client verification failed. Retained examples
and the earlier demos are outside this command's cleanup scope.

## Build and deploy

```sh
docker build -t forgeapi:floci-placement .
docker save -o /tmp/forgeapi-placement-image.tar forgeapi:floci-placement
scp /tmp/forgeapi-placement-image.tar ubuntu@10.0.20.10:/tmp/
scp -r deploy/emulator deploy/emulator-placement ubuntu@10.0.20.10:/tmp/
# First install only:
ssh ubuntu@10.0.20.10 'sudo k3s kubectl create namespace forgeapi-placement; sudo install -d -m 0700 -o 1000 -g 1000 /var/lib/forgeapi-placement'
ssh ubuntu@10.0.20.10 'sudo k3s ctr images import /tmp/forgeapi-placement-image.tar && sudo k3s kubectl apply -k /tmp/emulator-placement'
ssh ubuntu@10.0.20.10 'sudo k3s kubectl -n forgeapi-placement rollout status deployment/forgeapi-emulator'
```

The base scripts become generated ConfigMaps. Image-only upgrades require a deliberate rollout
restart after import. Node-local data and image imports require the existing node selector;
this is a single-host development runtime. Stop it by scaling to zero, preserving the host data.

## Azure emulator networking

Floci-AZ 0.13.0 returns normal `*.blob/queue/table/dfs.core.windows.net` URLs on port 80 for
Storage. Its [Terraform harness](https://github.com/floci-io/floci-az/blob/main/compatibility-tests/compat-terraform/run-bats-in-container.sh)
uses hosts entries and port forwarding. Our dynamic names instead use `storage_proxy.py`, a
loopback HTTP router restricted to those storage hostnames, forwarding only to Floci on 4577.
It preserves Host for account routing, rejects other destinations and does not implement
CONNECT or log credentials. Only the emulator worker receives its `HTTP_PROXY` setting;
localhost cloud endpoints bypass it. The API runtime and real-cloud provider configuration
do not depend on this helper. Its lifetime is the engine container.

The Azure container fixture uses the data-plane `storage_account_name` option because the
emulator's ARM container URL returns misleading success for nonexistent containers. This is
an emulator compatibility choice with the pinned provider, not proof of ARM container parity.

API/Temporal history and Terraform state persist. Emulator resources are disposable and may
vanish on restart; inspect and replan the same resource ID. Never replay an old saved plan.
Real cloud identity/permissions, storage security behavior and production hosting are unverified.
