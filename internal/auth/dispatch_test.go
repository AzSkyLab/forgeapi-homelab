package auth

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"

	"forgeapi/internal/execution"
)

func TestDispatchUsesCurrentGrant(t *testing.T) {
	tenant := "11111111-1111-4111-8111-111111111111"
	object := "22222222-2222-4222-8222-222222222222"
	path := filepath.Join(t.TempDir(), "grants.json")
	body := `{"grants":[{"tenant_id":"` + tenant + `","object_id":"` + object + `","principal_kind":"human","application_id":"software-factory","environment":"development","classification":"synthetic","role":"developer"}]}`
	if err := os.WriteFile(path, []byte(body), 0600); err != nil {
		t.Fatal(err)
	}
	r := execution.NewRecord("entra:"+tenant+":"+object, "http://localhost:8080", execution.Defaults(), time.Now())
	r.Correlation.PrincipalKind = "human"
	check := DispatchAuthorizer(path, tenant)
	if ok, err := check(context.Background(), r); err != nil || !ok {
		t.Fatal("current grant denied", err)
	}
	if err := os.WriteFile(path, []byte(`{"grants":[]}`), 0600); err != nil {
		t.Fatal(err)
	}
	if ok, err := check(context.Background(), r); err != nil || ok {
		t.Fatal("revoked grant retained", err)
	}
	if err := os.WriteFile(path, []byte(`invalid`), 0600); err != nil {
		t.Fatal(err)
	}
	if ok, err := check(context.Background(), r); err == nil || ok {
		t.Fatal("unreadable policy failed open")
	}
}
