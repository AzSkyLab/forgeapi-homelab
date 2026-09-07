package httpapi

import (
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"forgeapi/internal/auth"
	"forgeapi/internal/execution"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/go-jose/go-jose/v4"
	"github.com/golang-jwt/jwt/v5"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

// Real verifier + real router + current grant file, with only the external
// signing-key response replaced. Synthetic signatures are never a runtime mode.
func TestEntraHTTPAuthorization(t *testing.T) {
	const tenant = "11111111-1111-4111-8111-111111111111"
	const audience = "22222222-2222-4222-8222-222222222222"
	const client = "33333333-3333-4333-8333-333333333333"
	const owner = "44444444-4444-4444-8444-444444444444"
	const other = "55555555-5555-4555-8555-555555555555"
	const auditor = "66666666-6666-4666-8666-666666666666"
	const ungranted = "77777777-7777-4777-8777-777777777777"
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	keys, err := json.Marshal(map[string]any{"keys": []jose.JSONWebKey{{Key: &key.PublicKey, KeyID: "synthetic-key", Use: "sig", Algorithm: "RS256"}}})
	if err != nil {
		t.Fatal(err)
	}
	previous := http.DefaultTransport
	http.DefaultTransport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		if r.URL.String() != "https://login.microsoftonline.com/"+tenant+"/discovery/v2.0/keys" {
			t.Errorf("unexpected auth destination: %s", r.URL)
		}
		return &http.Response{StatusCode: 200, Body: io.NopCloser(strings.NewReader(string(keys))), Header: make(http.Header)}, nil
	})
	t.Cleanup(func() { http.DefaultTransport = previous })
	path := filepath.Join(t.TempDir(), "grants.json")
	writePolicy := func(includeOwner bool) {
		t.Helper()
		p := auth.Policy{Grants: []auth.Grant{}}
		for oid, role := range map[string]string{owner: "developer", other: "developer", auditor: "auditor"} {
			if oid == owner && !includeOwner {
				continue
			}
			p.Grants = append(p.Grants, auth.Grant{TenantID: tenant, ObjectID: oid, PrincipalKind: "human", ApplicationID: execution.Application, Environment: execution.Environment, Classification: "synthetic", Role: role})
		}
		b, err := json.Marshal(p)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, b, 0600); err != nil {
			t.Fatal(err)
		}
	}
	writePolicy(true)
	cfg := auth.Config{TenantID: tenant, Audience: audience, AllowedClients: []string{client}, DelegatedScope: "executions.access", ApplicationRole: "executions.access", GrantsFile: path}
	verifier, err := auth.NewEntra(cfg)
	if err != nil {
		t.Fatal(err)
	}
	a, repo := fixture()
	a.Auth = verifier
	repo.record.Owner = "entra:" + tenant + ":" + owner
	repo.record.Event("execution.dispatch_changed", time.Now())
	h := a.Handler()
	token := func(oid, aud string) string {
		t.Helper()
		now := time.Now()
		c := auth.Claims{RegisteredClaims: jwt.RegisteredClaims{Issuer: cfg.Issuer(), Subject: "synthetic-subject", Audience: jwt.ClaimStrings{aud}, IssuedAt: jwt.NewNumericDate(now.Add(-time.Minute)), NotBefore: jwt.NewNumericDate(now.Add(-time.Minute)), ExpiresAt: jwt.NewNumericDate(now.Add(time.Hour))}, TenantID: tenant, ObjectID: oid, ClientID: client, Version: "2.0", Scope: "executions.access"}
		jwtToken := jwt.NewWithClaims(jwt.SigningMethodRS256, c)
		jwtToken.Header["kid"] = "synthetic-key"
		raw, err := jwtToken.SignedString(key)
		if err != nil {
			t.Fatal(err)
		}
		return raw
	}
	call := func(method, path, raw, body string, headers map[string]string) *httptest.ResponseRecorder {
		t.Helper()
		r := httptest.NewRequest(method, "http://localhost:8080"+path, strings.NewReader(body))
		if raw != "" {
			r.Header.Set("Authorization", "Bearer "+raw)
		}
		for k, v := range headers {
			r.Header.Set(k, v)
		}
		w := httptest.NewRecorder()
		h.ServeHTTP(w, r)
		if raw != "" && strings.Contains(w.Body.String(), raw) {
			t.Fatal("token leaked")
		}
		return w
	}
	ownerToken := token(owner, audience)
	statusPath := "/executions/" + repo.record.Execution.ID
	for _, tc := range []struct {
		name, path, raw string
		headers         map[string]string
		want            int
	}{
		{"identity", "/identity-context", ownerToken, nil, 200},
		{"owner", statusPath, ownerToken, nil, 200},
		{"other user", statusPath, token(other, audience), nil, 404},
		{"auditor metadata", statusPath, token(auditor, audience), nil, 200},
		{"auditor data", statusPath + "/logs", token(auditor, audience), nil, 403},
		{"no grant", statusPath, token(ungranted, audience), nil, 403},
		{"missing token", statusPath, "", nil, 401},
		{"wrong audience", statusPath, token(owner, "https://management.azure.com/"), nil, 401},
		{"fixture header", statusPath, "", map[string]string{"X-Demo-Principal": "alice"}, 401},
		{"token plus fixture", statusPath, ownerToken, map[string]string{"X-Demo-Principal": "alice"}, 401},
	} {
		t.Run(tc.name, func(t *testing.T) {
			w := call("GET", tc.path, tc.raw, "", tc.headers)
			if w.Code != tc.want {
				t.Fatalf("status %d, want %d: %s", w.Code, tc.want, w.Body)
			}
			if w.Code == 401 && w.Header().Get("WWW-Authenticate") == "" {
				t.Fatal("missing bearer challenge")
			}
		})
	}
	identity := call("GET", "/identity-context", token(ungranted, audience), "", nil)
	if identity.Code != 200 || !strings.Contains(identity.Body.String(), `"authorized_contexts":[]`) {
		t.Fatal("ungranted identity gained permissions")
	}
	if w := call("POST", "/executions", ownerToken, `{"application_id":"software-factory","environment":"development","template_id":"pr-validation-v1","template_version":"1.0.0","input_artifact_refs":["source-01"]}`, map[string]string{"Content-Type": "application/json", "Idempotency-Key": "entra-submit-test-01"}); w.Code != 202 || repo.owner != "entra:"+tenant+":"+owner {
		t.Fatal("submission did not bind verified owner")
	}
	etag := call("GET", statusPath, ownerToken, "", nil).Header().Get("ETag")
	page := call("GET", statusPath+"/events?limit=1", ownerToken, "", nil)
	var response struct {
		Cursor string `json:"cursor"`
	}
	if json.Unmarshal(page.Body.Bytes(), &response) != nil || response.Cursor == "" {
		t.Fatal("missing cursor")
	}
	writePolicy(false)
	for _, path := range []string{statusPath, statusPath + "/events?cursor=" + response.Cursor} {
		w := call("GET", path, ownerToken, "", map[string]string{"If-None-Match": etag})
		if w.Code != 403 {
			t.Fatal("ETag/cursor bypassed current grant revocation", w.Code)
		}
	}
	if err := os.WriteFile(path, []byte(`{"grants":null}`), 0600); err != nil {
		t.Fatal(err)
	}
	if w := call("GET", statusPath, ownerToken, "", nil); w.Code != 503 || w.Header().Get("Retry-After") == "" {
		t.Fatal("invalid policy did not fail closed")
	}
}
