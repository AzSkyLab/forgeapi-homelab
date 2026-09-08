// Package orchestration keeps deterministic Temporal decisions separate from effects.
package orchestration

import (
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"
	"time"
)

const TaskQueue = "forgeapi-local-executions"
const CancelSignal = "request-cancellation"

type Input struct {
	ID       string
	Deadline time.Time
}
type Step struct {
	ID   string
	Name string
}

// Execution uses durable timers. Fake steps are atomic and idempotent in PostgreSQL.
// No external provider runs here; retry safety of real cloud effects is future work.
func Execution(ctx workflow.Context, in Input) error {
	if workflow.GetVersion(ctx, "local-core-lifecycle", workflow.DefaultVersion, 1) != workflow.DefaultVersion {
		return coreWorkflow(ctx, in)
	}
	return legacyWorkflow(ctx, in)
}

// Retained for replay compatibility with histories recorded before core v1.
func legacyWorkflow(ctx workflow.Context, in Input) error {
	ctx = workflow.WithActivityOptions(ctx, workflow.ActivityOptions{
		StartToCloseTimeout: 10 * time.Second,
		RetryPolicy:         &temporal.RetryPolicy{InitialInterval: time.Second, MaximumInterval: 10 * time.Second},
	})
	signals := workflow.GetSignalChannel(ctx, CancelSignal)
	finish := func(name string) error {
		return workflow.ExecuteActivity(ctx, "FakeStep", Step{in.ID, name}).Get(ctx, nil)
	}
	for _, name := range []string{"provisioning", "starting", "running", "succeeded"} {
		if !workflow.Now(ctx).Before(in.Deadline) {
			return finish("timed_out")
		}
		var request string
		if signals.ReceiveAsync(&request) {
			return finish("cancelled")
		}
		if err := finish(name); err != nil {
			return err
		}
		if name == "succeeded" {
			return nil
		}
		delay := 2 * time.Second
		if name == "running" {
			delay = 10 * time.Second
		}
		remaining := in.Deadline.Sub(workflow.Now(ctx))
		if remaining < delay {
			delay = remaining
		}
		timerCtx, cancel := workflow.WithCancel(ctx)
		timer := workflow.NewTimer(timerCtx, delay)
		cancelled := false
		selector := workflow.NewSelector(ctx)
		selector.AddFuture(timer, func(workflow.Future) {})
		selector.AddReceive(signals, func(ch workflow.ReceiveChannel, _ bool) { ch.Receive(ctx, &request); cancelled = true })
		selector.Select(ctx)
		cancel()
		if cancelled {
			return finish("cancelled")
		}
	}
	return nil
}
