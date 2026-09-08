package orchestration

import (
	"context"
	"errors"
	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"forgeapi/internal/telemetry"
	"go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/api/workflowservice/v1"
	"go.temporal.io/sdk/client"
	"log/slog"
	"time"
)

type DispatchClient interface {
	ExecuteWorkflow(context.Context, client.StartWorkflowOptions, interface{}, ...interface{}) (client.WorkflowRun, error)
	SignalWorkflow(context.Context, string, string, string, interface{}) error
	DescribeWorkflowExecution(context.Context, string, string) (*workflowservice.DescribeWorkflowExecutionResponse, error)
}
type DispatchPolicy func(context.Context, execution.Record) (bool, error)

func Dispatch(ctx context.Context, s *store.Store, c DispatchClient, policy DispatchPolicy) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	nextRecovery := time.Time{}
	for {
		if err := DispatchOnce(ctx, s, c, policy); err != nil && ctx.Err() == nil {
			slog.Warn("outbox delivery will retry", "error_type", "delivery_unavailable")
		}
		if !time.Now().Before(nextRecovery) {
			if err := ReconcileOnce(ctx, s, c); err != nil && ctx.Err() == nil {
				slog.Warn("recovery will retry", "error_type", "recovery_unavailable")
			}
			nextRecovery = time.Now().Add(5 * time.Second)
		}
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}

func DispatchOnce(ctx context.Context, s *store.Store, c DispatchClient, policy DispatchPolicy) error {
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
				err = dispatchStart(callCtx, s, c, policy, r)
			} else if !execution.Terminal(r.Execution.State) {
				err = c.SignalWorkflow(callCtx, p.ExecutionID, "", CancelSignal, "requested")
			}
			if err == nil {
				err = s.Delivered(callCtx, p)
			}
		}
		cancel()
		if err != nil && ctx.Err() == nil {
			retryCtx, retryCancel := context.WithTimeout(ctx, 3*time.Second)
			if retryErr := s.Retry(retryCtx, p); retryErr != nil {
				err = retryErr
			}
			if p.Attempts >= 4 {
				_ = s.Update(retryCtx, p.ExecutionID, func(r *execution.Record) error {
					if !execution.Terminal(r.Execution.State) && r.Execution.DispatchStatus == "pending" {
						r.Execution.DispatchStatus = "attention_required"
						r.Event("execution.dispatch_changed", time.Now().UTC())
					}
					return nil
				})
			}
			retryCancel()
		}
		if first == nil && err != nil {
			first = err
		}
	}
	return first
}

func dispatchStart(ctx context.Context, s *store.Store, c DispatchClient, policy DispatchPolicy, r execution.Record) error {
	ctx, span := telemetry.Span(ctx, r, "execution.dispatch")
	defer span.End()
	// A lost Start response must be resolved by stable workflow ID before any
	// decision that might incorrectly claim no workflow/resources exist.
	_, err := c.DescribeWorkflowExecution(ctx, r.Execution.ID, "")
	if err == nil {
		return nil
	}
	var missing *serviceerror.NotFound
	if !errors.As(err, &missing) {
		return err
	}
	if execution.Terminal(r.Execution.State) {
		return nil
	}
	finish := func(name string) error {
		if err := s.Update(ctx, r.Execution.ID, func(current *execution.Record) error {
			current.CoreVersion = 1
			return apply(current, name, time.Now().UTC(), false)
		}); err != nil {
			return err
		}
		// Closing the synthetic provider marker fences even an in-flight late
		// Start from another dispatcher. Its activity rechecks terminal intent.
		a := Activities{Store: s}
		if err := a.Cleanup(ctx, r.Execution.ID); err != nil {
			return err
		}
		return a.Deliver(ctx, r.Execution.ID)
	}
	if !time.Now().Before(r.Execution.DeadlineAt) {
		return finish("timed_out")
	}
	if r.Execution.Cancellation != nil {
		return finish("cancelled")
	}
	if policy == nil {
		return errors.New("dispatch policy unavailable")
	}
	allowed, err := policy(ctx, r)
	if err != nil {
		return err
	}
	revoked, err := s.TemplateRevoked(ctx)
	if err != nil {
		return err
	}
	if !allowed || revoked {
		return finish("policy_denied")
	}
	_, err = c.ExecuteWorkflow(ctx, client.StartWorkflowOptions{ID: r.Execution.ID, TaskQueue: TaskQueue, WorkflowIDReusePolicy: enums.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE}, Execution, Input{ID: r.Execution.ID, Deadline: r.Execution.DeadlineAt})
	var exists *serviceerror.WorkflowExecutionAlreadyStarted
	if errors.As(err, &exists) {
		return nil
	}
	return err
}
