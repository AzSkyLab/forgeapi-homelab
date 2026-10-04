-- Captured from pre-versioning app.ledger.SCHEMA on 2026-10-01.

CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY, resource_id TEXT NOT NULL, actor TEXT NOT NULL,
  request_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
  state TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(actor, request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS resource_busy ON operations(resource_id)
  WHERE state NOT IN ('succeeded', 'failed');
CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT, actor TEXT NOT NULL,
  action TEXT NOT NULL, outcome TEXT NOT NULL, timestamp TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
