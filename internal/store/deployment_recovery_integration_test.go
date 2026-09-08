//go:build integration

package store

import (
	"context"
	"encoding/json"
	"errors"
	"testing"

	"forgeapi/internal/deployment"
)

func TestRejectedDeploymentPreservesEvidenceAndReleasesOnlyTarget(t *testing.T) {
	s := database(t)
	ctx := context.Background()
	target := deployment.Target{Owner: "owner", ResourceGroupID: "/approved", Name: "vault", PatternDigest: "old"}
	target.Executor = fixtureExecutor()
	receipt, err := s.CreateDeployment(ctx, "owner", "old-key", "hash", target)
	if err != nil {
		t.Fatal(err)
	}
	var old deployment.Record
	_ = json.Unmarshal(receipt, &old)
	if err = s.UpdateDeployment(ctx, old.ID, func(r *deployment.Record) error {
		r.PlanDigest = "failed-plan"
		r.Transition("recovery_required")
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	current := target
	current.PatternDigest = "corrected"
	wrong := current
	wrong.Name = "different"
	if err = s.ConfirmRejectedDeployment(ctx, old.ID, "failed-plan", "correlation", wrong); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("changed target accepted", err)
	}
	if err = s.ConfirmRejectedDeployment(ctx, old.ID, "different-plan", "correlation", current); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("changed old plan accepted", err)
	}
	for range 2 {
		if err = s.ConfirmRejectedDeployment(ctx, old.ID, "failed-plan", "correlation", current); err != nil {
			t.Fatal(err)
		}
	}
	preserved, err := s.GetDeployment(ctx, old.ID)
	if err != nil || preserved.State != "rejected_no_effect" || preserved.Target != target || preserved.PlanDigest != "failed-plan" || preserved.RejectionCorrelationID != "correlation" || len(preserved.Events) != 3 {
		t.Fatal("original evidence changed", err, preserved)
	}
	replay, err := s.CreateDeployment(ctx, "owner", "old-key", "hash", current)
	if err != nil || string(replay) != string(receipt) {
		t.Fatal("original receipt not preserved", err)
	}
	fresh, err := s.CreateDeployment(ctx, "owner", "new-key", "hash", current)
	if err != nil {
		t.Fatal(err)
	}
	var next deployment.Record
	_ = json.Unmarshal(fresh, &next)
	if next.ID == old.ID || next.Target != current {
		t.Fatal("fresh binding incorrect")
	}
	if _, err = s.CreateDeployment(ctx, "owner", "third-key", "hash", current); !errors.Is(err, deployment.ErrConflict) {
		t.Fatal("multiple live target owners", err)
	}
	pending, err := s.PendingDeployments(ctx)
	if err != nil || len(pending) != 2 {
		t.Fatal("old/new outbox not preserved", err)
	}
}
