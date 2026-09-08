package keyvaultrunner

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"forgeapi/internal/deployment"
	"forgeapi/internal/store"
	enumspb "go.temporal.io/api/enums/v1"
	"go.temporal.io/api/serviceerror"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/temporal"
	"go.temporal.io/sdk/workflow"
)

func Workflow(ctx workflow.Context, intent store.DeploymentIntent) error {
	ctx = workflow.WithActivityOptions(ctx, workflow.ActivityOptions{StartToCloseTimeout: 20 * time.Minute, ScheduleToCloseTimeout: 25 * time.Minute, RetryPolicy: &temporal.RetryPolicy{MaximumAttempts: 1}})
	if err := workflow.ExecuteActivity(ctx, "KeyVaultRun", intent).Get(ctx, nil); err != nil {
		cleanup, _ := workflow.NewDisconnectedContext(ctx)
		cleanup = workflow.WithActivityOptions(cleanup, workflow.ActivityOptions{StartToCloseTimeout: 10 * time.Second, ScheduleToCloseTimeout: time.Minute, RetryPolicy: &temporal.RetryPolicy{MaximumAttempts: 3}})
		_ = workflow.ExecuteActivity(cleanup, "KeyVaultFailed", intent).Get(cleanup, nil)
		return temporal.NewNonRetryableApplicationError("deployment phase failed; see sanitized deployment state", "deployment_failed", nil)
	}
	return nil
}

// Start intent survives API restarts and ambiguous Temporal Start acknowledgments.
// Apply never gets a new workflow ID or an automatic retry after worker loss.
func (a *Runner) Dispatch(ctx context.Context, c client.Client) {
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()
	for {
		call, cancel := context.WithTimeout(ctx, 10*time.Second)
		pending, err := a.Store.PendingDeployments(call)
		if err == nil {
			for _, intent := range pending {
				_, err = c.ExecuteWorkflow(call, client.StartWorkflowOptions{ID: deployment.WorkflowID(intent.ID, intent.Phase), TaskQueue: deployment.TaskQueue, WorkflowIDReusePolicy: enumspb.WORKFLOW_ID_REUSE_POLICY_REJECT_DUPLICATE, WorkflowExecutionTimeout: 30 * time.Minute}, Workflow, intent)
				var exists *serviceerror.WorkflowExecutionAlreadyStarted
				if err == nil || errors.As(err, &exists) {
					_ = a.Store.DeploymentDispatched(call, intent.ID, intent.Phase)
				}
			}
		} else if ctx.Err() == nil {
			slog.Warn("deployment outbox temporarily unavailable")
		}
		active, err := a.Store.ActiveDeployments(call)
		if err == nil {
			for _, intent := range active {
				desc, err := c.DescribeWorkflowExecution(call, deployment.WorkflowID(intent.ID, intent.Phase), "")
				if err == nil && desc.WorkflowExecutionInfo.Status != enumspb.WORKFLOW_EXECUTION_STATUS_RUNNING {
					_ = a.Failed(call, intent)
				}
			}
		}
		cancel()
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
	}
}
