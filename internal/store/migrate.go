package store

import (
	"context"
	"crypto/sha256"
	"embed"
	"fmt"

	"github.com/jackc/pgx/v5"
)

//go:embed migrations/*.sql
var migrations embed.FS

// Migrate must run with API and worker stopped. Upgrades are atomic and serialized;
// checksums make accidental edits to an already-applied migration fail closed.
func (s *Store) Migrate(ctx context.Context) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(706010001)"); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return err
	}
	steps := []struct{ version, sql string }{{"001_initial", schema}}
	files, err := migrations.ReadDir("migrations")
	if err != nil {
		return err
	}
	for _, f := range files {
		b, err := migrations.ReadFile("migrations/" + f.Name())
		if err != nil {
			return err
		}
		steps = append(steps, struct{ version, sql string }{f.Name(), string(b)})
	}
	for _, step := range steps {
		checksum := fmt.Sprintf("%x", sha256.Sum256([]byte(step.sql)))
		var stored string
		err := tx.QueryRow(ctx, "SELECT checksum FROM schema_migrations WHERE version=$1", step.version).Scan(&stored)
		if err == nil {
			if stored != checksum {
				return fmt.Errorf("migration checksum mismatch: %s", step.version)
			}
			continue
		}
		if err != pgx.ErrNoRows {
			return err
		}
		if _, err = tx.Exec(ctx, step.sql); err != nil {
			return fmt.Errorf("migration %s failed: %w", step.version, err)
		}
		if _, err = tx.Exec(ctx, "INSERT INTO schema_migrations(version,checksum) VALUES($1,$2)", step.version, checksum); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}
