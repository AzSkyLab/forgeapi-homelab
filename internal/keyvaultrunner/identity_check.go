package keyvaultrunner

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"forgeapi/internal/deployment"
	keyvault "forgeapi/patterns/key-vault"
)

const identityRead = `
data "azapi_resource" "existing" {
  type = "Microsoft.KeyVault/vaults@2025-05-01"
  name = var.name
  parent_id = var.resource_group_id
  response_export_values = ["properties.provisioningState"]
}
output "resource_id" { value = data.azapi_resource.existing.id }
output "provisioning_state" { value = data.azapi_resource.existing.output.properties.provisioningState }
`

func identityCheckSource() ([]byte, error) {
	b, err := keyvault.Files.ReadFile("main.tf")
	if err != nil {
		return nil, err
	}
	provider, _, found := strings.Cut(string(b), "# ARM-only:")
	if !found || strings.Contains(provider, `resource "`) {
		return nil, errors.New("read-only provider boundary changed")
	}
	return []byte(provider + identityRead), nil
}

// CheckIdentity performs only authenticated ARM GETs and a Terraform data-source
// plan in a fresh workspace. It does not connect to Temporal/DB, apply/import,
// modify the existing vault, or touch any accepted deployment's original files.
func (a *Runner) CheckIdentity(ctx context.Context) error {
	t, err := deployment.LoadTarget(a.TargetPath)
	if err != nil {
		return err
	}
	if err = a.authorize(ctx, deployment.Record{Target: t}); err != nil {
		return err
	}
	if _, err = a.Verify(ctx, t); err != nil {
		return err
	}
	if !filepath.IsAbs(a.Root) {
		return errors.New("workspace root must be absolute")
	}
	if err = os.MkdirAll(a.Root, 0700); err != nil {
		return errors.New("check workspace unavailable")
	}
	resolved, err := filepath.EvalSymlinks(a.Root)
	if err != nil || resolved != filepath.Clean(a.Root) {
		return errors.New("workspace symlinks forbidden")
	}
	dir, err := os.MkdirTemp(a.Root, "identity-check-")
	if err != nil {
		return errors.New("check workspace unavailable")
	}
	if err = prepare(dir, t); err != nil {
		return err
	}
	b, err := identityCheckSource()
	if err != nil {
		return err
	}
	if err = os.WriteFile(filepath.Join(dir, "main.tf"), b, 0600); err != nil {
		return err
	}
	if _, err = a.tf(ctx, dir, "init", "-input=false", "-no-color", "-lockfile=readonly"); err != nil {
		return errors.New("read-only Terraform init failed")
	}
	if _, err = a.tf(ctx, dir, "plan", "-input=false", "-no-color", "-out=identity-check.tfplan"); err != nil {
		return errors.New("read-only Terraform identity check failed; no apply attempted")
	}
	if err = os.Chmod(filepath.Join(dir, "identity-check.tfplan"), 0600); err != nil {
		return err
	}
	b, err = a.tf(ctx, dir, "show", "-json", "identity-check.tfplan")
	if err != nil {
		return errors.New("read-only plan inspection failed")
	}
	return validateIdentityCheck(b, t)
}

func validateIdentityCheck(b []byte, t deployment.Target) error {
	var p struct {
		ResourceChanges []struct{ Address, Mode string } `json:"resource_changes"`
		Planned         struct {
			Outputs map[string]struct{ Value string } `json:"outputs"`
		} `json:"planned_values"`
	}
	deny := errors.New("read-only Terraform result did not match the existing approved vault")
	if json.Unmarshal(b, &p) != nil {
		return deny
	}
	for _, r := range p.ResourceChanges {
		if r.Mode != "data" || r.Address != "data.azapi_resource.existing" {
			return deny
		}
	}
	if !strings.EqualFold(p.Planned.Outputs["resource_id"].Value, t.ResourceID()) || p.Planned.Outputs["provisioning_state"].Value != "Succeeded" {
		return deny
	}
	return nil
}
