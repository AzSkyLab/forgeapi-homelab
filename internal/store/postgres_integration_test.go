//go:build integration

package store

import (
	"context"
	"encoding/json"
	"errors"
	"forgeapi/internal/execution"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"os"
	"sync"
	"testing"
	"time"
)

func unmigratedDatabase(t *testing.T) *Store {
	t.Helper()
	url := os.Getenv("FORGE_TEST_DATABASE_URL")
	if url == "" {
		t.Fatal("FORGE_TEST_DATABASE_URL is required; integration checks never silently skip")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	schema := execution.ID("test_")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err = admin.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		admin.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if _, err := admin.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE"); err != nil {
			t.Error(err)
		}
		admin.Close()
	})
	cfg, err := pgxpool.ParseConfig(url)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	p, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(p.Close)
	s := &Store{Pool: p}
	return s
}

func database(t *testing.T) *Store {
	t.Helper()
	s := unmigratedDatabase(t)
	ctx := context.Background()
	var err error
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if err = s.Migrate(ctx); err != nil {
		t.Fatal("migration rerun", err)
	}
	return s
}

func TestConcurrentAdmissionIsAtomic(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	var wg sync.WaitGroup
	results := make(chan Accepted, 20)
	errs := make(chan error, 20)
	for range 20 {
		wg.Go(func() {
			a, err := s.Submit(ctx, "alice", "concurrent-key-01", "hash-a", "http://localhost:8080", execution.Defaults())
			results <- a
			errs <- err
		})
	}
	wg.Wait()
	close(results)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	id := ""
	for a := range results {
		var e execution.Execution
		if err := json.Unmarshal(a.Body, &e); err != nil {
			t.Fatal(err)
		}
		if id != "" && e.ID != id {
			t.Fatal("duplicate execution")
		}
		id = e.ID
	}
	for _, table := range []string{"executions", "idempotency", "outbox"} {
		var count int
		if err := s.Pool.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&count); err != nil || count != 1 {
			t.Fatalf("%s count=%d err=%v", table, count, err)
		}
	}
	if _, err := s.Submit(ctx, "alice", "concurrent-key-01", "hash-b", "http://localhost:8080", execution.Defaults()); !errors.Is(err, ErrConflict) {
		t.Fatal("missing conflict", err)
	}
	if err := s.Update(ctx, id, func(r *execution.Record) error { r.Execution.State = "succeeded"; return nil }); err != nil {
		t.Fatal(err)
	}
	a, err := s.Submit(ctx, "alice", "concurrent-key-01", "hash-a", "http://localhost:8080", execution.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	var replay execution.Execution
	_ = json.Unmarshal(a.Body, &replay)
	if replay.State != "accepted" {
		t.Fatal("replay did not preserve original response")
	}
}

func TestCancellationReplayAndOwnership(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	a, err := s.Submit(ctx, "alice", "submit-key-00001", "hash", "http://localhost:8080", execution.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	var e execution.Execution
	_ = json.Unmarshal(a.Body, &e)
	if _, err := s.Cancel(ctx, "bob", e.ID, "cancel-key-00001", "hash", "operator_request"); !errors.Is(err, ErrNotFound) {
		t.Fatal("other principal cancelled")
	}
	c, err := s.Cancel(ctx, "alice", e.ID, "cancel-key-00001", "hash", "operator_request")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Update(ctx, e.ID, func(r *execution.Record) error {
		r.Execution.State = "cancelled"
		r.Execution.Cancellation.Status = "acknowledged"
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	replayed, err := s.Cancel(ctx, "alice", e.ID, "cancel-key-00001", "hash", "operator_request")
	if err != nil || string(replayed.Body) != string(c.Body) {
		t.Fatal("cancellation replay changed")
	}
	if _, err := s.Cancel(ctx, "alice", e.ID, "cancel-key-00002", "hash", "operator_request"); !errors.Is(err, ErrTerminal) {
		t.Fatal("new cancellation on terminal execution")
	}
}

func TestAcceptanceSurvivesDatabaseConnectionRestart(t *testing.T) {
	s := database(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	a, err := s.Submit(ctx, "alice", "restart-key-00001", "hash", "http://localhost:8080", execution.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	var e execution.Execution
	_ = json.Unmarshal(a.Body, &e)
	cfg := s.Pool.Config()
	s.Pool.Close()
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	reopened := &Store{Pool: pool}
	r, err := reopened.Get(ctx, e.ID)
	if err != nil || r.Execution.ID != e.ID {
		t.Fatal("lost acceptance", err)
	}
	pending, err := reopened.Pending(ctx)
	if err != nil || len(pending) != 1 {
		t.Fatal("lost dispatch intent", err)
	}
}
