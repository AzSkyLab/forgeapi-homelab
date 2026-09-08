-- Additive, separate stack lifecycle. Original compute records remain unchanged.
CREATE TABLE local_deployments (
    id text PRIMARY KEY,
    owner text NOT NULL,
    idempotency_digest text NOT NULL UNIQUE,
    request_hash text NOT NULL,
    resource_key text NOT NULL UNIQUE,
    state text NOT NULL,
    body jsonb NOT NULL,
    receipt jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE local_deployment_outbox (
    deployment_id text NOT NULL REFERENCES local_deployments(id),
    phase text NOT NULL CHECK (phase IN ('plan','apply')),
    delivered boolean NOT NULL DEFAULT false,
    PRIMARY KEY(deployment_id,phase)
);
