-- Additive operational metadata. Existing execution documents and histories stay intact.
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS attempts integer NOT NULL DEFAULT 0;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX IF NOT EXISTS outbox_due ON outbox(next_attempt_at, created_at, id) WHERE NOT delivered;
CREATE INDEX IF NOT EXISTS executions_active_owner ON executions(owner_id)
    WHERE document->'Execution'->>'state' NOT IN ('succeeded','failed','cancelled','timed_out');
CREATE TABLE IF NOT EXISTS template_policy (
    template_id text NOT NULL,
    version text NOT NULL,
    revoked boolean NOT NULL DEFAULT false,
    PRIMARY KEY(template_id,version)
);
INSERT INTO template_policy(template_id,version) VALUES('pr-validation-v1','1.0.0') ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS audit_events (
    execution_id text NOT NULL REFERENCES executions(id),
    revision bigint NOT NULL,
    event_type text NOT NULL,
    recorded_at timestamptz NOT NULL,
    owner_id text NOT NULL,
    request_id text NOT NULL,
    flow_id text NOT NULL,
    trace_parent text NOT NULL,
    state text NOT NULL,
    PRIMARY KEY(execution_id,revision)
);
