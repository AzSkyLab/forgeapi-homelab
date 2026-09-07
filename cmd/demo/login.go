package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/AzureAD/microsoft-authentication-library-for-go/apps/public"
	"github.com/google/uuid"
)

type loginConfig struct{ tenant, audience, client string }

// Read identifiers as data, never source .env as shell code. This client does
// not need the API's cursor key, grants, database password or Azure CLI cache.
func readLoginConfig(r io.Reader) (loginConfig, error) {
	var c loginConfig
	seen := map[string]bool{}
	s := bufio.NewScanner(io.LimitReader(r, 65537))
	total := 0
	for s.Scan() {
		total += len(s.Bytes()) + 1
		if total > 65536 {
			return c, errors.New(".env exceeds 64 KiB")
		}
		line := strings.TrimSpace(s.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			return c, errors.New(".env must contain literal KEY=value lines")
		}
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if key == "AUTH_CLIENT_SECRET" || key == "AZURE_CLIENT_SECRET" {
			return c, errors.New("client secrets are not supported; use browser sign-in")
		}
		var target *string
		switch key {
		case "AUTH_TENANT_ID":
			target = &c.tenant
		case "AUTH_AUDIENCE":
			target = &c.audience
		case "AUTH_CLIENT_ID":
			target = &c.client
		default:
			continue
		}
		id, err := uuid.Parse(value)
		if seen[key] || err != nil || id.String() != value || id == uuid.Nil {
			return c, fmt.Errorf("%s must occur once and contain an unquoted lowercase UUID", key)
		}
		seen[key] = true
		*target = value
	}
	if s.Err() != nil || c.tenant == "" || c.audience == "" || c.client == "" {
		return c, errors.New(".env needs AUTH_TENANT_ID, AUTH_AUDIENCE and AUTH_CLIENT_ID")
	}
	return c, nil
}

func signIn(ctx context.Context, path string, printURL bool) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", errors.New("cannot open configuration; complete docs/entra-local.md first")
	}
	c, err := readLoginConfig(f)
	_ = f.Close()
	if err != nil {
		return "", err
	}
	client, err := public.New(c.client, public.WithAuthority("https://login.microsoftonline.com/"+c.tenant))
	if err != nil {
		return "", errors.New("cannot initialize Microsoft sign-in")
	}
	opts := []public.AcquireInteractiveOption{public.WithRedirectURI("http://localhost:8400")}
	if printURL {
		opts = append(opts, public.WithOpenURL(func(url string) error {
			fmt.Fprintln(os.Stderr, "Open this sign-in URL in a browser ON THIS MACHINE:\n"+url)
			return nil
		}))
	}
	fmt.Println("Sign in to Entra in your browser. No client secret; tokens stay in this process.")
	result, err := client.AcquireTokenInteractive(ctx, []string{"api://" + c.audience + "/executions.access"}, opts...)
	if err != nil || result.AccessToken == "" {
		// SDK errors can contain response bodies or identity details. The browser
		// shows actionable Entra errors; do not dump them into demo/meeting logs.
		return "", errors.New("Entra sign-in failed; check the browser error, tenant consent, client redirect URI and port 8400")
	}
	return result.AccessToken, nil
}
