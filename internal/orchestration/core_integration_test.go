//go:build integration

package orchestration

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"testing"
	"time"

	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"go.temporal.io/api/enums/v1"
	historypb "go.temporal.io/api/history/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/api/workflowservice/v1"
	"go.temporal.io/sdk/client"
	temporallog "go.temporal.io/sdk/log"
	"go.temporal.io/sdk/worker"
	"go.temporal.io/sdk/workflow"
)

func integrationStore(t *testing.T) *store.Store {
	t.Helper()
	databaseURL := os.Getenv("FORGE_TEST_DATABASE_URL")
	if databaseURL == "" {
		t.Fatal("FORGE_TEST_DATABASE_URL required; never skip integration proof")
	}
	ctx := context.Background()
	admin, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatal(err)
	}
	schema := execution.ID("orchestration_test_")
	quoted := pgx.Identifier{schema}.Sanitize()
	if _, err = admin.Exec(ctx, "CREATE SCHEMA "+quoted); err != nil {
		t.Fatal(err)
	}
	cfg, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = schema
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		t.Fatal(err)
	}
	s := &store.Store{Pool: pool}
	t.Cleanup(func() {
		pool.Close()
		if _, err := admin.Exec(context.Background(), "DROP SCHEMA "+quoted+" CASCADE"); err != nil {
			t.Error(err)
		}
		admin.Close()
	})
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return s
}

func acceptedExecution(t *testing.T, s *store.Store) execution.Execution {
	t.Helper()
	a, err := s.Submit(context.Background(), "alice", execution.ID("integration-"), "hash", "http://localhost:8080", execution.Defaults())
	if err != nil {
		t.Fatal(err)
	}
	var e execution.Execution
	if err = json.Unmarshal(a.Body, &e); err != nil {
		t.Fatal(err)
	}
	return e
}

type missingTemporal struct {
	starts int
	fail   bool
}

func (c *missingTemporal) DescribeWorkflowExecution(context.Context, string, string) (*workflowservice.DescribeWorkflowExecutionResponse, error) {
	return nil, serviceerror.NewNotFound("test absent")
}
func (c *missingTemporal) ExecuteWorkflow(context.Context, client.StartWorkflowOptions, interface{}, ...interface{}) (client.WorkflowRun, error) {
	c.starts++
	if c.fail {
		return nil, errors.New("test transport outage")
	}
	return nil, nil
}
func (*missingTemporal) SignalWorkflow(context.Context, string, string, string, interface{}) error {
	return nil
}

func TestPreDispatchPolicyAndExpiry(t *testing.T) {
	for _, mode := range []string{"revoked", "cancelled", "expired", "retry"} {
		t.Run(mode, func(t *testing.T) {
			s := integrationStore(t)
			e := acceptedExecution(t, s)
			ctx := context.Background()
			if mode == "expired" {
				if err := s.Update(ctx, e.ID, func(r *execution.Record) error { r.Execution.DeadlineAt = time.Now().Add(-time.Second); return nil }); err != nil {
					t.Fatal(err)
				}
			}
			if mode == "cancelled" {
				if _, err := s.Cancel(ctx, "alice", e.ID, "cancel-key-00001", "hash", ""); err != nil {
					t.Fatal(err)
				}
			}
			c := &missingTemporal{fail: mode == "retry"}
			err := DispatchOnce(ctx, s, c, func(context.Context, execution.Record) (bool, error) { return mode != "revoked", nil })
			if mode == "retry" {
				if err == nil {
					t.Fatal("missing retry error")
				}
				if pending, err := s.Pending(ctx); err != nil || len(pending) != 0 {
					t.Fatal("failed dispatch lacks backoff", err)
				}
				return
			}
			if err != nil || c.starts != 0 {
				t.Fatal("forbidden/expired intent launched", err)
			}
			r, err := s.Get(ctx, e.ID)
			if err != nil || !execution.Terminal(r.Execution.State) || r.Execution.CleanupState != "succeeded" {
				t.Fatal("did not reconcile undispatched intent", err)
			}
		})
	}
}

func TestPersistentEffectRecoveryAndLateSubmission(t *testing.T) {
	s := integrationStore(t)
	e := acceptedExecution(t, s)
	ctx := context.Background()
	a := Activities{Store: s}
	// Crash boundary: provider accepted, projection never committed.
	if err := s.AllocateSimulation(ctx, e.ID); err != nil {
		t.Fatal(err)
	}
	for _, step := range []string{"provisioning", "starting", "running", "succeeded"} {
		if _, err := a.CoreStep(ctx, Step{e.ID, step}); err != nil {
			t.Fatal(err)
		}
	}
	r, _ := s.Get(ctx, e.ID)
	if r.Execution.ResultComplete || r.Execution.CleanupState == "succeeded" {
		t.Fatal("fabricated finalization")
	}
	// Simulate process loss after terminal observation: no workflow code runs.
	if err := ReconcileOnce(ctx, s, &missingTemporal{}); err != nil {
		t.Fatal(err)
	}
	if err := s.AllocateSimulation(ctx, e.ID); err != nil {
		t.Fatal(err)
	}
	if _, err := a.CoreStep(ctx, Step{e.ID, "provisioning"}); err != nil {
		t.Fatal(err)
	}
	var present, closed bool
	var allocations int
	if err := s.Pool.QueryRow(ctx, "SELECT present,submission_closed,allocation_count FROM simulated_resources WHERE execution_id=$1", e.ID).Scan(&present, &closed, &allocations); err != nil {
		t.Fatal(err)
	}
	if present || !closed || allocations != 1 {
		t.Fatal("late retry reallocated or erased tombstone")
	}
	r, _ = s.Get(ctx, e.ID)
	if !r.Execution.ResultComplete || r.Execution.CleanupState != "succeeded" {
		t.Fatal("sweeper lost outcome/delivery/cleanup")
	}
	ids, err := s.RecoveryPending(ctx)
	if err != nil || len(ids) != 0 {
		t.Fatal("completed recovery ticket remains", err)
	}
}

func TestBlockedCleanupKeepsTicketAndOutcome(t *testing.T) {
	s := integrationStore(t)
	e := acceptedExecution(t, s)
	a := Activities{Store: s}
	ctx := context.Background()
	for _, step := range []string{"provisioning", "starting", "running", "succeeded"} {
		if _, err := a.CoreStep(ctx, Step{e.ID, step}); err != nil {
			t.Fatal(err)
		}
	}
	if err := s.Update(ctx, e.ID, func(r *execution.Record) error {
		past := time.Now().Add(-2 * time.Minute)
		r.Execution.CompletedAt = &past
		return nil
	}); err != nil {
		t.Fatal(err)
	}
	// A real PostgreSQL row lock makes the external-effect cleanup call time
	// out. Delivery uses a different transaction and must still complete.
	lock, err := s.Pool.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer lock.Rollback(ctx)
	if _, err = lock.Exec(ctx, "SELECT execution_id FROM simulated_resources WHERE execution_id=$1 FOR UPDATE", e.ID); err != nil {
		t.Fatal(err)
	}
	if err = ReconcileOnce(ctx, s, &missingTemporal{}); err == nil {
		t.Fatal("blocked cleanup did not report failure")
	}
	r, err := s.Get(ctx, e.ID)
	if err != nil {
		t.Fatal(err)
	}
	if r.Execution.State != "succeeded" || !r.Result.ResultComplete || r.Execution.CleanupState != "failed" || r.Execution.CleanupError == nil || r.Execution.InfrastructureStatus != "present" {
		t.Fatal("cleanup failure erased outcome or fabricated absence")
	}
	var count int
	if err = s.Pool.QueryRow(ctx, "SELECT count(*) FROM recovery_tasks WHERE execution_id=$1", e.ID).Scan(&count); err != nil || count != 1 {
		t.Fatal("lost recovery ticket", err)
	}
	if err = lock.Rollback(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err = s.Pool.Exec(ctx, "UPDATE recovery_tasks SET next_attempt_at=now() WHERE execution_id=$1", e.ID); err != nil {
		t.Fatal(err)
	}
	if err = ReconcileOnce(ctx, s, &missingTemporal{}); err != nil {
		t.Fatal(err)
	}
	r, err = s.Get(ctx, e.ID)
	if err != nil || r.Execution.CleanupState != "succeeded" || r.Execution.CleanupError != nil || r.Execution.State != "succeeded" {
		t.Fatal("cleanup recovery did not converge", err)
	}
}

// This test function is also the dedicated worker subprocess entrypoint.
// The parent kills the OS process, not a mocked worker or graceful stop.
func TestWorkerProcess(t *testing.T) {
	if os.Getenv("FORGE_TEST_WORKER_PROCESS") != "1" {
		return
	}
	cfg, err := pgxpool.ParseConfig(os.Getenv("FORGE_TEST_DATABASE_URL"))
	if err != nil {
		t.Fatal(err)
	}
	cfg.ConnConfig.RuntimeParams["search_path"] = os.Getenv("FORGE_TEST_SCHEMA")
	pool, err := pgxpool.NewWithConfig(context.Background(), cfg)
	if err != nil {
		t.Fatal(err)
	}
	c, err := client.Dial(client.Options{HostPort: os.Getenv("FORGE_TEST_TEMPORAL_ADDRESS")})
	if err != nil {
		t.Fatal(err)
	}
	w := worker.New(c, TaskQueue, worker.Options{})
	w.RegisterWorkflow(Execution)
	w.RegisterWorkflow(legacyWorkflow)
	w.RegisterActivity(&Activities{Store: &store.Store{Pool: pool}})
	if err = w.Start(); err != nil {
		t.Fatal(err)
	}
	fmt.Println("worker-ready")
	select {}
}

func startWorkerProcess(t *testing.T, s *store.Store) *exec.Cmd {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^TestWorkerProcess$", "-test.timeout=2m")
	cmd.Env = append(os.Environ(), "FORGE_TEST_WORKER_PROCESS=1", "FORGE_TEST_SCHEMA="+s.Pool.Config().ConnConfig.RuntimeParams["search_path"])
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	cmd.Stderr = io.Discard
	if err = cmd.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = cmd.Process.Kill(); _ = cmd.Wait() })
	ready := make(chan bool, 1)
	go func() {
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			if scanner.Text() == "worker-ready" {
				ready <- true
				_, _ = io.Copy(io.Discard, stdout)
				return
			}
		}
		ready <- false
	}()
	select {
	case ok := <-ready:
		if !ok {
			t.Fatal("worker failed before readiness")
		}
	case <-time.After(20 * time.Second):
		t.Fatal("worker startup timed out")
	}
	return cmd
}

func TestTemporalWorkerKillRestartAndReplay(t *testing.T) {
	address := os.Getenv("FORGE_TEST_TEMPORAL_ADDRESS")
	if address == "" {
		t.Fatal("FORGE_TEST_TEMPORAL_ADDRESS required; never skip real Temporal proof")
	}
	s := integrationStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 75*time.Second)
	defer cancel()
	c, err := client.DialContext(ctx, client.Options{HostPort: address})
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	first := startWorkerProcess(t, s)
	e := acceptedExecution(t, s)
	if err = DispatchOnce(ctx, s, c, func(context.Context, execution.Record) (bool, error) { return true, nil }); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.TerminateWorkflow(context.Background(), e.ID, "", "isolated integration test cleanup") })
	wait := func(predicate func(execution.Record) bool) execution.Record {
		for {
			r, err := s.Get(ctx, e.ID)
			if err != nil {
				t.Fatal(err)
			}
			if predicate(r) {
				return r
			}
			select {
			case <-ctx.Done():
				t.Fatal("execution did not converge")
			case <-time.After(100 * time.Millisecond):
			}
		}
	}
	wait(func(r execution.Record) bool { return r.Execution.State == "running" })
	if err = first.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = first.Wait()
	startWorkerProcess(t, s)
	r := wait(func(r execution.Record) bool {
		return execution.Terminal(r.Execution.State) && r.Execution.CleanupState == "succeeded" && r.Execution.ResultComplete
	})
	if r.Execution.State != "succeeded" {
		t.Fatal("outcome changed after restart")
	}
	if err = c.GetWorkflow(ctx, e.ID, "").Get(ctx, nil); err != nil {
		t.Fatal(err)
	}
	var allocations int
	if err = s.Pool.QueryRow(ctx, "SELECT allocation_count FROM simulated_resources WHERE execution_id=$1", e.ID).Scan(&allocations); err != nil || allocations != 1 {
		t.Fatal("restart duplicated allocation", err)
	}
	for i, event := range r.Events {
		if event.Sequence != int64(i+1) || event.Revision != int64(i+1) {
			t.Fatal("event order/revision gap")
		}
	}
	history := &historypb.History{}
	iterator := c.GetWorkflowHistory(ctx, e.ID, "", false, enums.HISTORY_EVENT_FILTER_TYPE_ALL_EVENT)
	for iterator.HasNext() {
		event, err := iterator.Next()
		if err != nil {
			t.Fatal(err)
		}
		history.Events = append(history.Events, event)
	}
	replayer := worker.NewWorkflowReplayer()
	replayer.RegisterWorkflow(Execution)
	if err = replayer.ReplayWorkflowHistory(temporallog.NewStructuredLogger(slog.New(slog.NewTextHandler(io.Discard, nil))), history); err != nil {
		t.Fatal("real-history replay failed", err)
	}
	t.Logf("Killed/restarted real worker; one allocation; %d ordered projection events; replayed %d Temporal events", len(r.Events), len(history.Events))
	legacy := acceptedExecution(t, s)
	legacyRun, err := c.ExecuteWorkflow(ctx, client.StartWorkflowOptions{ID: legacy.ID, TaskQueue: TaskQueue}, legacyWorkflow, Input{ID: legacy.ID, Deadline: time.Now().Add(time.Second)})
	if err != nil {
		t.Fatal(err)
	}
	if err = legacyRun.Get(ctx, nil); err != nil {
		t.Fatal(err)
	}
	oldHistory := &historypb.History{}
	oldIterator := c.GetWorkflowHistory(ctx, legacy.ID, "", false, enums.HISTORY_EVENT_FILTER_TYPE_ALL_EVENT)
	for oldIterator.HasNext() {
		event, err := oldIterator.Next()
		if err != nil {
			t.Fatal(err)
		}
		oldHistory.Events = append(oldHistory.Events, event)
	}
	legacyReplayer := worker.NewWorkflowReplayer()
	legacyReplayer.RegisterWorkflowWithOptions(Execution, workflow.RegisterOptions{Name: "legacyWorkflow"})
	if err = legacyReplayer.ReplayWorkflowHistory(temporallog.NewStructuredLogger(slog.New(slog.NewTextHandler(io.Discard, nil))), oldHistory); err != nil {
		t.Fatal("pre-version-marker history no longer replays", err)
	}
	t.Logf("Current workflow code also replayed %d legacy history events without a version marker", len(oldHistory.Events))
}
