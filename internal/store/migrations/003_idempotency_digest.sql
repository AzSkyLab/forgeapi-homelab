-- SHA-256 is built into PostgreSQL; no extension or additional credentials.
-- Existing keys still replay through the same digest. No responses/intents are removed.
UPDATE idempotency SET scope='sha256:' || encode(sha256(convert_to(scope,'UTF8')),'hex')
WHERE scope NOT LIKE 'sha256:%';
-- Old writers fail atomically instead of accepting duplicate intents after upgrade.
ALTER TABLE idempotency ADD CONSTRAINT idempotency_digest_only CHECK(scope ~ '^sha256:[0-9a-f]{64}$');
