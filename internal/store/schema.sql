-- Local slice migration 1. Owned by this application; no infrastructure provisioning.
CREATE TABLE IF NOT EXISTS executions (
    id text PRIMARY KEY,
    owner_id text NOT NULL,
    document jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
    scope text PRIMARY KEY,
    request_hash text NOT NULL,
    response jsonb NOT NULL,
    location text NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id text PRIMARY KEY,
    execution_id text NOT NULL REFERENCES executions(id),
    kind text NOT NULL CHECK (kind IN ('start', 'cancel')),
    delivered boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS outbox_pending ON outbox(id) WHERE NOT delivered;
