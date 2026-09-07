package orchestration

import (
	"context"
	"crypto/sha256"
	"fmt"
	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"time"
)

type Activities struct{ Store *store.Store }

func (a *Activities) FakeStep(ctx context.Context, step Step) error {
	return a.Store.Update(ctx, step.ID, func(r *execution.Record) error { return Apply(r, step.Name, time.Now().UTC()) })
}

// Apply simulates compute effects only. Transaction replay cannot duplicate events/artifacts.
func Apply(r *execution.Record, name string, now time.Time) error {
	if r.Applied[name] || execution.Terminal(r.Execution.State) {
		return nil
	}
	// Recheck persisted cancellation and acceptance-relative deadline inside the lock.
	if !now.Before(r.Execution.DeadlineAt) {
		name = "timed_out"
	} else if r.Execution.Cancellation != nil {
		name = "cancelled"
	}
	old := r.Execution.State
	allowed := map[string]string{"provisioning": "accepted", "starting": "provisioning", "running": "starting", "succeeded": "running"}
	if before, ok := allowed[name]; ok {
		if old != before {
			return fmt.Errorf("invalid transition %s to %s", old, name)
		}
	} else if name != "cancelled" && name != "timed_out" {
		return fmt.Errorf("unknown fake step")
	}
	r.Applied[name] = true
	e := &r.Execution
	e.State = name
	e.ObservedAt = &now
	if name == "provisioning" {
		e.InfrastructureStatus = "present"
	}
	if name == "running" {
		e.StartedAt = &now
	}
	if execution.Terminal(name) {
		e.CompletedAt = &now
		e.CleanupState = "succeeded"
		e.InfrastructureStatus = "absent"
		if e.Cancellation != nil {
			if name == "cancelled" {
				e.Cancellation.Status = "acknowledged"
			} else {
				e.Cancellation.Status = "superseded"
			}
		}
		if name == "succeeded" {
			// Deliberately synthetic output: no source repository is downloaded or tested.
			r.ArtifactData = []byte("{\"demo\":true,\"notice\":\"SIMULATED SBOM: no workload or source tests were executed\",\"packages\":[]}\n")
			digest := sha256.Sum256(r.ArtifactData)
			exit := 0
			e.ResultComplete = true
			e.DeliveryStatus = "complete"
			r.Result = execution.Result{ExecutionID: e.ID, ExitCode: &exit, DeliveryStatus: "complete", ResultComplete: true, Artifacts: []execution.Artifact{{ID: "artifact-sbom", Name: "sbom", Digest: fmt.Sprintf("sha256:%x", digest), SizeBytes: len(r.ArtifactData), MediaType: "application/json", DownloadURL: e.Links["self"] + "/artifacts/artifact-sbom", ExpiresAt: now.Add(24 * time.Hour)}}}
		} else {
			e.DeliveryStatus = "partial"
			if name == "timed_out" {
				e.TimedOutFrom = old
				e.Error = &execution.Failure{Code: "execution_timed_out", Message: "Acceptance-relative deadline elapsed in the simulator.", Origin: "platform"}
			}
			r.Result.DeliveryStatus = "partial"
			r.Result.Error = e.Error
		}
	}
	r.Logs = append(r.Logs, execution.Log{Sequence: int64(len(r.Logs) + 1), Stream: "stdout", Source: "supervisor", RecordedAt: now, Text: "SIMULATED compute: " + name + " (no real workload launched)"})
	r.Event("execution.state_changed", now)
	return nil
}
