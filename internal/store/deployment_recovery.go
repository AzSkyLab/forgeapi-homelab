package store

import (
	"context"
	"encoding/json"
	"strings"

	"forgeapi/internal/deployment"
)

// ConfirmRejectedDeployment is an operator-only transition after external proof
// that the failed create had no effect. It is deliberately not an HTTP operation.
// Receipts, outboxes, old target/plan binding and events remain intact. The
// historical resource key is retired atomically with the rejection evidence,
// allowing one new API acceptance to own the same actual target.
func (s *Store) ConfirmRejectedDeployment(ctx context.Context, id, digest, correlation string, current deployment.Target) error {
	tx, err := s.Pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, "SELECT pg_advisory_xact_lock(706010005)"); err != nil {
		return err
	}
	var b []byte
	if err = tx.QueryRow(ctx, "SELECT body FROM local_deployments WHERE id=$1 FOR UPDATE", id).Scan(&b); err != nil {
		return err
	}
	var r deployment.Record
	if err = json.Unmarshal(b, &r); err != nil {
		return err
	}
	expected := r.Target
	expected.PatternDigest = current.PatternDigest
	if r.PlanDigest != digest || digest == "" || correlation == "" || current != expected {
		return deployment.ErrConflict
	}
	if r.State == "rejected_no_effect" && r.RejectionCorrelationID == correlation {
		return nil
	}
	if r.State != "recovery_required" {
		return deployment.ErrConflict
	}
	r.RejectionCorrelationID = correlation
	r.Transition("rejected_no_effect")
	b, _ = json.Marshal(r)
	_, err = tx.Exec(ctx, "UPDATE local_deployments SET state=$2,body=$3,resource_key=$4,updated_at=now() WHERE id=$1", id, r.State, b, strings.ToLower(r.Target.ResourceID())+"#rejected/"+id)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
