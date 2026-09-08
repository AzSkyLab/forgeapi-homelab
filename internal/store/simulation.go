package store

import (
	"context"
	"time"

	"forgeapi/internal/execution"
)

// AllocateSimulation models an external, durable idempotent provider effect in
// its own transaction. Projection can fail after this commits, just as after a
// real provider accepts an operation. It never launches a process or image.
func (s *Store) AllocateSimulation(ctx context.Context, id string) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	r, err := loadLocked(ctx, tx, id)
	if err != nil {
		return err
	}
	if execution.Terminal(r.Execution.State) || r.Execution.Cancellation != nil || !time.Now().Before(r.Execution.DeadlineAt) {
		return nil
	}
	_, err = tx.Exec(ctx, `INSERT INTO simulated_resources(execution_id,present,allocation_count) VALUES($1,true,1) ON CONFLICT DO NOTHING`, id)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// CloseSimulation shares the execution lock with allocation. A late allocation
// can neither pass a closed terminal intent nor overwrite this durable tombstone.
func (s *Store) CloseSimulation(ctx context.Context, id string) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	r, err := loadLocked(ctx, tx, id)
	if err != nil {
		return err
	}
	if !execution.Terminal(r.Execution.State) {
		return ErrConflict
	}
	_, err = tx.Exec(ctx, `INSERT INTO simulated_resources(execution_id,present,submission_closed,allocation_count) VALUES($1,false,true,0)
	ON CONFLICT(execution_id) DO UPDATE SET present=false,submission_closed=true`, id)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) RecoveryPending(ctx context.Context) ([]string, error) {
	rows, err := s.Pool.Query(ctx, "SELECT execution_id FROM recovery_tasks WHERE next_attempt_at<=now() ORDER BY next_attempt_at,execution_id LIMIT 10")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

func (s *Store) RecoveryRetry(ctx context.Context, id string) error {
	_, err := s.Pool.Exec(ctx, "UPDATE recovery_tasks SET attempts=attempts+1,next_attempt_at=now()+interval '5 seconds' WHERE execution_id=$1", id)
	return err
}

func (s *Store) Unfinished(ctx context.Context) ([]string, error) {
	rows, err := s.Pool.Query(ctx, `SELECT id FROM executions WHERE document->'Execution'->>'dispatch_status'='started'
	AND document->'Execution'->>'state' NOT IN ('succeeded','failed','cancelled','timed_out') ORDER BY id LIMIT 100`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}
