package auth

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/go-jose/go-jose/v4"
	"github.com/golang-jwt/jwt/v5"
)

const tenant = "11111111-1111-4111-8111-111111111111"
const audience = "22222222-2222-4222-8222-222222222222"
const actor = "33333333-3333-4333-8333-333333333333"
const subject = "44444444-4444-4444-8444-444444444444"
const stranger = "55555555-5555-4555-8555-555555555555"

func config() Config {
	return Config{TenantID: tenant, Audience: audience, AllowedClients: []string{actor}, DelegatedScope: "access_as_user", ApplicationRole: "Execution.Invoke", GrantsFile: "unused-test-path"}
}

func signingKey(t *testing.T) *rsa.PrivateKey {
	t.Helper()
	k, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	return k
}

func validClaims(now time.Time) Claims {
	return Claims{RegisteredClaims: jwt.RegisteredClaims{Issuer: config().Issuer(), Subject: "synthetic-subject", Audience: jwt.ClaimStrings{audience}, ExpiresAt: jwt.NewNumericDate(now.Add(time.Hour)), NotBefore: jwt.NewNumericDate(now.Add(-time.Minute)), IssuedAt: jwt.NewNumericDate(now.Add(-time.Minute))}, TenantID: tenant, ObjectID: subject, Version: "2.0", ClientID: actor, Scope: "access_as_user"}
}

func signed(t *testing.T, c Claims, key any, method jwt.SigningMethod, kid string) string {
	t.Helper()
	token := jwt.NewWithClaims(method, c)
	token.Header["kid"] = kid
	raw, err := token.SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func verifier(t *testing.T, key *rsa.PrivateKey, now time.Time) *Entra {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []jose.JSONWebKey{{Key: &key.PublicKey, KeyID: "key-1", Use: "sig", Algorithm: "RS256"}}})
	}))
	t.Cleanup(srv.Close)
	policy := Policy{Grants: []Grant{{TenantID: tenant, ObjectID: subject, PrincipalKind: "human", Role: "developer"}}}
	return &Entra{config: config(), keys: newKeySet(srv.URL, config().Issuer(), srv.Client(), func() time.Time { return now }), policy: func() (Policy, error) { return policy, nil }, now: func() time.Time { return now }}
}

func bearer(raw string) *http.Request {
	r := httptest.NewRequest("GET", "http://localhost:8080/identity-context", nil)
	r.Header.Set("Authorization", "Bearer "+raw)
	return r
}

func TestEntraTokenValidation(t *testing.T) {
	key := signingKey(t)
	wrongKey := signingKey(t)
	now := time.Now().UTC()
	e := verifier(t, key, now)
	cases := []struct {
		name   string
		change func(*Claims)
		valid  bool
	}{
		{"delegated access", func(*Claims) {}, true},
		{"wrong audience ARM or Graph", func(c *Claims) { c.Audience = jwt.ClaimStrings{"https://management.azure.com/"} }, false},
		{"wrong tenant", func(c *Claims) { c.TenantID = stranger }, false},
		{"wrong issuer", func(c *Claims) { c.Issuer = "https://untrusted.example/v2.0" }, false},
		{"expired", func(c *Claims) { c.ExpiresAt = jwt.NewNumericDate(now.Add(-time.Minute)) }, false},
		{"not yet valid", func(c *Claims) { c.NotBefore = jwt.NewNumericDate(now.Add(time.Minute)) }, false},
		{"future issued at", func(c *Claims) { c.IssuedAt = jwt.NewNumericDate(now.Add(time.Minute)) }, false},
		{"missing expiry", func(c *Claims) { c.ExpiresAt = nil }, false},
		{"missing not before", func(c *Claims) { c.NotBefore = nil }, false},
		{"missing issued at", func(c *Claims) { c.IssuedAt = nil }, false},
		{"missing object ID", func(c *Claims) { c.ObjectID = "" }, false},
		{"missing subject", func(c *Claims) { c.Subject = "" }, false},
		{"v1 token", func(c *Claims) { c.Version = "1.0" }, false},
		{"unapproved client", func(c *Claims) { c.ClientID = stranger }, false},
		{"ID token without API scope", func(c *Claims) { c.Scope = "" }, false},
		{"ID token with roles but no app marker", func(c *Claims) { c.Scope = ""; c.Roles = []string{"Execution.Invoke"} }, false},
		{"wrong scope", func(c *Claims) { c.Scope = "access_as_user_extra" }, false},
		{"app-only access", func(c *Claims) { c.Scope = ""; c.IdentityType = "app"; c.Roles = []string{"Execution.Invoke"} }, true},
		{"app-only missing role", func(c *Claims) { c.Scope = ""; c.IdentityType = "app" }, false},
		{"app with delegated scope", func(c *Claims) { c.IdentityType = "app"; c.Roles = []string{"Execution.Invoke"} }, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			claims := validClaims(now)
			tc.change(&claims)
			p, err := e.Authenticate(bearer(signed(t, claims, key, jwt.SigningMethodRS256, "key-1")))
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v err=%v", tc.valid, err)
			}
			if err == nil && p.OwnerKey != "entra:"+tenant+":"+subject {
				t.Fatal("ownership is not tenant/object scoped")
			}
			if err == nil && p.Kind == "application" && p.Can("execution.submit") {
				t.Fatal("human grant applied to an application")
			}
		})
	}
	for _, tc := range []struct{ name, token string }{{"forged signature", signed(t, validClaims(now), wrongKey, jwt.SigningMethodRS256, "key-1")}, {"HMAC confusion", signed(t, validClaims(now), []byte("not-an-rsa-key"), jwt.SigningMethodHS256, "key-1")}, {"unsigned", signed(t, validClaims(now), jwt.UnsafeAllowNoneSignatureType, jwt.SigningMethodNone, "key-1")}, {"malformed", "not.a.jwt"}} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := e.Authenticate(bearer(tc.token)); !errors.Is(err, ErrUnauthenticated) {
				t.Fatal("invalid token accepted", err)
			}
		})
	}
}

func TestEntraNoFixtureFallbackAndRevocation(t *testing.T) {
	key := signingKey(t)
	now := time.Now().UTC()
	e := verifier(t, key, now)
	token := signed(t, validClaims(now), key, jwt.SigningMethodRS256, "key-1")
	r := bearer(token)
	r.Header.Set("X-Demo-Principal", "alice")
	if _, err := e.Authenticate(r); !errors.Is(err, ErrUnauthenticated) {
		t.Fatal("fixture injection accepted")
	}
	r = bearer(token)
	r.Header.Add("Authorization", "Bearer "+token)
	if _, err := e.Authenticate(r); !errors.Is(err, ErrUnauthenticated) {
		t.Fatal("duplicate authorization accepted")
	}
	p, err := e.Authenticate(bearer(token))
	if err != nil || !p.Can("execution.submit") {
		t.Fatal("valid current grant denied")
	}
	e.policy = func() (Policy, error) { return Policy{}, nil }
	p, err = e.Authenticate(bearer(token))
	if err != nil || len(p.Permissions()) != 0 {
		t.Fatal("revoked grant retained")
	}
	e.policy = func() (Policy, error) { return Policy{}, errors.New("private filesystem detail") }
	if _, err = e.Authenticate(bearer(token)); err != ErrUnavailable {
		t.Fatal("policy failure was not sanitized and fail-closed")
	}
}

func TestJWKSRotationExpiryAndCooldown(t *testing.T) {
	key1 := signingKey(t)
	key2 := signingKey(t)
	now := time.Now()
	requests := 0
	fail := false
	doc := map[string]any{"keys": []jose.JSONWebKey{{Key: &key1.PublicKey, KeyID: "old", Use: "sig", Algorithm: "RS256"}}}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests++
		if fail {
			w.WriteHeader(503)
			return
		}
		_ = json.NewEncoder(w).Encode(doc)
	}))
	defer srv.Close()
	s := newKeySet(srv.URL, config().Issuer(), srv.Client(), func() time.Time { return now })
	if _, err := s.get(context.Background(), "old"); err != nil {
		t.Fatal(err)
	}
	for range 10 {
		if _, err := s.get(context.Background(), "unknown"); err != ErrUnauthenticated {
			t.Fatal(err)
		}
	}
	if requests != 1 {
		t.Fatal("unknown kid caused request storm")
	}
	now = now.Add(refreshCooldown)
	doc = map[string]any{"keys": []jose.JSONWebKey{{Key: &key2.PublicKey, KeyID: "new", Use: "sig", Algorithm: "RS256"}}}
	if _, err := s.get(context.Background(), "new"); err != nil || requests != 2 {
		t.Fatal("rotation failed", err)
	}
	if _, err := s.get(context.Background(), "old"); err != ErrUnauthenticated {
		t.Fatal("removed key retained")
	}
	now = now.Add(keyTTL)
	fail = true
	if _, err := s.get(context.Background(), "new"); err != ErrUnavailable {
		t.Fatal("expired key accepted during outage")
	}
	if _, err := s.get(context.Background(), "new"); err != ErrUnavailable || requests != 3 {
		t.Fatal("failed refresh not throttled")
	}
}

func TestJWKSConcurrentColdStart(t *testing.T) {
	key := signingKey(t)
	var requests atomic.Int64
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{"keys": []jose.JSONWebKey{{Key: &key.PublicKey, KeyID: "key", Use: "sig", Algorithm: "RS256"}}})
	}))
	defer srv.Close()
	s := newKeySet(srv.URL, config().Issuer(), srv.Client(), time.Now)
	var wg sync.WaitGroup
	for range 20 {
		wg.Go(func() {
			if _, err := s.get(context.Background(), "key"); err != nil {
				t.Error(err)
			}
		})
	}
	wg.Wait()
	if requests.Load() != 1 {
		t.Fatal("duplicate cold-cache fetches")
	}
}

func TestJWKSRejectsBadResponses(t *testing.T) {
	key := signingKey(t)
	pub := jose.JSONWebKey{Key: &key.PublicKey, KeyID: "key", Use: "sig", Algorithm: "RS256"}
	encoded, _ := json.Marshal(pub)
	var foreign map[string]any
	_ = json.Unmarshal(encoded, &foreign)
	foreign["issuer"] = "https://untrusted.example/v2.0"
	for _, tc := range []struct {
		name string
		body any
	}{{"empty keys", map[string]any{"keys": []any{}}}, {"duplicate kid", map[string]any{"keys": []any{pub, pub}}}, {"wrong key issuer", map[string]any{"keys": []any{foreign}}}, {"invalid document", map[string]any{"not-keys": true}}} {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { _ = json.NewEncoder(w).Encode(tc.body) }))
			defer srv.Close()
			s := newKeySet(srv.URL, config().Issuer(), srv.Client(), time.Now)
			if _, err := s.get(context.Background(), "key"); err != ErrUnavailable {
				t.Fatal("bad keys accepted", err)
			}
		})
	}
}
