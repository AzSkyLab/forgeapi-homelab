package deployment

import (
	"encoding/json"
	"errors"
	"reflect"
)

// ValidatePlan accepts exactly one known create, never updates/deletes/imports,
// unrelated resources, child modules, unknown target/security settings or extras.
// The runner separately checks the complete embedded bundle and saved plan hashes.
func ValidatePlan(raw []byte, target Target) error {
	var p struct {
		Variables       map[string]struct{ Value string } `json:"variables"`
		ResourceChanges []struct {
			Address string `json:"address"`
			Mode    string `json:"mode"`
			Type    string `json:"type"`
			Change  struct {
				Actions   []string       `json:"actions"`
				Before    any            `json:"before"`
				After     map[string]any `json:"after"`
				Importing any            `json:"importing"`
			} `json:"change"`
		} `json:"resource_changes"`
	}
	deny := errors.New("plan is not exactly one approved empty Standard vault creation")
	if json.Unmarshal(raw, &p) != nil || len(p.ResourceChanges) != 1 {
		return deny
	}
	if target.Executor.Validate() != nil || p.Variables["executor_client_id"].Value != target.Executor.ClientID {
		return deny
	}
	r := p.ResourceChanges[0]
	if r.Address != "azapi_resource.vault" || r.Mode != "managed" || r.Type != "azapi_resource" || !reflect.DeepEqual(r.Change.Actions, []string{"create"}) || r.Change.Before != nil || r.Change.Importing != nil {
		return deny
	}
	a := r.Change.After
	for k, v := range map[string]string{"type": "Microsoft.KeyVault/vaults@2025-05-01", "name": target.Name, "parent_id": target.ResourceGroupID, "location": target.Location} {
		if a[k] != v {
			return deny
		}
	}
	want := map[string]any{"properties": map[string]any{
		"tenantId": target.TenantID, "sku": map[string]any{"family": "A", "name": "standard"},
		"enableRbacAuthorization": true, "accessPolicies": []any{}, "enableSoftDelete": true, "softDeleteRetentionInDays": float64(7),
		"enabledForDeployment": false, "enabledForDiskEncryption": false, "enabledForTemplateDeployment": false,
		"publicNetworkAccess": "Disabled", "networkAcls": map[string]any{"bypass": "None", "defaultAction": "Deny", "ipRules": []any{}, "virtualNetworkRules": []any{}},
	}}
	if !reflect.DeepEqual(a["body"], want) || !reflect.DeepEqual(a["tags"], map[string]any{"managed_by": "forgeapi", "purpose": "empty-vault-demo"}) {
		return deny
	}
	return nil
}
