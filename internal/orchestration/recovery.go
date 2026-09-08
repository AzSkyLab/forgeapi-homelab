package orchestration

import (
	"context"
	"errors"
	"time"

	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"go.temporal.io/api/enums/v1"
)

// ReconcileOnce is independent of workflow completion. It never starts another
// workflow or reallocates a resource. Lost/closed workflows and unfinished
// terminal delivery/cleanup remain visible in PostgreSQL across process loss.
func ReconcileOnce(ctx context.Context, s *store.Store, c DispatchClient) error {
	ids, err := s.Unfinished(ctx)
	if err != nil {
		return err
	}
	var failures []error
	for _, id := range ids {
		callCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		description, err := c.DescribeWorkflowExecution(callCtx, id, "")
		if err == nil && description.WorkflowExecutionInfo != nil && description.WorkflowExecutionInfo.Status != enums.WORKFLOW_EXECUTION_STATUS_RUNNING {
			err = s.Update(callCtx, id, func(r *execution.Record) error {
				if execution.Terminal(r.Execution.State) {
					return nil
				}
				r.CoreVersion = 1
				return apply(r, "failed", time.Now().UTC(), false)
			})
		}
		cancel()
		if err != nil {
			failures = append(failures, err)
		}
	}
	ids, err = s.RecoveryPending(ctx)
	if err != nil {
		return err
	}
	a := Activities{Store: s}
	for _, id := range ids {
		cleanupCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		cleanupErr := a.Cleanup(cleanupCtx, id)
		cancel()
		deliveryCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		deliveryErr := a.Deliver(deliveryCtx, id)
		cancel()
		if cleanupErr != nil || deliveryErr != nil {
			retryCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
			failures = append(failures, cleanupErr, deliveryErr, s.RecoveryRetry(retryCtx, id))
			if err := s.Update(retryCtx, id, func(r *execution.Record) error {
				if r.Execution.CompletedAt == nil || time.Now().Before(r.Execution.CompletedAt.Add(time.Minute)) {
					return nil
				}
				changed := false
				if cleanupErr != nil && r.Execution.CleanupState != "succeeded" && r.Execution.CleanupError == nil {
					r.Execution.CleanupState = "failed"
					r.Execution.CleanupError = &execution.Failure{Code: "cleanup_pending_recovery", Message: "Synthetic cleanup budget elapsed; recovery continues.", Origin: "platform"}
					changed = true
				}
				if deliveryErr != nil && !r.Applied["delivery"] && r.Execution.DeliveryError == nil {
					r.Execution.DeliveryStatus = "partial"
					r.Result.DeliveryStatus = "partial"
					r.Execution.DeliveryError = &execution.Failure{Code: "delivery_pending_recovery", Message: "Synthetic delivery budget elapsed; recovery continues.", Origin: "platform"}
					changed = true
				}
				if changed {
					r.Event("execution.recovery_changed", time.Now().UTC())
				}
				return nil
			}); err != nil {
				failures = append(failures, err)
			}
			cancel()
		}
	}
	return errors.Join(failures...)
}
