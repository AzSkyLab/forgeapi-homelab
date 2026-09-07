package local

import "testing"

func TestLocalOnlyStartup(t *testing.T) {
	t.Setenv("FORGE_MODE", "")
	if _, err := Read(); err == nil {
		t.Fatal("implicit fixture mode accepted")
	}
	t.Setenv("FORGE_MODE", "production")
	if _, err := Read(); err == nil {
		t.Fatal("production mode accepted")
	}
	t.Setenv("FORGE_MODE", "local-demo")
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
