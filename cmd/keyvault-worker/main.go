// Trusted local host worker; never package with host Azure credentials in Docker.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"forgeapi/internal/deployment"
	"forgeapi/internal/keyvaultrunner"
	"forgeapi/internal/store"
	keyvault "forgeapi/patterns/key-vault"
	"go.temporal.io/sdk/activity"
	"go.temporal.io/sdk/client"
	"go.temporal.io/sdk/worker"
)

func main() {
	if err := run(); err != nil {
		slog.Error("Key Vault worker stopped", "reason", err.Error())
		os.Exit(1)
	}
}
func run() error {
	target := flag.String("target", "config/deployment.local.json", "approved local target")
	grants := flag.String("grants", "config/grants.local.json", "current owner grants")
	root := flag.String("state-dir", ".local/deployments", "restricted persistent local state")
	identity := flag.String("identity", "config/terraform-executor.local.json", "explicit home-lab certificate executor; no CLI fallback")
	verifyIdentity := flag.Bool("verify-identity", false, "read-only certificate/ARM/Terraform check against the existing vault; no apply or workflow")
	printDigest := flag.Bool("print-pattern-digest", false, "print embedded Terraform/lock content digest and exit")
	confirmRejection := flag.String("confirm-rejection", "", "operator recovery: prove a specific rejected create had no effect; never applies")
	failedPlan := flag.String("failed-plan-digest", "", "original failed saved-plan digest for recovery")
	azureCorrelation := flag.String("azure-correlation", "", "Azure activity-log correlation for the rejected create")
	flag.Parse()
	if *printDigest {
		fmt.Println(keyvault.Digest())
		return nil
	}
	if os.Getenv("LOCAL_CONTAINER") == "true" {
		return errors.New("this trusted runner must run on the local host, not a workload container")
	}
	t, err := deployment.LoadTarget(*target)
	if err != nil {
		return err
	}
	if t.PatternDigest != keyvault.Digest() {
		return errors.New("configured pattern digest differs from this worker")
	}
	tf, err := exec.LookPath("terraform")
	if err != nil {
		return errors.New("install Terraform 1.15.9 on the host")
	}
	tf, err = filepath.Abs(tf)
	if err != nil {
		return err
	}
	abs, err := filepath.Abs(*root)
	if err != nil {
		return err
	}
	// Local development dependencies only; no arbitrary remote database/Temporal endpoint.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	executor, err := keyvaultrunner.LoadExecutor(*identity)
	if err != nil {
		return err
	}
	a := &keyvaultrunner.Runner{TargetPath: *target, GrantsPath: *grants, Root: abs, Terraform: tf, Identity: executor}
	if *verifyIdentity {
		check, cancel := context.WithTimeout(ctx, 3*time.Minute)
		defer cancel()
		if err = a.CheckIdentity(check); err != nil {
			return err
		}
		fmt.Println("PASS: certificate-only executor authenticated; ARM and Terraform read the existing approved vault. No Azure writes or apply performed.")
		fmt.Println("Executor client:", t.Executor.ClientID, "Principal:", t.Executor.PrincipalID)
		return nil
	}
	connect, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	s, err := store.Open(connect, "postgres://forge:local-fixture-only@127.0.0.1:54329/forge?sslmode=disable")
	if err != nil {
		return errors.New("local PostgreSQL unavailable")
	}
	defer s.Pool.Close()
	c, err := client.DialContext(connect, client.Options{HostPort: "127.0.0.1:7233"})
	if err != nil {
		return errors.New("local Temporal unavailable")
	}
	defer c.Close()
	a.Store = s
	if *confirmRejection != "" {
		check, cancel := context.WithTimeout(ctx, 2*time.Minute)
		defer cancel()
		if err := a.ConfirmRejected(check, c, *confirmRejection, *failedPlan, *azureCorrelation); err != nil {
			return err
		}
		fmt.Println("Verified rejection with no resource/state effect; original evidence retained. A fresh API request/plan/approval is required.")
		return nil
	}
	w := worker.New(c, deployment.TaskQueue, worker.Options{MaxConcurrentActivityExecutionSize: 1, WorkerStopTimeout: 5 * time.Second})
	w.RegisterWorkflow(keyvaultrunner.Workflow)
	w.RegisterActivityWithOptions(a.Run, activity.RegisterOptions{Name: "KeyVaultRun"})
	w.RegisterActivityWithOptions(a.Failed, activity.RegisterOptions{Name: "KeyVaultFailed"})
	if err = w.Start(); err != nil {
		return errors.New("local deployment worker startup failed")
	}
	defer w.Stop()
	slog.Warn("LIVE AZURE: one approved empty Key Vault target; plan approval required; no automatic apply retry", "resource_id", t.ResourceID())
	a.Dispatch(ctx, c)
	return nil
}
