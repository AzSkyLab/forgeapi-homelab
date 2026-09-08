// Package keyvaultrunner runs only the embedded trusted Terraform pattern on the
// local host. It is not a container workload executor or a generic shell service.
package keyvaultrunner

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"forgeapi/internal/auth"
	"forgeapi/internal/deployment"
	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	keyvault "forgeapi/patterns/key-vault"
)

type Runner struct {
	Store                                   *store.Store
	TargetPath, GrantsPath, Root, Terraform string
	Identity                                *ExecutorClient
}

// Commands never inherit provider secrets, TF_CLI_ARGS, logging settings or
// arbitrary TF_VAR overrides. The executor explicitly supplies its approved
// certificate path; human Azure CLI credentials are never used by the worker.
func commandEnv() []string {
	env := []string{"TF_IN_AUTOMATION=1", "CHECKPOINT_DISABLE=1", "TF_CLI_CONFIG_FILE=/dev/null"}
	for _, k := range []string{"PATH", "HOME", "SYSTEMROOT", "SSL_CERT_FILE", "SSL_CERT_DIR"} {
		if v := os.Getenv(k); v != "" {
			env = append(env, k+"="+v)
		}
	}
	return env
}

type boundedBuffer struct {
	data  []byte
	limit int
}

func (b *boundedBuffer) Write(p []byte) (int, error) {
	if len(p) > b.limit-len(b.data) {
		return 0, errors.New("command output limit exceeded")
	}
	b.data = append(b.data, p...)
	return len(p), nil
}
func command(ctx context.Context, dir, name string, args ...string) ([]byte, error) {
	return commandWithEnv(ctx, dir, name, commandEnv(), args...)
}
func commandWithEnv(ctx context.Context, dir, name string, env []string, args ...string) ([]byte, error) {
	c := exec.CommandContext(ctx, name, args...)
	c.Dir = dir
	c.Env = env
	c.WaitDelay = 3 * time.Second
	out := &boundedBuffer{limit: 4 * 1024 * 1024}
	c.Stdout = out
	c.Stderr = io.Discard
	if err := c.Run(); err != nil {
		return nil, errors.New("local command failed; raw diagnostics suppressed")
	}
	return out.data, nil
}
func (a *Runner) tf(ctx context.Context, dir string, args ...string) ([]byte, error) {
	t, err := deployment.LoadTarget(a.TargetPath)
	if err != nil {
		return nil, err
	}
	env, err := a.Identity.terraformEnv(t)
	if err != nil {
		return nil, err
	}
	return commandWithEnv(ctx, dir, a.Terraform, env, args...)
}
func (a *Runner) authorize(ctx context.Context, r deployment.Record) error {
	current, err := deployment.LoadTarget(a.TargetPath)
	if err != nil || current != r.Target || current.PatternDigest != keyvault.Digest() {
		return errors.New("target or pattern approval changed")
	}
	allowed, err := auth.DispatchAuthorizer(a.GrantsPath, current.TenantID)(ctx, execution.Record{Owner: current.Owner, Correlation: execution.Correlation{PrincipalKind: "human"}})
	if err != nil || !allowed {
		return errors.New("current owner grant denied")
	}
	if !a.Identity.matches(current) {
		return errors.New("approved non-human executor unavailable; no CLI fallback")
	}
	if _, err = a.Identity.token(ctx); err != nil {
		return err
	}
	version, err := a.tf(ctx, "", "version", "-json")
	var v struct {
		Version string `json:"terraform_version"`
	}
	if err != nil || json.Unmarshal(version, &v) != nil || v.Version != "1.15.9" {
		return errors.New("Terraform 1.15.9 is required")
	}
	return nil
}
func (a *Runner) workspace(id string) (string, error) {
	if len(id) != 36 || !strings.HasPrefix(id, "dep_") {
		return "", errors.New("invalid deployment ID")
	}
	for _, c := range id[4:] {
		if !strings.ContainsRune("0123456789abcdef", c) {
			return "", errors.New("invalid deployment ID")
		}
	}
	if !filepath.IsAbs(a.Root) {
		return "", errors.New("workspace root must be absolute")
	}
	if err := os.MkdirAll(a.Root, 0700); err != nil {
		return "", err
	}
	resolved, err := filepath.EvalSymlinks(a.Root)
	if err != nil || resolved != filepath.Clean(a.Root) {
		return "", errors.New("workspace symlinks are forbidden")
	}
	dir := filepath.Join(a.Root, id)
	if err = os.Mkdir(dir, 0700); err != nil && !os.IsExist(err) {
		return "", err
	}
	resolved, err = filepath.EvalSymlinks(dir)
	if err != nil || resolved != dir {
		return "", errors.New("workspace symlinks are forbidden")
	}
	if err = os.Chmod(dir, 0700); err != nil {
		return "", err
	}
	return dir, nil
}
func prepare(dir string, t deployment.Target) error {
	for _, name := range []string{"main.tf", "variables.tf", ".terraform.lock.hcl"} {
		b, err := keyvault.Files.ReadFile(name)
		if err != nil {
			return err
		}
		// New plan attempts never overwrite another attempt's evidence.
		f, err := os.OpenFile(filepath.Join(dir, name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		_, err = f.Write(b)
		closeErr := f.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
	}
	b, _ := json.Marshal(map[string]string{"tenant_id": t.TenantID, "subscription_id": t.SubscriptionID, "resource_group_id": t.ResourceGroupID, "location": t.Location, "name": t.Name, "executor_client_id": t.Executor.ClientID})
	return os.WriteFile(filepath.Join(dir, "target.auto.tfvars.json"), b, 0600)
}
func digestFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(f, 16*1024*1024+1))
	if err != nil || n > 16*1024*1024 {
		return "", errors.New("invalid plan size")
	}
	return fmt.Sprintf("sha256:%x", h.Sum(nil)), nil
}

// ARM-only resource listing prevents adoption/overwrite of an existing vault.
// It does not list or read vault contents, or create provider registrations.
func (a *Runner) absent(ctx context.Context, t deployment.Target) error {
	if !a.Identity.matches(t) {
		return errors.New("executor binding changed")
	}
	b, err := a.Identity.get(ctx, t.ResourceGroupID+"/providers/Microsoft.KeyVault/vaults?api-version=2025-05-01")
	var list struct {
		Value    []struct{ Name string }
		NextLink string
	}
	if err != nil || json.Unmarshal(b, &list) != nil || list.NextLink != "" {
		return errors.New("cannot prove target is absent")
	}
	for _, v := range list.Value {
		if strings.EqualFold(v.Name, t.Name) {
			return errors.New("existing vault will not be adopted or modified")
		}
	}
	return nil
}
func (a *Runner) Verify(ctx context.Context, t deployment.Target) (string, error) {
	if !a.Identity.matches(t) {
		return "", errors.New("executor binding changed")
	}
	b, err := a.Identity.get(ctx, t.ResourceID()+"?api-version=2025-05-01")
	var actual struct {
		ID, Location string
		Properties   struct {
			VaultURI, ProvisioningState, TenantID, PublicNetworkAccess                                                                                     string
			EnableRbacAuthorization, EnableSoftDelete, EnablePurgeProtection, EnabledForDeployment, EnabledForDiskEncryption, EnabledForTemplateDeployment bool
			SoftDeleteRetentionInDays                                                                                                                      int
			AccessPolicies                                                                                                                                 []any
			Sku                                                                                                                                            struct{ Name string }
			NetworkAcls                                                                                                                                    struct {
				Bypass, DefaultAction        string
				IPRules, VirtualNetworkRules []any
			}
		}
	}
	if err != nil || json.Unmarshal(b, &actual) != nil {
		return "", errors.New("ARM verification unavailable")
	}
	p := actual.Properties
	if !strings.EqualFold(actual.ID, t.ResourceID()) || actual.Location != t.Location || p.TenantID != t.TenantID || p.ProvisioningState != "Succeeded" || p.Sku.Name != "standard" || p.PublicNetworkAccess != "Disabled" || !p.EnableRbacAuthorization || !p.EnableSoftDelete || p.SoftDeleteRetentionInDays != 7 || p.EnablePurgeProtection || p.EnabledForDeployment || p.EnabledForDiskEncryption || p.EnabledForTemplateDeployment || len(p.AccessPolicies) != 0 || p.NetworkAcls.Bypass != "None" || p.NetworkAcls.DefaultAction != "Deny" || len(p.NetworkAcls.IPRules) != 0 || len(p.NetworkAcls.VirtualNetworkRules) != 0 || p.VaultURI != "https://"+t.Name+".vault.azure.net/" {
		return "", errors.New("ARM verification did not match approved settings")
	}
	return p.VaultURI, nil
}

func (a *Runner) Run(ctx context.Context, intent store.DeploymentIntent) error {
	if intent.Phase != "plan" && intent.Phase != "apply" {
		return errors.New("unsupported deployment phase")
	}
	r, err := a.Store.GetDeployment(ctx, intent.ID)
	if err != nil {
		return errors.New("deployment unavailable")
	}
	if err = a.authorize(ctx, r); err != nil {
		return err
	}
	expected, running := "accepted", "planning"
	if intent.Phase == "apply" {
		expected, running = "apply_queued", "applying"
	}
	// No retries after claiming: an interrupted apply needs explicit recovery.
	if err = a.Store.UpdateDeployment(ctx, r.ID, func(current *deployment.Record) error {
		if current.State != expected || current.Target != r.Target {
			return deployment.ErrConflict
		}
		if intent.Phase == "apply" && (!time.Now().Before(current.PlanExpiresAt) || current.PlanDigest == "") {
			return deployment.ErrConflict
		}
		current.Transition(running)
		return nil
	}); err != nil {
		return errors.New("deployment already claimed or expired")
	}
	dir, err := a.workspace(r.ID)
	if err != nil {
		return errors.New("workspace unavailable")
	}
	if intent.Phase == "plan" {
		if err = a.absent(ctx, r.Target); err != nil {
			return err
		}
		if err = prepare(dir, r.Target); err != nil {
			return errors.New("workspace initialization failed; evidence retained")
		}
		if _, err = a.tf(ctx, dir, "init", "-input=false", "-no-color", "-lockfile=readonly"); err != nil {
			return errors.New("terraform init failed")
		}
		if _, err = a.tf(ctx, dir, "validate", "-no-color"); err != nil {
			return errors.New("terraform validation failed")
		}
		if _, err = a.tf(ctx, dir, "plan", "-input=false", "-no-color", "-lock-timeout=5s", "-out=approved.tfplan"); err != nil {
			return errors.New("terraform plan failed")
		}
		b, err := a.tf(ctx, dir, "show", "-json", "approved.tfplan")
		if err != nil {
			return errors.New("plan inspection failed")
		}
		if err = deployment.ValidatePlan(b, r.Target); err != nil {
			return err
		}
		digest, err := digestFile(filepath.Join(dir, "approved.tfplan"))
		if err != nil {
			return err
		}
		_ = os.Chmod(filepath.Join(dir, "approved.tfplan"), 0600)
		return a.Store.UpdateDeployment(ctx, r.ID, func(current *deployment.Record) error {
			if current.State != "planning" {
				return deployment.ErrConflict
			}
			current.PlanDigest = digest
			current.PlanExpiresAt = time.Now().UTC().Add(30 * time.Minute)
			current.Transition("planned")
			return nil
		})
	}
	// Recheck the reviewed binary plan, current authority and target absence just
	// before apply. No fresh unreviewed plan, destroy, force-unlock or CLI apply fallback.
	digest, err := digestFile(filepath.Join(dir, "approved.tfplan"))
	if err != nil || digest != r.PlanDigest {
		return errors.New("approved plan digest changed")
	}
	b, err := a.tf(ctx, dir, "show", "-json", "approved.tfplan")
	if err != nil {
		return errors.New("plan inspection failed")
	}
	if err = deployment.ValidatePlan(b, r.Target); err != nil {
		return err
	}
	if err = a.authorize(ctx, r); err != nil {
		return err
	}
	if !time.Now().Before(r.PlanExpiresAt) {
		return errors.New("plan expired")
	}
	if err = a.absent(ctx, r.Target); err != nil {
		return err
	}
	if _, err = a.tf(ctx, dir, "apply", "-input=false", "-no-color", "-lock-timeout=5s", "approved.tfplan"); err != nil {
		return errors.New("apply uncertain; preserve state and inspect Azure before recovery")
	}
	uri, err := a.Verify(ctx, r.Target)
	if err != nil {
		return err
	}
	return a.Store.UpdateDeployment(ctx, r.ID, func(current *deployment.Record) error {
		if current.State != "applying" {
			return deployment.ErrConflict
		}
		current.ResourceID = r.Target.ResourceID()
		current.VaultURI = uri
		current.Transition("succeeded")
		return nil
	})
}
func (a *Runner) Failed(ctx context.Context, intent store.DeploymentIntent) error {
	return a.Store.UpdateDeployment(ctx, intent.ID, func(r *deployment.Record) error {
		if r.State == "succeeded" || r.State == "planned" || r.State == "failed" || r.State == "recovery_required" {
			return nil
		}
		state, code := "failed", "plan_failed"
		if intent.Phase == "apply" {
			state, code = "recovery_required", "apply_requires_inspection"
		}
		r.ErrorCode = code
		r.Transition(state)
		return nil
	})
}
