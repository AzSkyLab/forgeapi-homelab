package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"forgeapi/internal/deployment"
)

func (d *demo) waitDeployment(id, want string) (deployment.Record, error) {
	var record deployment.Record
	last := ""
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		b, err := d.request("GET", "/deployments/"+id, "owner", "", "", 200)
		if err != nil {
			return record, err
		}
		if err = json.Unmarshal(b, &record); err != nil {
			return record, err
		}
		if record.State != last {
			fmt.Println(id, "→", record.State)
			last = record.State
		}
		if record.State == want {
			return record, nil
		}
		if want == "planned" && (record.State == "apply_queued" || record.State == "applying" || record.State == "succeeded") {
			return record, nil
		}
		if record.State == "failed" || record.State == "recovery_required" || record.State == "rejected_no_effect" {
			return record, fmt.Errorf("deployment %s: %s; inspect retained workspace; no automatic retry", record.State, record.ErrorCode)
		}
		select {
		case <-d.ctx.Done():
			return record, d.ctx.Err()
		case <-ticker.C:
		}
	}
}
func (d *demo) keyVault() error {
	fmt.Println("LIVE AZURE: one empty Standard Key Vault; infrastructure will be retained.")
	if _, err := d.request("GET", "/deployment-patterns", "owner", "", "", 200); err != nil {
		return err
	}
	key := d.keyVaultRequestKey
	if key == "" {
		key = "key-vault-create-v1"
	}
	b, err := d.request("POST", "/deployments", "owner", key, `{"pattern_id":"azure-key-vault-v1"}`, 202)
	if err != nil {
		return err
	}
	var r deployment.Record
	if err = json.Unmarshal(b, &r); err != nil {
		return err
	}
	fmt.Println("Accepted through ForgeAPI:", r.ID)
	r, err = d.waitDeployment(r.ID, "planned")
	if err != nil {
		return err
	}
	if r.State == "succeeded" {
		fmt.Println("Existing API deployment already succeeded:", r.ResourceID, "— no new plan/apply requested.")
		return nil
	}
	if r.State == "apply_queued" || r.State == "applying" {
		r, err = d.waitDeployment(r.ID, "succeeded")
		if err != nil {
			return err
		}
		fmt.Println("Previously approved deployment completed:", r.ResourceID)
		return nil
	}
	fmt.Println("Reviewed policy: exactly 1 create, 0 updates, 0 deletes; no secrets/keys, networking resources or role grants.")
	fmt.Println("Target:", r.Target.ResourceID(), "Location:", r.Target.Location)
	fmt.Println("Saved plan:", r.PlanDigest, "Expires:", r.PlanExpiresAt.Format(time.RFC3339))
	fmt.Println("To approve this exact live change, type APPLY followed by the plan digest. Anything else stops without applying:")
	scanner := bufio.NewScanner(os.Stdin)
	if !scanner.Scan() || strings.TrimSpace(scanner.Text()) != "APPLY "+r.PlanDigest {
		return errors.New("plan was not approved; no apply requested")
	}
	body, _ := json.Marshal(map[string]string{"plan_digest": r.PlanDigest})
	if _, err = d.request("POST", "/deployments/"+r.ID+"/approvals", "owner", "key-vault-approve-v1", string(body), 202); err != nil {
		return err
	}
	r, err = d.waitDeployment(r.ID, "succeeded")
	if err != nil {
		return err
	}
	fmt.Println("PASS: Terraform applied the API-approved saved plan and ARM verification matched.")
	fmt.Println("Resource:", r.ResourceID)
	fmt.Println("Vault URI:", r.VaultURI, "(public data-plane access is disabled)")
	fmt.Println("Resource retained. State stays in .local/deployments/" + r.ID + "; do not delete it or blindly retry an interrupted apply.")
	return nil
}
