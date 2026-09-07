// Package store owns transactional admission, idempotency, projections and outbox.
package store

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/json"
	"errors"
	"time"

	"forgeapi/internal/execution"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrNotFound = errors.New("not found")
var ErrConflict = errors.New("idempotency conflict")
var ErrTerminal = errors.New("execution terminal")

//go:embed schema.sql
var schema string

type Store struct{ Pool *pgxpool.Pool }
type Accepted struct {
	Body     json.RawMessage
	Location string
}
type Pending struct {
	ID          string
	ExecutionID string
	Kind        string
}

func Open(ctx context.Context, url string) (*Store, error) {
	p, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	if err = p.Ping(ctx); err != nil {
		p.Close()
		return nil, err
	}
	return &Store{Pool: p}, nil
}

// Migrate is explicit, serialized, and additive. It is not run by HTTP handlers.
func (s *Store) Migrate(ctx context.Context) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(706010001)"); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, schema); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func (s *Store) Get(ctx context.Context, id string) (execution.Record, error) {
	var data []byte
	err := s.Pool.QueryRow(ctx, "SELECT document FROM executions WHERE id=$1", id).Scan(&data)
	if errors.Is(err, pgx.ErrNoRows) {
		return execution.Record{}, ErrNotFound
	}
	var r execution.Record
	if err == nil {
		err = json.Unmarshal(data, &r)
	}
	return r, err
}

func loadLocked(ctx context.Context, tx pgx.Tx, id string) (execution.Record, error) {
	var data []byte
	err := tx.QueryRow(ctx, "SELECT document FROM executions WHERE id=$1 FOR UPDATE", id).Scan(&data)
	if errors.Is(err, pgx.ErrNoRows) {
		return execution.Record{}, ErrNotFound
	}
	var r execution.Record
	if err == nil {
		err = json.Unmarshal(data, &r)
	}
	return r, err
}

func save(ctx context.Context, tx pgx.Tx, r execution.Record) error {
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, "UPDATE executions SET document=$2 WHERE id=$1", r.Execution.ID, b)
	return err
}

func (s *Store) Update(ctx context.Context, id string, fn func(*execution.Record) error) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	r, err := loadLocked(ctx, tx, id)
	if err != nil {
		return err
	}
	if err = fn(&r); err != nil {
		return err
	}
	if err = save(ctx, tx, r); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func lockKey(ctx context.Context, tx pgx.Tx, scope string) error {
	h := sha256.Sum256([]byte(scope))
	_, err := tx.Exec(ctx, "SELECT pg_advisory_xact_lock($1)", int64(binary.BigEndian.Uint64(h[:8])))
	return err
}

func replay(ctx context.Context, tx pgx.Tx, scope, hash string) (Accepted, bool, error) {
	var got string
	var a Accepted
	err := tx.QueryRow(ctx, "SELECT request_hash, response, location FROM idempotency WHERE scope=$1", scope).Scan(&got, &a.Body, &a.Location)
	if errors.Is(err, pgx.ErrNoRows) {
		return a, false, nil
	}
	if err != nil {
		return a, false, err
	}
	if got != hash {
		return a, false, ErrConflict
	}
	// JSONB normalizes object order/spacing. Return the same canonical JSON on
	// first acceptance and every replay, including after a process restart.
	a.Body, err = canonicalResponse(a.Body)
	if err != nil {
		return a, false, err
	}
	return a, true, nil
}

func canonicalResponse(b []byte) ([]byte, error) {
	var value any
	if err := json.Unmarshal(b, &value); err != nil {
		return nil, err
	}
	return json.Marshal(value)
}

func recordAcceptance(ctx context.Context, tx pgx.Tx, scope, hash string, body any, location string) (Accepted, error) {
	b, err := json.Marshal(body)
	if err != nil {
		return Accepted{}, err
	}
	b, err = canonicalResponse(b)
	if err != nil {
		return Accepted{}, err
	}
	_, err = tx.Exec(ctx, "INSERT INTO idempotency(scope,request_hash,response,location) VALUES($1,$2,$3,$4)", scope, hash, b, location)
	return Accepted{Body: b, Location: location}, err
}

func (s *Store) Submit(ctx context.Context, owner, key, hash, base string, spec execution.Spec) (Accepted, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return Accepted{}, err
	}
	defer tx.Rollback(ctx)
	scope := owner + "|" + spec.ApplicationID + "|" + spec.Environment + "|submit|" + key
	if err = lockKey(ctx, tx, scope); err != nil {
		return Accepted{}, err
	}
	if a, found, err := replay(ctx, tx, scope, hash); err != nil || found {
		return a, err
	}
	r := execution.NewRecord(owner, base, spec, time.Now().UTC())
	b, err := json.Marshal(r)
	if err != nil {
		return Accepted{}, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO executions(id,owner_id,document) VALUES($1,$2,$3)", r.Execution.ID, owner, b); err != nil {
		return Accepted{}, err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO outbox(id,execution_id,kind) VALUES($1,$2,'start')", r.Execution.ID+"-start", r.Execution.ID); err != nil {
		return Accepted{}, err
	}
	a, err := recordAcceptance(ctx, tx, scope, hash, r.Execution, r.Execution.Links["self"])
	if err != nil {
		return a, err
	}
	return a, tx.Commit(ctx)
}

func (s *Store) Cancel(ctx context.Context, owner, id, key, hash, reason string) (Accepted, error) {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return Accepted{}, err
	}
	defer tx.Rollback(ctx)
	scope := owner + "|cancel|" + id + "|" + key
	if err = lockKey(ctx, tx, scope); err != nil {
		return Accepted{}, err
	}
	r, err := loadLocked(ctx, tx, id)
	if err != nil {
		return Accepted{}, err
	}
	if r.Owner != owner {
		return Accepted{}, ErrNotFound
	}
	if a, found, err := replay(ctx, tx, scope, hash); err != nil || found {
		return a, err
	}
	if execution.Terminal(r.Execution.State) {
		return Accepted{}, ErrTerminal
	}
	if r.Execution.Cancellation == nil {
		now := time.Now().UTC()
		r.Execution.Cancellation = &execution.Cancellation{ID: execution.ID("cancel-"), ExecutionID: id, Status: "requested", RequestedAt: now, DeadlineAt: now.Add(time.Minute), Reason: reason}
		r.Event("execution.cancellation_changed", now)
		if err = save(ctx, tx, r); err != nil {
			return Accepted{}, err
		}
		if _, err = tx.Exec(ctx, "INSERT INTO outbox(id,execution_id,kind) VALUES($1,$2,'cancel') ON CONFLICT DO NOTHING", id+"-cancel", id); err != nil {
			return Accepted{}, err
		}
	}
	a, err := recordAcceptance(ctx, tx, scope, hash, r.Execution.Cancellation, r.Execution.Links["self"])
	if err != nil {
		return a, err
	}
	return a, tx.Commit(ctx)
}

func (s *Store) Pending(ctx context.Context) ([]Pending, error) {
	// Deliver a start before its cancellation. Failed delivery remains retryable.
	rows, err := s.Pool.Query(ctx, `SELECT o.id,o.execution_id,o.kind FROM outbox o WHERE NOT o.delivered
	 AND (o.kind='start' OR EXISTS(SELECT 1 FROM outbox s WHERE s.execution_id=o.execution_id AND s.kind='start' AND s.delivered))
	 ORDER BY o.id LIMIT 50`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []Pending{}
	for rows.Next() {
		var p Pending
		if err := rows.Scan(&p.ID, &p.ExecutionID, &p.Kind); err != nil {
			return nil, err
		}
		items = append(items, p)
	}
	return items, rows.Err()
}

func (s *Store) Delivered(ctx context.Context, p Pending) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if p.Kind == "start" {
		r, err := loadLocked(ctx, tx, p.ExecutionID)
		if err != nil {
			return err
		}
		if r.Execution.DispatchStatus != "started" {
			r.Execution.DispatchStatus = "started"
			r.Event("execution.dispatch_changed", time.Now().UTC())
			if err = save(ctx, tx, r); err != nil {
				return err
			}
		}
	}
	if _, err = tx.Exec(ctx, "UPDATE outbox SET delivered=true WHERE id=$1", p.ID); err != nil {
		return err
	}
	return tx.Commit(ctx)
}
