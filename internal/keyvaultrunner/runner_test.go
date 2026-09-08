package keyvaultrunner

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"forgeapi/internal/store"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/testsuite"
)

func TestNoCredentialOrCommandOverrideInheritance(t *testing.T) {
	for _, k := range []string{"ARM_CLIENT_SECRET", "AZURE_CLIENT_SECRET", "TF_CLI_ARGS_apply", "TF_LOG", "TF_VAR_name", "TF_CLI_CONFIG_FILE", "ARM_USE_MSI"} {
		t.Setenv(k, "override-canary")
	}
	env := strings.Join(commandEnv(), "\n")
	if strings.Contains(env, "canary") || !strings.Contains(env, "TF_CLI_CONFIG_FILE=/dev/null") {
		t.Fatal("untrusted command environment")
	}
}
func TestWorkspaceFence(t *testing.T) {
	a := &Runner{Root: t.TempDir()}
	if _, err := a.workspace("../../escape"); err == nil {
		t.Fatal("path escape")
	}
	id := "dep_" + strings.Repeat("a", 32)
	dir, err := a.workspace(id)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(dir)
	if err != nil || info.Mode().Perm() != 0700 {
		t.Fatal("unrestricted workspace")
	}
	id2 := "dep_" + strings.Repeat("b", 32)
	if err = os.Symlink(t.TempDir(), filepath.Join(a.Root, id2)); err != nil {
		t.Fatal(err)
	}
	if _, err = a.workspace(id2); err == nil {
		t.Fatal("workspace symlink admitted")
	}
}
func TestApplyNeverRetriesAutomatically(t *testing.T) {
	for _, fail := range []bool{false, true} {
		var suite testsuite.WorkflowTestSuite
		env := suite.NewTestWorkflowEnvironment()
		calls, failed := 0, 0
		env.RegisterActivityWithOptions(func(context.Context, store.DeploymentIntent) error {
			calls++
			if fail {
				return errors.New("simulated lost apply acknowledgement")
			}
			return nil
		}, activity.RegisterOptions{Name: "KeyVaultRun"})
		env.RegisterActivityWithOptions(func(context.Context, store.DeploymentIntent) error { failed++; return nil }, activity.RegisterOptions{Name: "KeyVaultFailed"})
		env.ExecuteWorkflow(Workflow, store.DeploymentIntent{ID: "dep_test", Phase: "apply"})
		if !env.IsWorkflowCompleted() || calls != 1 || (env.GetWorkflowError() != nil) != fail || failed != map[bool]int{false: 0, true: 1}[fail] {
			t.Fatalf("calls=%d failed=%d error=%v", calls, failed, env.GetWorkflowError())
		}
	}
}
