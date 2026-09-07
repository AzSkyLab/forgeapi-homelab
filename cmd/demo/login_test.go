package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const loginFixture = "AUTH_TENANT_ID=11111111-1111-4111-8111-111111111111\nAUTH_AUDIENCE=22222222-2222-4222-8222-222222222222\nAUTH_CLIENT_ID=33333333-3333-4333-8333-333333333333\n"

func TestLoginConfig(t *testing.T) {
	if _, err := readLoginConfig(strings.NewReader(loginFixture + "CURSOR_SIGNING_KEY=ignored-by-client\n")); err != nil {
		t.Fatal(err)
	}
	for _, raw := range []string{"", loginFixture + "AUTH_CLIENT_SECRET=canary-secret", loginFixture + "AZURE_CLIENT_SECRET=canary-secret", loginFixture + "AUTH_CLIENT_ID=33333333-3333-4333-8333-333333333333", strings.Replace(loginFixture, "11111111-1111-4111-8111-111111111111", "$(do-not-execute)", 1), strings.Repeat("#", 65538)} {
		if _, err := readLoginConfig(strings.NewReader(raw)); err == nil || strings.Contains(err.Error(), "canary-secret") {
			t.Fatal("invalid config accepted or secret leaked")
		}
	}
}

func TestLoginDestination(t *testing.T) {
	for _, raw := range []string{"http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080"} {
		if !localAPIURL(raw) {
			t.Fatal("loopback rejected")
		}
	}
	for _, raw := range []string{"https://example.com", "http://api:8080", "http://localhost@evil.example", "http://localhost:8080/path", "http://localhost:8080?token=x", "http://localhost:8080#x"} {
		if localAPIURL(raw) {
			t.Fatal("unsafe token destination accepted")
		}
	}
}

func TestDemoBearerAndRedaction(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer access-token-canary" || r.Header.Get("X-Demo-Principal") != "" {
			t.Error("wrong authentication headers")
		}
		w.WriteHeader(403)
		_, _ = w.Write([]byte("access-token-canary"))
	}))
	defer server.Close()
	d := demo{base: server.URL, token: "access-token-canary", client: *server.Client(), ctx: context.Background()}
	_, err := d.request("GET", "/identity-context", "owner", "", "", 200)
	if err == nil || strings.Contains(err.Error(), d.token) {
		t.Fatal("error response leaked token")
	}
}
