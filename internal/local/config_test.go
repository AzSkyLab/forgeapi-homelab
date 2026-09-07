package local

import "testing"

func TestLocalOnlyStartup(t *testing.T) {
	clearAuth(t)
	t.Setenv("FORGE_MODE", "")
	if _, err := Read(); err == nil {
		t.Fatal("implicit fixture mode accepted")
	}
	t.Setenv("FORGE_MODE", "production")
	if _, err := Read(); err == nil {
		t.Fatal("production mode accepted")
	}
	t.Setenv("FORGE_MODE", "local-demo")
	validAuth(t)
	t.Setenv("LISTEN_ADDR", "127.0.0.1:8080")
	t.Setenv("PUBLIC_BASE_URL", "http://localhost:8080")
	t.Setenv("LOCAL_CONTAINER", "")
	if _, err := Read(); err != nil {
		t.Fatal(err)
	}
	t.Setenv("LISTEN_ADDR", "0.0.0.0:8080")
	if _, err := Read(); err == nil {
		t.Fatal("public listener accepted")
	}
	t.Setenv("LOCAL_CONTAINER", "true")
	if _, err := Read(); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PUBLIC_BASE_URL", "https://public.example.com")
	if _, err := Read(); err == nil {
		t.Fatal("public URL accepted")
	}
}

func clearAuth(t *testing.T) {
	t.Helper()
	for _, key := range []string{"AUTH_MODE", "AUTH_TENANT_ID", "AUTH_AUDIENCE", "AUTH_ALLOWED_CLIENT_IDS", "AUTH_DELEGATED_SCOPE", "AUTH_APPLICATION_ROLE", "AUTH_GRANTS_FILE", "CURSOR_SIGNING_KEY"} {
		t.Setenv(key, "")
	}
}

func validAuth(t *testing.T) {
	t.Helper()
	t.Setenv("AUTH_TENANT_ID", "11111111-1111-4111-8111-111111111111")
	t.Setenv("AUTH_AUDIENCE", "22222222-2222-4222-8222-222222222222")
	t.Setenv("AUTH_ALLOWED_CLIENT_IDS", "33333333-3333-4333-8333-333333333333")
	t.Setenv("CURSOR_SIGNING_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
}

func TestAuthConfigurationFailsClosed(t *testing.T) {
	clearAuth(t)
	t.Setenv("FORGE_MODE", "local-demo")
	t.Setenv("LISTEN_ADDR", "127.0.0.1:8080")
	t.Setenv("PUBLIC_BASE_URL", "http://localhost:8080")
	t.Setenv("AUTH_MODE", "entraa")
	if _, err := Read(); err == nil {
		t.Fatal("unknown mode accepted")
	}
	t.Setenv("AUTH_MODE", "entra")
	if _, err := Read(); err == nil {
		t.Fatal("missing Entra configuration accepted")
	}
	t.Setenv("AUTH_TENANT_ID", "11111111-1111-4111-8111-111111111111")
	t.Setenv("AUTH_AUDIENCE", "22222222-2222-4222-8222-222222222222")
	t.Setenv("AUTH_ALLOWED_CLIENT_IDS", "33333333-3333-4333-8333-333333333333")
	t.Setenv("AUTH_DELEGATED_SCOPE", "access_as_user")
	t.Setenv("AUTH_APPLICATION_ROLE", "Execution.Invoke")
	t.Setenv("AUTH_GRANTS_FILE", "configured.json")
	if _, err := Read(); err == nil {
		t.Fatal("missing cursor secret accepted")
	}
	t.Setenv("CURSOR_SIGNING_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
	if c, err := Read(); err != nil || c.AuthMode != "entra" {
		t.Fatal("valid config rejected", err)
	}
	t.Setenv("AUTH_MODE", "fixture")
	if _, err := Read(); err == nil {
		t.Fatal("fixture startup accepted")
	}
	t.Setenv("AUTH_MODE", "")
	if c, err := Read(); err != nil || c.AuthMode != "entra" {
		t.Fatal("normal startup must require Entra", err)
	}
}
