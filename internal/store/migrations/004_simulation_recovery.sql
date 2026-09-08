-- Durable synthetic provider state. Tombstones survive cleanup to fence late
-- allocation/retry. These rows do not represent Azure resources.
CREATE TABLE simulated_resources (
    execution_id text PRIMARY KEY REFERENCES executions(id),
    present boolean NOT NULL,
    submission_closed boolean NOT NULL DEFAULT false,
    allocation_count integer NOT NULL CHECK(allocation_count BETWEEN 0 AND 1)
);
CREATE TABLE recovery_tasks (
    execution_id text PRIMARY KEY REFERENCES executions(id),
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now()
);
