package auth

import (
	"context"
	"errors"
	"net/http"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

var uuid = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
var claimName = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9._-]{0,127}$`)

type Config struct {
	TenantID        string
	Audience        string
	AllowedClients  []string
	DelegatedScope  string
	ApplicationRole string
	GrantsFile      string
}

func (c Config) Validate() error {
	if !uuid.MatchString(c.TenantID) || !uuid.MatchString(c.Audience) {
		return errors.New("AUTH_TENANT_ID and AUTH_AUDIENCE must be lowercase UUIDs (Entra v2 API client ID, not an api:// URI)")
	}
	if len(c.AllowedClients) == 0 || len(c.AllowedClients) > 32 {
		return errors.New("AUTH_ALLOWED_CLIENT_IDS must list 1–32 client UUIDs")
	}
	seen := map[string]bool{}
	for _, id := range c.AllowedClients {
		if !uuid.MatchString(id) || seen[id] {
			return errors.New("AUTH_ALLOWED_CLIENT_IDS contains an invalid or duplicate UUID")
		}
		seen[id] = true
	}
	if !claimName.MatchString(c.DelegatedScope) || !claimName.MatchString(c.ApplicationRole) || c.GrantsFile == "" {
		return errors.New("AUTH_DELEGATED_SCOPE, AUTH_APPLICATION_ROLE and AUTH_GRANTS_FILE are required")
	}
	return nil
}

func (c Config) Issuer() string { return "https://login.microsoftonline.com/" + c.TenantID + "/v2.0" }

type Claims struct {
	jwt.RegisteredClaims
	TenantID     string   `json:"tid"`
	ObjectID     string   `json:"oid"`
	Version      string   `json:"ver"`
	ClientID     string   `json:"azp"`
	Scope        string   `json:"scp"`
	Roles        []string `json:"roles"`
	IdentityType string   `json:"idtyp"`
}

type Entra struct {
	config Config
	keys   *keySet
	policy func() (Policy, error)
	now    func() time.Time
}

func NewEntra(c Config) (*Entra, error) {
	if err := c.Validate(); err != nil {
		return nil, err
	}
	if _, err := LoadPolicy(c.GrantsFile); err != nil {
		return nil, err
	}
	// Neither token-supplied jku/x5u nor arbitrary issuer/JWKS URLs are followed.
	endpoint := "https://login.microsoftonline.com/" + c.TenantID + "/discovery/v2.0/keys"
	client := &http.Client{Timeout: 5 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return &Entra{config: c, keys: newKeySet(endpoint, c.Issuer(), client, time.Now), policy: func() (Policy, error) { return LoadPolicy(c.GrantsFile) }, now: time.Now}, nil
}

func (e *Entra) Authenticate(r *http.Request) (Principal, error) {
	if len(r.Header.Values("X-Demo-Principal")) != 0 || len(r.Header.Values("Authorization")) != 1 {
		return Principal{}, ErrUnauthenticated
	}
	h := strings.Fields(r.Header.Get("Authorization"))
	if len(h) != 2 || !strings.EqualFold(h[0], "Bearer") || len(h[1]) > 16384 {
		return Principal{}, ErrUnauthenticated
	}
	p, err := e.verify(r.Context(), h[1])
	if err != nil {
		return Principal{}, err
	}
	// Re-read the small local grant file per request: revocation is not cached
	// in a token, cursor or session. Malformed/missing policy fails closed.
	policy, err := e.policy()
	if err != nil {
		return Principal{}, ErrUnavailable
	}
	return policy.Apply(p), nil
}

func (e *Entra) verify(ctx context.Context, raw string) (Principal, error) {
	var claims Claims
	var keyErr error
	_, err := jwt.ParseWithClaims(raw, &claims, func(token *jwt.Token) (any, error) {
		kid, ok := token.Header["kid"].(string)
		if !ok || kid == "" || len(kid) > 256 || token.Header["crit"] != nil || token.Header["jku"] != nil || token.Header["x5u"] != nil || token.Header["jwk"] != nil {
			return nil, ErrUnauthenticated
		}
		// Reject obvious issuer/tenant/audience mismatches before any remote I/O;
		// these unverified claims never confer authority.
		if claims.TenantID != e.config.TenantID || claims.Issuer != e.config.Issuer() || len(claims.Audience) != 1 || claims.Audience[0] != e.config.Audience {
			return nil, ErrUnauthenticated
		}
		key, err := e.keys.get(ctx, kid)
		keyErr = err
		return key, err
	}, jwt.WithValidMethods([]string{"RS256"}), jwt.WithIssuer(e.config.Issuer()), jwt.WithAudience(e.config.Audience), jwt.WithExpirationRequired(), jwt.WithIssuedAt(), jwt.WithLeeway(30*time.Second), jwt.WithTimeFunc(e.now), jwt.WithStrictDecoding())
	if errors.Is(keyErr, ErrUnavailable) {
		return Principal{}, ErrUnavailable
	}
	if err != nil || claims.Version != "2.0" || !uuid.MatchString(claims.ObjectID) || claims.Subject == "" || len(claims.Subject) > 256 || claims.NotBefore == nil || claims.IssuedAt == nil || claims.ExpiresAt == nil || !claims.ExpiresAt.After(claims.IssuedAt.Time) || !claims.ExpiresAt.After(claims.NotBefore.Time) || !slices.Contains(e.config.AllowedClients, claims.ClientID) {
		return Principal{}, ErrUnauthenticated
	}
	kind := "human"
	if claims.IdentityType == "app" {
		if claims.Scope != "" || !slices.Contains(claims.Roles, e.config.ApplicationRole) {
			return Principal{}, ErrUnauthenticated
		}
		kind = "application"
	} else if (claims.IdentityType != "" && claims.IdentityType != "user") || !slices.Contains(strings.Fields(claims.Scope), e.config.DelegatedScope) {
		return Principal{}, ErrUnauthenticated
	}
	return Principal{ID: claims.ObjectID, Kind: kind, Tenant: claims.TenantID, OwnerKey: "entra:" + claims.TenantID + ":" + claims.ObjectID}, nil
}
