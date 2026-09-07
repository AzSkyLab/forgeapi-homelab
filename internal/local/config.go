// Package local is intentionally not a production configuration system.
package local

import (
	"errors"
	"forgeapi/internal/auth"
	"net"
	"net/url"
	"os"
	"strings"
)

type Config struct {
	DatabaseURL     string
	TemporalAddress string
	Listen          string
	BaseURL         string
	CursorKey       string
	AuthMode        string
	Entra           auth.Config
}

func Read() (Config, error) {
	if os.Getenv("FORGE_MODE") != "local-demo" {
		return Config{}, errors.New("this binary only supports FORGE_MODE=local-demo; shared deployment and live compute remain disabled")
	}
	c := Config{DatabaseURL: env("DATABASE_URL", "postgres://forge:local-fixture-only@127.0.0.1:54329/forge?sslmode=disable"), TemporalAddress: env("TEMPORAL_ADDRESS", "127.0.0.1:7233"), Listen: env("LISTEN_ADDR", "127.0.0.1:8080"), BaseURL: env("PUBLIC_BASE_URL", "http://localhost:8080"), CursorKey: "local-demo-cursor-key-not-a-production-secret"}
	c.AuthMode = env("AUTH_MODE", "entra")
	if c.AuthMode != "entra" {
		return c, errors.New("AUTH_MODE must be entra; fixture identities are available only inside automated tests")
	}
	c.Entra = auth.Config{TenantID: os.Getenv("AUTH_TENANT_ID"), Audience: os.Getenv("AUTH_AUDIENCE"), AllowedClients: strings.Split(os.Getenv("AUTH_ALLOWED_CLIENT_IDS"), ","), DelegatedScope: env("AUTH_DELEGATED_SCOPE", "executions.access"), ApplicationRole: env("AUTH_APPLICATION_ROLE", "executions.access"), GrantsFile: env("AUTH_GRANTS_FILE", "config/grants.local.json")}
	if err := c.Entra.Validate(); err != nil {
		return c, err
	}
	c.CursorKey = os.Getenv("CURSOR_SIGNING_KEY")
	if len(c.CursorKey) < 32 || c.CursorKey == "local-demo-cursor-key-not-a-production-secret" {
		return c, errors.New("Entra requires a separate CURSOR_SIGNING_KEY of at least 32 characters")
	}
	host, _, err := net.SplitHostPort(c.Listen)
	if err != nil {
		return c, errors.New("invalid LISTEN_ADDR")
	}
	ip := net.ParseIP(host)
	if (ip == nil || !ip.IsLoopback()) && !(host == "0.0.0.0" && os.Getenv("LOCAL_CONTAINER") == "true") {
		return c, errors.New("listen address must be loopback; LOCAL_CONTAINER=true is only for the private Compose network")
	}
	u, err := url.Parse(c.BaseURL)
	if err != nil || u.Scheme != "http" || (u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1") || u.Path != "" || u.RawQuery != "" || u.User != nil || u.Fragment != "" {
		return c, errors.New("PUBLIC_BASE_URL must be a loopback http origin")
	}
	return c, nil
}

func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
