package auth

import (
	"encoding/json"
	"forgeapi/internal/execution"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func grant() Grant {
	return Grant{TenantID: tenant, ObjectID: subject, PrincipalKind: "human", ApplicationID: execution.Application, Environment: execution.Environment, Classification: "synthetic", Role: "developer"}
}

func TestGrantScopeAndSeparation(t *testing.T) {
	g := grant()
	p := Policy{Grants: []Grant{g}}.Apply(Principal{ID: subject, Tenant: tenant, Kind: "human", OwnerKey: "entra:" + tenant + ":" + subject})
	r := execution.NewRecord(p.OwnerKey, "http://localhost:8080", execution.Defaults(), time.Now())
	if !p.CanRead(r) || !p.Can("execution.data.read") {
		t.Fatal("own synthetic record denied")
	}
	r.Owner = "entra:" + tenant + ":" + stranger
	if p.CanRead(r) {
		t.Fatal("other owner's record allowed")
	}
	p.Role = "auditor"
	if !p.CanRead(r) || p.Can("execution.data.read") || p.Can("execution.cancel") {
		t.Fatal("auditor scope wrong")
	}
	r.Owner = "entra:" + stranger + ":" + subject
	if p.CanRead(r) {
		t.Fatal("cross-tenant auditor access")
	}
	r.Owner = "alice"
	if p.CanRead(r) {
		t.Fatal("Entra identity accessed fixture record")
	}
	p = Principal{Tenant: "fixture", ID: "auditor", OwnerKey: "auditor", Role: "auditor"}
	r.Owner = "entra:" + tenant + ":" + subject
	if p.CanRead(r) {
		t.Fatal("fixture identity accessed Entra record")
	}
	p.Role = "operator"
	if p.Can("execution.read") || !p.Can("platform.operate") {
		t.Fatal("operator implied data access")
	}
}

func TestPolicyFileIsCurrentAndClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "grants.json")
	b, _ := json.Marshal(Policy{Grants: []Grant{grant()}})
	if err := os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := LoadPolicy(path); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`{"grants":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	p, err := LoadPolicy(path)
	if err != nil || len(p.Grants) != 0 {
		t.Fatal("revocation not loaded")
	}
	for _, body := range []string{`{}`, `{"grants":null}`, `{"grants":[],"extra":1}`, `{"grants":[],"grants":[]}`, `{"grants":[{}]}`} {
		if _, err := ParsePolicy([]byte(body)); err == nil {
			t.Errorf("accepted %s", body)
		}
	}
	for _, change := range []func(*Grant){func(g *Grant) { g.Role = "admin" }, func(g *Grant) { g.Environment = "production" }, func(g *Grant) { g.Classification = "customer-data" }, func(g *Grant) { g.PrincipalKind = "group" }, func(g *Grant) { g.ApplicationID = "other-app" }} {
		g := grant()
		change(&g)
		b, _ := json.Marshal(Policy{Grants: []Grant{g}})
		if _, err := ParsePolicy(b); err == nil {
			t.Fatal("unsafe policy accepted")
		}
	}
	b, _ = json.Marshal(Policy{Grants: []Grant{grant(), grant()}})
	if _, err := ParsePolicy(b); err == nil {
		t.Fatal("duplicate grant accepted")
	}
}
