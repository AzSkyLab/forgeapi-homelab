package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"
	"testing"

	"forgeapi/internal/auth"
	"forgeapi/internal/deployment"
)

type deploymentFixture struct {
	r     deployment.Record
	calls int
}

func (f *deploymentFixture) CreateDeployment(_ context.Context, owner, key, hash string, target deployment.Target) ([]byte, error) {
	f.calls++
	f.r = deployment.Record{ID: "dep_test", State: "accepted", Target: target}
	b, _ := json.Marshal(f.r)
	return b, nil
}
func (f *deploymentFixture) GetDeployment(context.Context, string) (deployment.Record, error) {
	return f.r, nil
}
func (f *deploymentFixture) ApproveDeployment(context.Context, string, string, string, deployment.Target) error {
	f.calls++
	return nil
}

type deploymentIdentity struct{ p auth.Principal }

func (f deploymentIdentity) Authenticate(r *http.Request) (auth.Principal, error) {
	if r.Header.Get("X-Demo-Principal") == "" {
		return auth.Principal{}, auth.ErrUnauthenticated
	}
	return f.p, nil
}
func TestDeploymentExplicitScopeAndInput(t *testing.T) {
	a, _ := fixture()
	target := deployment.Target{TenantID: "tenant", Owner: "explicit-owner"}
	f := &deploymentFixture{}
	a.Deployments = f
	a.DeploymentTarget = func() (deployment.Target, error) { return target, nil }
	p := auth.Principal{ID: "human", Kind: "human", Tenant: "tenant", OwnerKey: "explicit-owner", Role: "developer"}
	a.Auth = deploymentIdentity{p: p}
	headers := map[string]string{"Content-Type": "application/json", "Idempotency-Key": "create-vault-test-01"}
	for _, body := range []string{`{"pattern_id":"azure-key-vault-v1","subscription_id":"evil"}`, `{"pattern_id":"azure-key-vault-v1","source":"https://evil.test"}`, `{"pattern_id":null}`, `{"pattern_id":"azure-key-vault-v1","pattern_id":"azure-key-vault-v1"}`} {
		w := request(a.Handler(), "POST", "/deployments", "alice", body, headers)
		if w.Code != 400 || f.calls != 0 {
			t.Fatalf("input admitted: %d", w.Code)
		}
	}
	w := request(a.Handler(), "POST", "/deployments", "alice", `{"pattern_id":"azure-key-vault-v1"}`, headers)
	if w.Code != 202 || f.calls != 1 || w.Header().Get("Location") == "" {
		t.Fatalf("%d %s", w.Code, w.Body)
	}
	for _, change := range []string{"owner", "kind", "role", "tenant"} {
		denied := p
		switch change {
		case "owner":
			denied.OwnerKey = "other"
		case "kind":
			denied.Kind = "application"
		case "role":
			denied.Role = "auditor"
		case "tenant":
			denied.Tenant = "other"
		}
		a.Auth = deploymentIdentity{p: denied}
		for _, path := range []string{"/deployment-patterns", "/deployments/dep_test"} {
			if got := request(a.Handler(), "GET", path, "alice", "", nil); got.Code != 403 {
				t.Fatalf("%s scope accepted: %d", change, got.Code)
			}
		}
	}
	a.Auth = deploymentIdentity{p: p}
	f.r.Target.Owner = "different-owner"
	if w = request(a.Handler(), "GET", "/deployments/dep_test", "alice", "", nil); w.Code != 404 {
		t.Fatal("cross-owner record leak")
	}
	a.DeploymentTarget = func() (deployment.Target, error) { return target, errors.New("private-config-canary") }
	w = request(a.Handler(), "GET", "/deployment-patterns", "alice", "", nil)
	if w.Code != 503 || strings.Contains(w.Body.String(), "canary") {
		t.Fatal("configuration fails open or leaks")
	}
}
