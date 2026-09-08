package httpapi

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
	"time"

	"forgeapi/internal/auth"
	"forgeapi/internal/deployment"
)

// In-memory storage exercises HTTP serialization/contracts only; this is not
// Terraform, durable admission or connected Entra evidence.
type deploymentContractRepository struct {
	record deployment.Record
	err    error
}

func (f *deploymentContractRepository) CreateDeployment(_ context.Context, _, _, _ string, target deployment.Target) ([]byte, error) {
	r := deployment.Record{ID: "dep_contract01", PatternID: deployment.PatternID, Target: target}
	r.Transition("accepted")
	b, err := json.Marshal(r)
	if f.err != nil {
		err = f.err
	}
	return b, err
}
func (f *deploymentContractRepository) GetDeployment(context.Context, string) (deployment.Record, error) {
	return f.record, f.err
}
func (f *deploymentContractRepository) ApproveDeployment(context.Context, string, string, string, deployment.Target) error {
	return f.err
}

func TestDeploymentOpenAPIResponses(t *testing.T) {
	validate := openAPIResponseValidator(t, "../../docs/design/openapi-deployments.yaml")
	a, _ := fixture()
	target := deployment.Target{
		TenantID: "11111111-1111-1111-1111-111111111111", SubscriptionID: "22222222-2222-2222-2222-222222222222",
		ResourceGroupID: "/subscriptions/22222222-2222-2222-2222-222222222222/resourceGroups/contract-rg", Location: "centralus", Name: "contract-vault",
		Owner: "entra:11111111-1111-1111-1111-111111111111:33333333-3333-3333-3333-333333333333", PatternDigest: "sha256:" + strings.Repeat("a", 64),
		Executor: deployment.Executor{Mode: "lab_certificate", ClientID: "44444444-4444-4444-4444-444444444444", PrincipalID: "55555555-5555-5555-5555-555555555555", CertificateSHA256: strings.Repeat("b", 64)},
	}
	if err := target.Validate(); err != nil {
		t.Fatal(err)
	}
	repo := &deploymentContractRepository{record: deployment.Record{ID: "dep_contract01", PatternID: deployment.PatternID, Target: target, PlanDigest: "sha256:" + strings.Repeat("c", 64), PlanExpiresAt: time.Now().UTC().Add(time.Minute)}}
	repo.record.Transition("planned")
	a.Deployments = repo
	a.DeploymentTarget = func() (deployment.Target, error) { return target, nil }
	p := auth.Principal{ID: "human", Kind: "human", Tenant: target.TenantID, OwnerKey: target.Owner, Role: "developer"}
	a.Auth = deploymentIdentity{p: p}
	headers := map[string]string{"Content-Type": "application/json", "Idempotency-Key": "contract-deployment-01"}
	for _, tc := range []struct{ route, method, body string }{
		{"/deployment-patterns", "get", ""},
		{"/deployments", "post", `{"pattern_id":"azure-key-vault-v1"}`},
		{"/deployments/{deployment_id}", "get", ""},
		{"/deployments/{deployment_id}/approvals", "post", `{"plan_digest":"` + repo.record.PlanDigest + `"}`},
	} {
		path := strings.ReplaceAll(tc.route, "{deployment_id}", repo.record.ID)
		call := func(want int, who, body, suffix string, h map[string]string) {
			t.Helper()
			w := request(a.Handler(), strings.ToUpper(tc.method), path+suffix, who, body, h)
			if w.Code != want {
				t.Fatalf("%s %s: got %d, want %d", tc.method, path+suffix, w.Code, want)
			}
			validate(tc.route, tc.method, w)
		}
		want := 200
		if tc.method == "post" {
			want = 202
		}
		call(want, "alice", tc.body, "", headers)
		call(401, "", tc.body, "", headers)
		call(400, "alice", tc.body, "?unsupported=1", headers)
		call(406, "alice", tc.body, "", map[string]string{"Accept": "text/plain"})
		if tc.method == "post" {
			call(400, "alice", "{}", "", headers)
			call(400, "alice", tc.body, "", nil)
			call(415, "alice", tc.body, "", map[string]string{"Content-Type": "text/plain", "Idempotency-Key": headers["Idempotency-Key"]})
			call(413, "alice", strings.Repeat("x", 65537), "", headers)
			repo.err = deployment.ErrConflict
			call(409, "alice", tc.body, "", headers)
			repo.err = nil
		}
		denied := p
		denied.Role = "auditor"
		a.Auth = deploymentIdentity{p: denied}
		call(403, "alice", tc.body, "", headers)
		a.Auth = deploymentIdentity{p: p}
		a.DeploymentTarget = nil
		call(404, "alice", tc.body, "", headers)
		a.DeploymentTarget = func() (deployment.Target, error) { return target, deployment.ErrDenied }
		call(503, "alice", tc.body, "", headers)
		a.DeploymentTarget = func() (deployment.Target, error) { return target, nil }
	}
	for _, state := range []string{"accepted", "planning", "planned", "apply_queued", "applying", "succeeded", "failed", "recovery_required", "rejected_no_effect"} {
		repo.record.Transition(state)
		validate("/deployments/{deployment_id}", "get", request(a.Handler(), "GET", "/deployments/"+repo.record.ID, "alice", "", nil))
	}
	// Immutable receipts created before the executor transition remain readable.
	repo.record.Target.Executor = deployment.Executor{}
	validate("/deployments/{deployment_id}", "get", request(a.Handler(), "GET", "/deployments/"+repo.record.ID, "alice", "", nil))
}
