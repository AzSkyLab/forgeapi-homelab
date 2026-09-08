//go:build integration

package store

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"forgeapi/internal/execution"
)

func TestLegacyMigrationPreservesReplayAndHistory(t *testing.T) {
	s := unmigratedDatabase(t)
	ctx := context.Background()
	if _, err := s.Pool.Exec(ctx, schema); err != nil {
		t.Fatal(err)
	}
	r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), time.Now())
	document, _ := json.Marshal(r)
	body, _ := json.Marshal(r.Execution)
	scope := "alice|software-factory|development|submit|legacy-key-00001"
	if _, err := s.Pool.Exec(ctx, "INSERT INTO executions VALUES($1,$2,$3)", r.Execution.ID, r.Owner, document); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Pool.Exec(ctx, "INSERT INTO outbox VALUES($1,$2,'start',false)", r.Execution.ID+"-start", r.Execution.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Pool.Exec(ctx, "INSERT INTO idempotency VALUES($1,$2,$3,$4)", scope, "hash", body, r.Execution.Links["self"]); err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if err := s.Migrate(ctx); err != nil {
			t.Fatal(err)
		}
	}
	a, err := s.Submit(ctx, "alice", "legacy-key-00001", "hash", "http://localhost:8080", execution.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	canonical, _ := canonicalResponse(body)
	if string(a.Body) != string(canonical) {
		t.Fatal("migration lost original replay")
	}
	got, err := s.Get(ctx, r.Execution.ID)
	if err != nil || len(got.Events) != len(r.Events) {
		t.Fatal("migration changed history")
	}
	var stored string
	if err := s.Pool.QueryRow(ctx, "SELECT scope FROM idempotency").Scan(&stored); err != nil || stored != digestScope(scope) || strings.Contains(stored, "legacy-key") {
		t.Fatal("plaintext scope retained", err)
	}
	if _, err := s.Pool.Exec(ctx, "UPDATE schema_migrations SET checksum='changed' WHERE version='001_initial'"); err != nil {
		t.Fatal(err)
	}
	if s.Migrate(ctx) == nil {
		t.Fatal("modified migration accepted")
	}
}

func TestConcurrentAdmissionBudgetsAndAudit(t *testing.T) {
	s := database(t)
	s.CallerLimit, s.ServiceLimit = 3, 5
	ctx := execution.WithCorrelation(context.Background(), execution.Correlation{RequestID: "req-proof", FlowID: "flow-proof"})
	errs := make(chan error, 20)
	var wg sync.WaitGroup
	for i := range 20 {
		wg.Go(func() {
			_, err := s.Submit(ctx, "alice", fmt.Sprintf("admission-key-%04d", i), "hash", "http://localhost:8080", execution.Defaults())
			errs <- err
		})
	}
	wg.Wait()
	close(errs)
	accepted, rejected := 0, 0
	for err := range errs {
		if err == nil {
			accepted++
		} else if errors.Is(err, ErrCallerCapacity) {
			rejected++
		} else {
			t.Fatal(err)
		}
	}
	if accepted != 3 || rejected != 17 {
		t.Fatalf("accepted=%d rejected=%d", accepted, rejected)
	}
	for _, table := range []string{"executions", "outbox", "idempotency", "audit_events"} {
		var count int
		if err := s.Pool.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&count); err != nil || count != 3 {
			t.Fatalf("%s=%d: %v", table, count, err)
		}
	}
	for i := range 2 {
		if _, err := s.Submit(ctx, "bob", fmt.Sprintf("service-key-%04d", i), "hash", "http://localhost:8080", execution.Defaults()); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := s.Submit(ctx, "charlie", "service-key-00001", "hash", "http://localhost:8080", execution.Defaults()); !errors.Is(err, ErrServiceCapacity) {
		t.Fatal("global limit", err)
	}
	if _, err := s.Submit(ctx, "bob", "service-key-0000", "hash", "http://localhost:8080", execution.Defaults()); err != nil {
		t.Fatal("replay consumed capacity", err)
	}
	var request, flow string
	if err := s.Pool.QueryRow(ctx, "SELECT request_id,flow_id FROM audit_events LIMIT 1").Scan(&request, &flow); err != nil || request != "req-proof" || flow != "flow-proof" {
		t.Fatal("lost audit correlation", err)
	}
}

func TestRevocationReplayAndOutboxBackoff(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	first, err := s.Submit(ctx, "alice", "template-key-0001", "hash", "http://localhost:8080", execution.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	if _, err = s.Pool.Exec(ctx, "UPDATE template_policy SET revoked=true"); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Submit(ctx, "alice", "template-key-0002", "hash", "http://localhost:8080", execution.Defaults()); !errors.Is(err, ErrTemplateRevoked) {
		t.Fatal("revoked admission accepted", err)
	}
	replayed, err := s.Submit(ctx, "alice", "template-key-0001", "hash", "http://localhost:8080", execution.Defaults())
	if err != nil || string(first.Body) != string(replayed.Body) {
		t.Fatal("revocation erased acceptance", err)
	}
	pending, err := s.Pending(ctx)
	if err != nil || len(pending) != 1 {
		t.Fatal(err)
	}
	if err = s.Retry(ctx, pending[0]); err != nil {
		t.Fatal(err)
	}
	pending, err = s.Pending(ctx)
	if err != nil || len(pending) != 0 {
		t.Fatal("retry spins without backoff", err)
	}
}
