package orchestration

import (
	"context"
	"errors"
	"testing"
	"time"

	"forgeapi/internal/execution"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/testsuite"
)

func TestCoreWorkflow(t *testing.T) {
	for _, tc := range []struct {
		name, want    string
		cancel        bool
		timeout       time.Duration
		deliveryFails bool
	}{
		{"success", "succeeded", false, time.Minute, false},
		{"cancel", "cancelled", true, time.Minute, false},
		{"timeout", "timed_out", false, time.Second, false},
		{"delivery-outage", "succeeded", false, time.Minute, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var suite testsuite.WorkflowTestSuite
			env := suite.NewTestWorkflowEnvironment()
			now := time.Now().UTC()
			env.SetStartTime(now)
			last, cleaned := "", false
			env.RegisterActivityWithOptions(func(_ context.Context, step Step) (string, error) { last = step.Name; return step.Name, nil }, activity.RegisterOptions{Name: "CoreStep"})
			env.RegisterActivityWithOptions(func(context.Context, string) error { cleaned = true; return nil }, activity.RegisterOptions{Name: "Cleanup"})
			env.RegisterActivityWithOptions(func(context.Context, string) error {
				if tc.deliveryFails {
					return errors.New("test delivery outage")
				}
				return nil
			}, activity.RegisterOptions{Name: "Deliver"})
			if tc.cancel {
				env.RegisterDelayedCallback(func() { env.SignalWorkflow(CancelSignal, "requested") }, time.Second)
			}
			env.ExecuteWorkflow(Execution, Input{ID: "exec_test12345", Deadline: now.Add(tc.timeout)})
			if !env.IsWorkflowCompleted() || last != tc.want || !cleaned {
				t.Fatalf("last=%s cleaned=%v", last, cleaned)
			}
			if (env.GetWorkflowError() != nil) != tc.deliveryFails {
				t.Fatal(env.GetWorkflowError())
			}
		})
	}
}

func TestCoreSeparatesOutcomeDeliveryAndCleanup(t *testing.T) {
	now := time.Now().UTC()
	r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), now)
	for _, step := range []string{"provisioning", "starting", "running", "succeeded"} {
		if err := apply(&r, step, now, false); err != nil {
			t.Fatal(err)
		}
	}
	if r.Execution.State != "succeeded" || r.Execution.ResultComplete || r.Execution.InfrastructureStatus != "present" || r.Execution.CleanupState == "succeeded" {
		t.Fatal("outcome fabricated delivery/cleanup proof")
	}
	if err := deliver(&r, now); err != nil {
		t.Fatal(err)
	}
	revision := r.Execution.Revision
	if err := deliver(&r, now); err != nil || revision != r.Execution.Revision {
		t.Fatal("delivery retry changed result")
	}
	if !r.Result.ResultComplete || r.Execution.CleanupState == "succeeded" {
		t.Fatal("delivery changed cleanup")
	}
}

func TestSyntheticFailureOriginsAndObservedExit(t *testing.T) {
	for _, tc := range []struct {
		name, origin string
		steps        []string
		exit         bool
	}{
		{"capacity_failed", "provider", nil, false},
		{"image_failed", "provider", []string{"provisioning"}, false},
		{"exit_nonzero", "workload", []string{"provisioning", "starting", "running"}, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			now := time.Now().UTC()
			r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), now)
			for _, step := range append(tc.steps, tc.name) {
				if err := apply(&r, step, now, false); err != nil {
					t.Fatal(err)
				}
			}
			if r.Execution.State != "failed" || r.Execution.Error.Origin != tc.origin || (r.Result.ExitCode != nil) != tc.exit {
				t.Fatal("failure classification invented or lost observation")
			}
			if err := deliver(&r, now); err != nil {
				t.Fatal(err)
			}
			if r.Result.ResultComplete != tc.exit {
				t.Fatal("delivery completeness conflated with outcome")
			}
			if tc.exit && *r.Result.ExitCode != 1 {
				t.Fatal("nonzero exit rewritten")
			}
		})
	}
}
