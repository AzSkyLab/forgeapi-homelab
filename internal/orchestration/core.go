package orchestration

import (
	"context"
	"crypto/sha256"
	"fmt"
	"time"

	"forgeapi/internal/execution"
	"forgeapi/internal/telemetry"
)

// CoreStep is separate from the legacy FakeStep so old histories replay with
// their original activity semantics. Only new workflows use this protocol.
func (a *Activities) CoreStep(ctx context.Context, step Step) (string, error) {
	r, err := a.Store.Get(ctx, step.ID)
	if err != nil {
		return "", err
	}
	ctx, span := telemetry.Span(ctx, r, "execution."+step.Name)
	defer span.End()
	if step.Name == "provisioning" {
		if err := a.Store.AllocateSimulation(ctx, step.ID); err != nil {
			return "", err
		}
	}
	state := ""
	err = a.Store.Update(ctx, step.ID, func(r *execution.Record) error {
		r.CoreVersion = 1
		if err := apply(r, step.Name, time.Now().UTC(), false); err != nil {
			return err
		}
		state = r.Execution.State
		return nil
	})
	return state, err
}

func (a *Activities) Deliver(ctx context.Context, id string) error {
	r, err := a.Store.Get(ctx, id)
	if err != nil {
		return err
	}
	ctx, span := telemetry.Span(ctx, r, "execution.delivery")
	defer span.End()
	return a.Store.Update(ctx, id, func(r *execution.Record) error { return deliver(r, time.Now().UTC()) })
}

func deliver(r *execution.Record, now time.Time) error {
	if !execution.Terminal(r.Execution.State) || r.Applied["delivery"] {
		return nil
	}
	e := &r.Execution
	if e.State == "succeeded" || r.Result.ExitCode != nil {
		r.ArtifactData = []byte("{\"demo\":true,\"notice\":\"SIMULATED SBOM: no workload or source tests were executed\",\"packages\":[]}\n")
		exit := 0
		if r.Result.ExitCode != nil {
			exit = *r.Result.ExitCode
		}
		r.Result = execution.Result{ExecutionID: e.ID, ExitCode: &exit, Error: e.Error, DeliveryStatus: "complete", ResultComplete: true,
			Artifacts: []execution.Artifact{{ID: "artifact-sbom", Name: "sbom", Digest: fmt.Sprintf("sha256:%x", sha256.Sum256(r.ArtifactData)), SizeBytes: len(r.ArtifactData), MediaType: "application/json", DownloadURL: e.Links["self"] + "/artifacts/artifact-sbom", ExpiresAt: now.Add(24 * time.Hour)}}}
		e.DeliveryStatus, e.ResultComplete = "complete", true
	} else {
		e.DeliveryStatus = "partial"
		r.Result.DeliveryStatus, r.Result.Error = "partial", e.Error
	}
	e.DeliveryError = nil
	r.Applied["delivery"] = true
	r.Event("execution.delivery_changed", now)
	return nil
}

func (a *Activities) Cleanup(ctx context.Context, id string) error {
	r, err := a.Store.Get(ctx, id)
	if err != nil {
		return err
	}
	ctx, span := telemetry.Span(ctx, r, "execution.cleanup")
	defer span.End()
	if err := a.Store.CloseSimulation(ctx, id); err != nil {
		return err
	}
	return a.Store.Update(ctx, id, func(r *execution.Record) error {
		if r.Execution.CleanupState == "succeeded" {
			return nil
		}
		r.Execution.CleanupState, r.Execution.InfrastructureStatus = "succeeded", "absent"
		r.Execution.CleanupError = nil
		r.Event("execution.cleanup_changed", time.Now().UTC())
		return nil
	})
}
