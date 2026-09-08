package keyvaultrunner

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"time"

	"forgeapi/internal/deployment"
	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/sdk/client"
)

const purgeRejection = `The property "enablePurgeProtection" cannot be set to false. Enabling the purge protection for a vault is an irreversible action.`

func rejectedPurgeCreate(raw []byte, resourceID, correlation string) bool {
	var events []struct {
		CorrelationID, ResourceID string
		OperationName, Status     struct{ Value string }
		Properties                struct{ StatusCode, StatusMessage string }
	}
	if json.Unmarshal(raw, &events) != nil {
		return false
	}
	found := false
	for _, e := range events {
		if e.CorrelationID != correlation || e.ResourceID != resourceID || e.OperationName.Value != "Microsoft.KeyVault/vaults/write" {
			continue
		}
		if e.Status.Value == "Started" {
			continue
		}
		var result struct {
			Error struct{ Code, Message string }
		}
		if e.Status.Value != "Failed" || e.Properties.StatusCode != "BadRequest" || json.Unmarshal([]byte(e.Properties.StatusMessage), &result) != nil || result.Error.Code != "BadRequest" || result.Error.Message != purgeRejection {
			return false
		}
		found = true
	}
	return found
}

// ConfirmRejected never calls Terraform apply, import, destroy or force-unlock.
// It only recognizes the specific documented pre-creation BadRequest, not a
// timeout/permission/network error or an arbitrary operator assertion of safety.
func (a *Runner) ConfirmRejected(ctx context.Context, c client.Client, id, digest, correlation string) error {
	if !regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`).MatchString(correlation) {
		return errors.New("valid Azure rejection correlation ID required")
	}
	r, err := a.Store.GetDeployment(ctx, id)
	if err != nil {
		return errors.New("original deployment unavailable")
	}
	if (r.State != "recovery_required" && r.State != "rejected_no_effect") || r.PlanDigest != digest || digest == "" {
		return errors.New("original failed plan binding does not match")
	}
	current, err := deployment.LoadTarget(a.TargetPath)
	if err != nil {
		return err
	}
	expected := r.Target
	expected.PatternDigest = current.PatternDigest
	if current != expected {
		return errors.New("recovery cannot change the target or owner")
	}
	authorized := r
	authorized.Target = current
	if err = a.authorize(ctx, authorized); err != nil {
		return err
	}
	desc, err := c.DescribeWorkflowExecution(ctx, deployment.WorkflowID(id, "apply"), "")
	if err != nil || desc.WorkflowExecutionInfo.Status != enumspb.WORKFLOW_EXECUTION_STATUS_FAILED {
		return errors.New("original apply workflow must be closed as failed")
	}
	// The operator stops the native worker before invoking this mode. Refuse if
	// any Terraform process remains on this single-user local development host.
	probe := exec.CommandContext(ctx, "pgrep", "-x", "terraform")
	probe.Env = commandEnv()
	probe.Stdout = io.Discard
	probe.Stderr = io.Discard
	var exited *exec.ExitError
	if err = probe.Run(); !errors.As(err, &exited) || exited.ExitCode() != 1 {
		return errors.New("cannot prove Terraform processes have stopped")
	}
	dir, err := a.workspace(id)
	if err != nil {
		return errors.New("original workspace unavailable")
	}
	if _, err = os.Lstat(filepath.Join(dir, ".terraform.tfstate.lock.info")); !os.IsNotExist(err) {
		return errors.New("Terraform state lock remains; never force-unlock automatically")
	}
	actual, err := digestFile(filepath.Join(dir, "approved.tfplan"))
	if err != nil || actual != digest {
		return errors.New("original plan evidence changed")
	}
	f, err := os.Open(filepath.Join(dir, "terraform.tfstate"))
	if err != nil {
		return errors.New("original state unavailable")
	}
	b, err := io.ReadAll(io.LimitReader(f, 16*1024*1024+1))
	_ = f.Close()
	var state struct {
		Version   int
		Resources []json.RawMessage
	}
	if err != nil || len(b) > 16*1024*1024 || json.Unmarshal(b, &state) != nil || state.Version != 4 || state.Resources == nil || len(state.Resources) != 0 {
		return errors.New("state does not prove an empty failed create")
	}
	if err = a.absent(ctx, r.Target); err != nil {
		return err
	}
	if len(r.Events) == 0 {
		return errors.New("missing original history")
	}
	filter := "eventTimestamp ge '" + r.Events[0].At.Add(-time.Minute).Format(time.RFC3339) + "' and resourceUri eq '" + r.Target.ResourceID() + "'"
	events, err := a.Identity.get(ctx, "/subscriptions/"+r.Target.SubscriptionID+"/providers/Microsoft.Insights/eventtypes/management/values?api-version=2015-04-01&$filter="+url.QueryEscape(filter))
	var result struct {
		Value    json.RawMessage
		NextLink string
	}
	if err != nil || json.Unmarshal(events, &result) != nil || result.NextLink != "" || !rejectedPurgeCreate(result.Value, r.Target.ResourceID(), correlation) {
		return errors.New("Azure activity log does not prove the specific rejected create")
	}
	return a.Store.ConfirmRejectedDeployment(ctx, id, digest, correlation, current)
}
