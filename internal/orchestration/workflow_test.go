package orchestration

import (
	"context"
	"forgeapi/internal/execution"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/testsuite"
	"go.temporal.io/sdk/workflow"
	"testing"
	"time"
)

func TestWorkflow(t *testing.T) {
	for _, tc := range []struct {
		name    string
		timeout time.Duration
		cancel  bool
		want    string
	}{{"success", time.Minute, false, "succeeded"}, {"cancellation", time.Minute, true, "cancelled"}, {"deadline", time.Second, false, "timed_out"}} {
		t.Run(tc.name, func(t *testing.T) {
			var suite testsuite.WorkflowTestSuite
			env := suite.NewTestWorkflowEnvironment()
			now := time.Date(2026, 9, 7, 12, 0, 0, 0, time.UTC)
			env.SetStartTime(now)
			env.OnGetVersion("local-core-lifecycle", workflow.DefaultVersion, 1).Return(workflow.DefaultVersion)
			steps := []string{}
			env.RegisterActivityWithOptions(func(_ context.Context, s Step) error { steps = append(steps, s.Name); return nil }, activity.RegisterOptions{Name: "FakeStep"})
			if tc.cancel {
				env.RegisterDelayedCallback(func() { env.SignalWorkflow(CancelSignal, "requested") }, time.Second)
			}
			env.ExecuteWorkflow(Execution, Input{ID: "exec_test12345", Deadline: now.Add(tc.timeout)})
			if !env.IsWorkflowCompleted() || env.GetWorkflowError() != nil {
				t.Fatalf("workflow error %v", env.GetWorkflowError())
			}
			if len(steps) == 0 || steps[len(steps)-1] != tc.want {
				t.Fatalf("steps %v", steps)
			}
		})
	}
}

func TestFakeIdempotencyAndTerminalImmutability(t *testing.T) {
	now := time.Now().UTC()
	r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), now)
	for _, s := range []string{"provisioning", "starting", "running", "succeeded"} {
		if err := Apply(&r, s, now); err != nil {
			t.Fatal(err)
		}
		revision := r.Execution.Revision
		if err := Apply(&r, s, now); err != nil || r.Execution.Revision != revision {
			t.Fatal("retry duplicated effects")
		}
	}
	if err := Apply(&r, "cancelled", now); err != nil {
		t.Fatal(err)
	}
	if r.Execution.State != "succeeded" || !r.Result.ResultComplete || len(r.Result.Artifacts) != 1 || r.Execution.InfrastructureStatus != "absent" {
		t.Fatal("terminal/result invariant")
	}
}

func TestFakeRechecksCancellationAndDeadline(t *testing.T) {
	now := time.Now().UTC()
	for _, tc := range []struct {
		name    string
		expired bool
		want    string
	}{{"cancelled", false, "cancelled"}, {"deadline wins", true, "timed_out"}} {
		t.Run(tc.name, func(t *testing.T) {
			r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), now)
			r.Execution.Cancellation = &execution.Cancellation{Status: "requested"}
			when := now
			if tc.expired {
				when = r.Execution.DeadlineAt
			}
			if err := Apply(&r, "provisioning", when); err != nil {
				t.Fatal(err)
			}
			if r.Execution.State != tc.want || r.Result.ExitCode != nil || r.Execution.StartedAt != nil {
				t.Fatal("invented workload observation")
			}
		})
	}
}

func TestFakeRejectsInvalidTransition(t *testing.T) {
	r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), time.Now())
	if Apply(&r, "succeeded", time.Now()) == nil {
		t.Fatal("accepted skipped phases")
	}
}
