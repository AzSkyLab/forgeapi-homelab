package orchestration

import (
	"time"

	"forgeapi/internal/execution"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"
)

func coreWorkflow(ctx workflow.Context, in Input) error {
	ctx = workflow.WithActivityOptions(ctx, workflow.ActivityOptions{StartToCloseTimeout: 10 * time.Second, ScheduleToCloseTimeout: 30 * time.Second,
		RetryPolicy: &temporal.RetryPolicy{InitialInterval: time.Second, MaximumInterval: 5 * time.Second, MaximumAttempts: 5}})
	signals := workflow.GetSignalChannel(ctx, CancelSignal)
	step := func(name string) (string, error) {
		var state string
		for {
			err := workflow.ExecuteActivity(ctx, "CoreStep", Step{in.ID, name}).Get(ctx, &state)
			if err == nil {
				return state, nil
			}
			// Storage outages cannot silently lose a terminal projection. Retry
			// through durable timers under the original lifecycle deadline.
			if workflow.Now(ctx).After(in.Deadline.Add(2 * time.Minute)) {
				return "", err
			}
			if !workflow.Now(ctx).Before(in.Deadline) {
				name = "timed_out"
			}
			if err := workflow.Sleep(ctx, 3*time.Second); err != nil {
				return "", err
			}
		}
	}
	for _, name := range []string{"provisioning", "starting", "running", "succeeded"} {
		var request string
		if !workflow.Now(ctx).Before(in.Deadline) {
			name = "timed_out"
		} else if signals.ReceiveAsync(&request) {
			name = "cancelled"
		}
		state, err := step(name)
		if err != nil {
			return err
		} // Independent DB sweeper handles a closed workflow.
		if execution.Terminal(state) {
			break
		}
		delay := 2 * time.Second
		if name == "running" {
			delay = 10 * time.Second
		}
		delay = min(delay, max(time.Duration(0), in.Deadline.Sub(workflow.Now(ctx))))
		timerCtx, cancel := workflow.WithCancel(ctx)
		selector := workflow.NewSelector(ctx)
		selector.AddFuture(workflow.NewTimer(timerCtx, delay), func(workflow.Future) {})
		cancelled := false
		selector.AddReceive(signals, func(c workflow.ReceiveChannel, _ bool) { c.Receive(ctx, &request); cancelled = true })
		selector.Select(ctx)
		cancel()
		if cancelled {
			if _, err := step("cancelled"); err != nil {
				return err
			}
			break
		}
	}
	// Cleanup and delivery are independent; either may finish/retry while the
	// other is unavailable. Durable recovery_tasks outlive this workflow.
	cleanup := workflow.ExecuteActivity(ctx, "Cleanup", in.ID)
	delivery := workflow.ExecuteActivity(ctx, "Deliver", in.ID)
	cleanupErr := cleanup.Get(ctx, nil)
	deliveryErr := delivery.Get(ctx, nil)
	if cleanupErr != nil {
		return cleanupErr
	}
	return deliveryErr
}
