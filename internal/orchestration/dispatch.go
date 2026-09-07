package orchestration

import (
	"context"
	"errors"
	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/sdk/client"
	"log/slog"
	"time"
)

func Dispatch(ctx context.Context, s *store.Store, c client.Client) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		if err := DispatchOnce(ctx, s, c); err != nil && ctx.Err() == nil {
			slog.Warn("outbox delivery will retry", "error_type", "delivery_unavailable")
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func DispatchOnce(ctx context.Context, s *store.Store, c client.Client) error {
	pending, err := s.Pending(ctx)
	if err != nil {
		return err
	}
	var first error
	for _, p := range pending {
		callCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		r, err := s.Get(callCtx, p.ExecutionID)
		if err == nil {
			if p.Kind == "start" {
				_, err = c.ExecuteWorkflow(callCtx, client.StartWorkflowOptions{ID: p.ExecutionID, TaskQueue: TaskQueue, WorkflowIDReusePolicy: enums.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE}, Execution, Input{ID: p.ExecutionID, Deadline: r.Execution.DeadlineAt})
				var exists *serviceerror.WorkflowExecutionAlreadyStarted
				if errors.As(err, &exists) {
					err = nil
				}
			} else if !execution.Terminal(r.Execution.State) {
				err = c.SignalWorkflow(callCtx, p.ExecutionID, "", CancelSignal, "requested")
			}
			if err == nil {
				err = s.Delivered(callCtx, p)
			}
		}
		cancel()
		if first == nil && err != nil {
			first = err
		}
	}
	return first
}
