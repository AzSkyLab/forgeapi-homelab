// Package store owns transactional admission, idempotency, projections and outbox.
package store

import (
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"time"

	"forgeapi/internal/execution"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

var ErrNotFound = errors.New("not found")
var ErrConflict = errors.New("idempotency conflict")
var ErrTerminal = errors.New("execution terminal")
var ErrCallerCapacity = errors.New("caller admission capacity")
var ErrServiceCapacity = errors.New("service admission capacity")
var ErrTemplateRevoked = errors.New("template revoked")

//go:embed schema.sql
var schema string

type Store struct {
	Pool *pgxpool.Pool
	// Zero uses conservative local defaults. Tests can lower budgets.
	CallerLimit  int
	ServiceLimit int
}
type Accepted struct {
	Body     json.RawMessage
	Location string
}
type Pending struct {
	ID          string
	ExecutionID string
	Kind        string
	Attempts    int
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
	if err != nil {
		return err
	}
	if r.CoreVersion > 0 && execution.Terminal(r.Execution.State) {
		if r.Execution.CleanupState != "succeeded" || !r.Applied["delivery"] {
			_, err = tx.Exec(ctx, "INSERT INTO recovery_tasks(execution_id) VALUES($1) ON CONFLICT DO NOTHING", r.Execution.ID)
		} else {
			_, err = tx.Exec(ctx, "DELETE FROM recovery_tasks WHERE execution_id=$1", r.Execution.ID)
		}
		if err != nil {
			return err
		}
	}
	return audit(ctx, tx, r)
}

func audit(ctx context.Context, tx pgx.Tx, r execution.Record) error {
	correlation := execution.CorrelationFrom(ctx)
	if correlation.RequestID == "" {
		correlation = r.Correlation
	}
	// Project the append-only state history into a separately queryable audit
	// ledger in the same transaction. Replays cannot duplicate audit rows.
	for _, e := range r.Events {
		if _, err := tx.Exec(ctx, `INSERT INTO audit_events(execution_id,revision,event_type,recorded_at,owner_id,request_id,flow_id,trace_parent,state)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING`, r.Execution.ID, e.Revision, e.EventType, e.RecordedAt, r.Owner, correlation.RequestID, correlation.FlowID, correlation.TraceParent, e.State); err != nil {
			return err
		}
	}
	return nil
}

func digestScope(scope string) string { return fmt.Sprintf("sha256:%x", sha256.Sum256([]byte(scope))) }

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
	err := tx.QueryRow(ctx, "SELECT request_hash, response, location FROM idempotency WHERE scope=$1", digestScope(scope)).Scan(&got, &a.Body, &a.Location)
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
	_, err = tx.Exec(ctx, "INSERT INTO idempotency(scope,request_hash,response,location) VALUES($1,$2,$3,$4)", digestScope(scope), hash, b, location)
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
	// Serialize the short admission decision globally, not workflow execution.
	// Replays are checked first and never consume a new admission slot.
	if err = lockKey(ctx, tx, "admission-budget"); err != nil {
		return Accepted{}, err
	}
	var revoked bool
	if err = tx.QueryRow(ctx, "SELECT revoked FROM template_policy WHERE template_id=$1 AND version=$2 FOR SHARE", spec.TemplateID, spec.TemplateVersion).Scan(&revoked); err != nil {
		return Accepted{}, err
	}
	if revoked {
		return Accepted{}, ErrTemplateRevoked
	}
	var total, caller, pending int
	if err = tx.QueryRow(ctx, `SELECT count(*),count(*) FILTER(WHERE owner_id=$1) FROM executions
	WHERE document->'Execution'->>'state' NOT IN ('succeeded','failed','cancelled','timed_out') OR document->'Execution'->>'cleanup_state'<>'succeeded'`, owner).Scan(&total, &caller); err != nil {
		return Accepted{}, err
	}
	if err = tx.QueryRow(ctx, "SELECT count(*) FROM outbox WHERE NOT delivered").Scan(&pending); err != nil {
		return Accepted{}, err
	}
	callerLimit, serviceLimit := s.CallerLimit, s.ServiceLimit
	if callerLimit <= 0 {
		callerLimit = 10
	}
	if serviceLimit <= 0 {
		serviceLimit = 100
	}
	if total >= serviceLimit || pending >= serviceLimit {
		return Accepted{}, ErrServiceCapacity
	}
	if caller >= callerLimit {
		return Accepted{}, ErrCallerCapacity
	}
	r := execution.NewRecord(owner, base, spec, time.Now().UTC())
	r.Correlation = execution.CorrelationFrom(ctx)
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
	if err = audit(ctx, tx, r); err != nil {
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
	rows, err := s.Pool.Query(ctx, `SELECT o.id,o.execution_id,o.kind,o.attempts FROM outbox o WHERE NOT o.delivered AND o.next_attempt_at<=now()
	 AND (o.kind='start' OR EXISTS(SELECT 1 FROM outbox s WHERE s.execution_id=o.execution_id AND s.kind='start' AND s.delivered))
	 ORDER BY o.next_attempt_at,o.created_at,o.id LIMIT 10`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	items := []Pending{}
	for rows.Next() {
		var p Pending
		if err := rows.Scan(&p.ID, &p.ExecutionID, &p.Kind, &p.Attempts); err != nil {
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
		if r.Execution.DispatchStatus != "started" && !execution.Terminal(r.Execution.State) {
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

func (s *Store) TemplateRevoked(ctx context.Context) (bool, error) {
	var revoked bool
	err := s.Pool.QueryRow(ctx, "SELECT revoked FROM template_policy WHERE template_id=$1 AND version=$2", execution.TemplateID, execution.TemplateVersion).Scan(&revoked)
	return revoked, err
}

func (s *Store) Retry(ctx context.Context, p Pending) error {
	delay := time.Second * time.Duration(1<<min(p.Attempts, 5))
	_, err := s.Pool.Exec(ctx, "UPDATE outbox SET attempts=attempts+1,next_attempt_at=now()+$2::interval WHERE id=$1 AND NOT delivered", p.ID, delay.String())
	return err
}
