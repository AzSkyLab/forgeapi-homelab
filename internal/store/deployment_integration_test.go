//go:build integration

package store

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"forgeapi/internal/deployment"
)

func fixtureExecutor() deployment.Executor {
	return deployment.Executor{Mode: "lab_certificate", ClientID: "44444444-4444-4444-4444-444444444444", PrincipalID: "55555555-5555-5555-5555-555555555555", CertificateSHA256: strings.Repeat("b", 64)}
}

func TestDeploymentAcceptanceApprovalAndOwnership(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	target := deployment.Target{Owner: "owner", ResourceGroupID: "/target", Name: "kv-test", PatternDigest: "sha256:" + strings.Repeat("a", 64)}
	target.Executor = fixtureExecutor()
	var wg sync.WaitGroup
	receipts := make(chan []byte, 20)
	errs := make(chan error, 20)
	for range 20 {
		wg.Go(func() {
			b, err := s.CreateDeployment(ctx, "owner", "same-key", "hash", target)
			receipts <- b
			errs <- err
		})
	}
	wg.Wait()
	close(receipts)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	var receipt []byte
	for b := range receipts {
		if receipt != nil && string(receipt) != string(b) {
			t.Fatal("replay changed")
		}
		receipt = b
	}
	var r deployment.Record
	if err := json.Unmarshal(receipt, &r); err != nil {
		t.Fatal(err)
	}
	pending, err := s.PendingDeployments(ctx)
	if err != nil || len(pending) != 1 || pending[0].Phase != "plan" {
		t.Fatal("atomic outbox", err)
	}
	for _, table := range []string{"local_deployments", "local_deployment_outbox"} {
		var n int
		if err = s.Pool.QueryRow(ctx, "SELECT count(*) FROM "+table).Scan(&n); err != nil || n != 1 {
			t.Fatal("duplicate rows", table, n, err)
		}
	}
	if _, err = s.CreateDeployment(ctx, "owner", "same-key", "changed", target); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("missing key conflict", err)
	}
	if _, err = s.CreateDeployment(ctx, "owner", "new-key", "hash", target); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("same resource has multiple writers", err)
	}
	if _, err = s.CreateDeployment(ctx, "other", "new-key", "hash", target); !errors.Is(err, deployment.ErrDenied) {
		t.Fatal("other owner accepted", err)
	}
	digest := "sha256:" + strings.Repeat("b", 64)
	if err = s.ApproveDeployment(ctx, r.ID, "owner", digest, target); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("unplanned approval")
	}
	if err = s.UpdateDeployment(ctx, r.ID, func(r *deployment.Record) error {
		r.PlanDigest = digest
		r.PlanExpiresAt = time.Now().Add(time.Minute)
		r.Transition("planned")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err = s.ApproveDeployment(ctx, r.ID, "other", digest, target); !errors.Is(err, deployment.ErrNotFound) {
		t.Fatal("approval ownership", err)
	}
	if err = s.ApproveDeployment(ctx, r.ID, "owner", "wrong-digest", target); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("approval digest", err)
	}
	changed := target
	changed.Executor.ClientID = "66666666-6666-6666-6666-666666666666"
	if err = s.ApproveDeployment(ctx, r.ID, "owner", digest, changed); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("executor switch admitted", err)
	}
	changed = target
	changed.Executor.CertificateSHA256 = strings.Repeat("c", 64)
	if err = s.ApproveDeployment(ctx, r.ID, "owner", digest, changed); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("certificate switch admitted", err)
	}
	changed = target
	changed.Name = "different"
	if err = s.ApproveDeployment(ctx, r.ID, "owner", digest, changed); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("approval target drift", err)
	}
	for range 2 {
		if err = s.ApproveDeployment(ctx, r.ID, "owner", digest, target); err != nil {
			t.Fatal(err)
		}
	}
	pending, err = s.PendingDeployments(ctx)
	if err != nil || len(pending) != 2 {
		t.Fatal("approval did not atomically add exactly one apply intent", err)
	}
	got, err := s.GetDeployment(ctx, r.ID)
	if err != nil || got.State != "apply_queued" {
		t.Fatal("projection", got.State, err)
	}
	migrated := target
	migrated.Executor.ClientID = "66666666-6666-6666-6666-666666666666"
	replay, err := s.CreateDeployment(ctx, "owner", "same-key", "hash", migrated)
	if err != nil || string(replay) != string(receipt) {
		t.Fatal("replay changed after approval", err)
	}
}

func TestNewDeploymentCannotUseLegacyHumanExecutor(t *testing.T) {
	s := database(t)
	_, err := s.CreateDeployment(context.Background(), "owner", "fresh-key", "hash", deployment.Target{Owner: "owner", ResourceGroupID: "/test", Name: "vault"})
	if !errors.Is(err, deployment.ErrDenied) {
		t.Fatal("new human-CLI deployment accepted", err)
	}
	pending, err := s.PendingDeployments(context.Background())
	if err != nil || len(pending) != 0 {
		t.Fatal("denied identity wrote an outbox", err)
	}
}
func TestExpiredDeploymentPlan(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	target := deployment.Target{Owner: "owner", ResourceGroupID: "/target", Name: "kv-expired"}
	target.Executor = fixtureExecutor()
	b, err := s.CreateDeployment(ctx, "owner", "key", "hash", target)
	if err != nil {
		t.Fatal(err)
	}
	var r deployment.Record
	_ = json.Unmarshal(b, &r)
	if err = s.UpdateDeployment(ctx, r.ID, func(r *deployment.Record) error {
		r.PlanDigest = "digest"
		r.PlanExpiresAt = time.Now().Add(-time.Second)
		r.Transition("planned")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	if err = s.ApproveDeployment(ctx, r.ID, "owner", "digest", target); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("expired plan approved", err)
	}
}
