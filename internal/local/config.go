// Package local is intentionally not a production configuration system.
package local

import (
	"errors"
	"net"
	"net/url"
	"os"
)

type Config struct {
	DatabaseURL     string
	TemporalAddress string
	Listen          string
	BaseURL         string
	CursorKey       string
}

func Read() (Config, error) {
	if os.Getenv("FORGE_MODE") != "local-demo" {
		return Config{}, errors.New("this binary only supports FORGE_MODE=local-demo; enterprise authentication is not implemented")
	}
	c := Config{DatabaseURL: env("DATABASE_URL", "postgres://forge:local-fixture-only@127.0.0.1:54329/forge?sslmode=disable"), TemporalAddress: env("TEMPORAL_ADDRESS", "127.0.0.1:7233"), Listen: env("LISTEN_ADDR", "127.0.0.1:8080"), BaseURL: env("PUBLIC_BASE_URL", "http://localhost:8080"), CursorKey: "local-demo-cursor-key-not-a-production-secret"}
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
