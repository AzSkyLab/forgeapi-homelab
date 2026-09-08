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
	return apply(r, name, now, true)
}

func apply(r *execution.Record, name string, now time.Time, finalize bool) error {
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
	failure := name == "failed" || name == "policy_denied" || name == "capacity_failed" || name == "image_failed" || name == "exit_nonzero"
	if failure {
		code := "execution_failed"
		message := "The synthetic execution could not complete."
		if name == "policy_denied" {
			code, message = "policy_denied", "Current policy no longer allows dispatch."
		}
		origin := "platform"
		switch name {
		case "capacity_failed":
			code, message, origin = "capacity_unavailable", "Synthetic provider capacity was unavailable.", "provider"
		case "image_failed":
			code, message, origin = "image_unavailable", "Synthetic image resolution failed.", "provider"
		case "exit_nonzero":
			if old != "running" {
				return fmt.Errorf("workload exit without observed start")
			}
			code, message, origin = "workload_failed", "Synthetic workload returned exit code 1.", "workload"
			exit := 1
			r.Result.ExitCode = &exit
		}
		r.Execution.Error = &execution.Failure{Code: code, Message: message, Origin: origin}
		name = "failed"
	}
	allowed := map[string]string{"provisioning": "accepted", "starting": "provisioning", "running": "starting", "succeeded": "running"}
	if before, ok := allowed[name]; ok {
		if old != before {
			return fmt.Errorf("invalid transition %s to %s", old, name)
		}
	} else if name != "cancelled" && name != "timed_out" && name != "failed" {
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
		if finalize {
			e.CleanupState = "succeeded"
			e.InfrastructureStatus = "absent"
		}
		if e.Cancellation != nil {
			if name == "cancelled" {
				e.Cancellation.Status = "acknowledged"
			} else {
				e.Cancellation.Status = "superseded"
			}
		}
		if name == "succeeded" && finalize {
			// Deliberately synthetic output: no source repository is downloaded or tested.
			r.ArtifactData = []byte("{\"demo\":true,\"notice\":\"SIMULATED SBOM: no workload or source tests were executed\",\"packages\":[]}\n")
			digest := sha256.Sum256(r.ArtifactData)
			exit := 0
			e.ResultComplete = true
			e.DeliveryStatus = "complete"
			r.Result = execution.Result{ExecutionID: e.ID, ExitCode: &exit, DeliveryStatus: "complete", ResultComplete: true, Artifacts: []execution.Artifact{{ID: "artifact-sbom", Name: "sbom", Digest: fmt.Sprintf("sha256:%x", digest), SizeBytes: len(r.ArtifactData), MediaType: "application/json", DownloadURL: e.Links["self"] + "/artifacts/artifact-sbom", ExpiresAt: now.Add(24 * time.Hour)}}}
		} else if name != "succeeded" {
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
