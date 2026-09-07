package auth

import (
	"context"
	"crypto/rsa"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/go-jose/go-jose/v4"
)

const keyTTL = 5 * time.Minute
const refreshCooldown = 15 * time.Second

type keySet struct {
	mu          sync.Mutex
	url         string
	issuer      string
	client      *http.Client
	now         func() time.Time
	keys        map[string]*rsa.PublicKey
	expires     time.Time
	lastAttempt time.Time
}

func newKeySet(url, issuer string, client *http.Client, now func() time.Time) *keySet {
	return &keySet{url: url, issuer: issuer, client: client, now: now, keys: map[string]*rsa.PublicKey{}}
}

func (s *keySet) get(ctx context.Context, kid string) (*rsa.PublicKey, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	if now.Before(s.expires) && s.keys[kid] != nil {
		return s.keys[kid], nil
	}
	// A new kid triggers refresh, but attacker-chosen kids cannot cause one
	// outbound request per token. Expired keys are never served on fetch failure.
	if !s.lastAttempt.IsZero() && now.Sub(s.lastAttempt) < refreshCooldown {
		if !now.Before(s.expires) {
			return nil, ErrUnavailable
		}
		return nil, ErrUnauthenticated
	}
	s.lastAttempt = now
	r, err := http.NewRequestWithContext(ctx, http.MethodGet, s.url, nil)
	if err != nil {
		return nil, ErrUnavailable
	}
	resp, err := s.client.Do(r)
	if err != nil {
		return nil, ErrUnavailable
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, ErrUnavailable
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024+1))
	if err != nil || len(body) > 1024*1024 {
		return nil, ErrUnavailable
	}
	var doc struct {
		Keys []json.RawMessage `json:"keys"`
	}
	if json.Unmarshal(body, &doc) != nil || len(doc.Keys) == 0 || len(doc.Keys) > 100 {
		return nil, ErrUnavailable
	}
	keys := map[string]*rsa.PublicKey{}
	for _, raw := range doc.Keys {
		var key jose.JSONWebKey
		var scope struct {
			Issuer string `json:"issuer"`
		}
		if json.Unmarshal(raw, &key) != nil || json.Unmarshal(raw, &scope) != nil {
			return nil, ErrUnavailable
		}
		if scope.Issuer != "" && scope.Issuer != s.issuer {
			tenant := strings.TrimSuffix(strings.TrimPrefix(s.issuer, "https://login.microsoftonline.com/"), "/v2.0")
			if strings.ReplaceAll(scope.Issuer, "{tenantid}", tenant) != s.issuer {
				continue
			}
		}
		pub, ok := key.Key.(*rsa.PublicKey)
		if !ok || !key.Valid() || pub.N.BitLen() < 2048 || key.KeyID == "" || len(key.KeyID) > 256 || (key.Use != "" && key.Use != "sig") || (key.Algorithm != "" && key.Algorithm != "RS256") {
			continue
		}
		if keys[key.KeyID] != nil {
			return nil, ErrUnavailable
		}
		keys[key.KeyID] = pub
	}
	if len(keys) == 0 {
		return nil, ErrUnavailable
	}
	s.keys = keys
	s.expires = now.Add(keyTTL)
	if keys[kid] == nil {
		return nil, ErrUnauthenticated
	}
	return keys[kid], nil
}
