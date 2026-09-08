package deployment

import (
	"encoding/json"
	"strings"
	"testing"
)

func testTarget() Target {
	return Target{TenantID: "11111111-1111-1111-1111-111111111111", SubscriptionID: "22222222-2222-2222-2222-222222222222", ResourceGroupID: "/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/test", Location: "centralus", Name: "kv-forge-test", Owner: "entra:11111111-1111-1111-1111-111111111111:33333333-3333-3333-3333-333333333333", PatternDigest: "sha256:" + strings.Repeat("a", 64), Executor: Executor{Mode: "lab_certificate", ClientID: "44444444-4444-4444-4444-444444444444", PrincipalID: "55555555-5555-5555-5555-555555555555", CertificateSHA256: strings.Repeat("b", 64)}}
}

func testPlan(t Target) []byte {
	a := map[string]any{"type": "Microsoft.KeyVault/vaults@2025-05-01", "name": t.Name, "parent_id": t.ResourceGroupID, "location": t.Location, "tags": map[string]any{"managed_by": "forgeapi", "purpose": "empty-vault-demo"}, "body": map[string]any{"properties": map[string]any{
		"tenantId": t.TenantID, "sku": map[string]any{"family": "A", "name": "standard"}, "enableRbacAuthorization": true, "accessPolicies": []any{}, "enableSoftDelete": true, "softDeleteRetentionInDays": 7, "enabledForDeployment": false, "enabledForDiskEncryption": false, "enabledForTemplateDeployment": false, "publicNetworkAccess": "Disabled", "networkAcls": map[string]any{"bypass": "None", "defaultAction": "Deny", "ipRules": []any{}, "virtualNetworkRules": []any{}},
	}}}
	b, _ := json.Marshal(map[string]any{"variables": map[string]any{"executor_client_id": map[string]any{"value": t.Executor.ClientID}}, "resource_changes": []any{map[string]any{"address": "azapi_resource.vault", "mode": "managed", "type": "azapi_resource", "change": map[string]any{"actions": []string{"create"}, "before": nil, "after": a}}}})
	return b
}
func TestPlanBoundary(t *testing.T) {
	target := testTarget()
	if err := target.Validate(); err != nil {
		t.Fatal(err)
	}
	b := testPlan(target)
	if err := ValidatePlan(b, target); err != nil {
		t.Fatal(err)
	}
	for _, change := range [][2]string{{`"create"`, `"update"`}, {`"standard"`, `"premium"`}, {`"Disabled"`, `"Enabled"`}, {`"enableRbacAuthorization":true`, `"enableRbacAuthorization":false`}, {target.Name, "other-vault"}, {target.TenantID, "other-tenant"}, {`"resource_changes":[`, `"resource_changes":[{},`}, {`"body":{`, `"body":{"extra":true,`}, {`"before":null`, `"before":{}`}} {
		bad := strings.Replace(string(b), change[0], change[1], 1)
		if bad == string(b) {
			t.Fatalf("bad test replacement %v", change)
		}
		if ValidatePlan([]byte(bad), target) == nil {
			t.Fatalf("accepted %v", change)
		}
	}
}

func TestCreatePlanOmitsPurgeProtection(t *testing.T) {
	target := testTarget()
	var plan map[string]any
	if err := json.Unmarshal(testPlan(target), &plan); err != nil {
		t.Fatal(err)
	}
	change := plan["resource_changes"].([]any)[0].(map[string]any)["change"].(map[string]any)
	properties := change["after"].(map[string]any)["body"].(map[string]any)["properties"].(map[string]any)
	delete(properties, "enablePurgeProtection")
	b, _ := json.Marshal(plan)
	if err := ValidatePlan(b, target); err != nil {
		t.Fatalf("omission must be accepted for create: %v", err)
	}
	for _, value := range []any{false, true, nil} {
		properties["enablePurgeProtection"] = value
		b, _ = json.Marshal(plan)
		if ValidatePlan(b, target) == nil {
			t.Fatalf("unapproved explicit purge protection value accepted: %v", value)
		}
	}
}
