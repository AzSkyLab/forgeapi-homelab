package keyvaultrunner

import (
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"forgeapi/internal/deployment"
)

func certificateFixture(t *testing.T, start, end time.Time) executorConfig {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	c := &x509.Certificate{SerialNumber: big.NewInt(1), NotBefore: start, NotAfter: end, KeyUsage: x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, c, c, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	if err = os.Chmod(dir, 0700); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(dir, "executor.pem")
	b := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	b = append(b, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})...)
	if err = os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	return executorConfig{Executor: deployment.Executor{Mode: "lab_certificate", ClientID: "44444444-4444-4444-4444-444444444444", PrincipalID: "55555555-5555-5555-5555-555555555555", CertificateSHA256: fmt.Sprintf("%x", sha256.Sum256(der))}, TenantID: "11111111-1111-1111-1111-111111111111", CertificatePath: path}
}

func TestCertificateBoundary(t *testing.T) {
	now := time.Now().Truncate(time.Second)
	c := certificateFixture(t, now.Add(-time.Minute), now.Add(time.Hour))
	if _, err := secureCertificate(c, now); err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"missing", "fingerprint", "relative", "symlink", "file-permissions", "directory-permissions", "expired", "not-yet-valid", "overlong"} {
		t.Run(kind, func(t *testing.T) {
			bad := c
			switch kind {
			case "missing":
				bad.CertificatePath += ".missing"
			case "fingerprint":
				bad.CertificateSHA256 = strings.Repeat("0", 64)
			case "relative":
				bad.CertificatePath = "relative.pem"
			case "symlink":
				bad.CertificatePath = filepath.Join(t.TempDir(), "link.pem")
				if err := os.Symlink(c.CertificatePath, bad.CertificatePath); err != nil {
					t.Fatal(err)
				}
			case "file-permissions":
				if err := os.Chmod(c.CertificatePath, 0644); err != nil {
					t.Fatal(err)
				}
				defer os.Chmod(c.CertificatePath, 0600)
			case "directory-permissions":
				if err := os.Chmod(filepath.Dir(c.CertificatePath), 0755); err != nil {
					t.Fatal(err)
				}
				defer os.Chmod(filepath.Dir(c.CertificatePath), 0700)
			case "expired":
				bad = certificateFixture(t, now.Add(-2*time.Hour), now.Add(-time.Hour))
			case "not-yet-valid":
				bad = certificateFixture(t, now.Add(time.Hour), now.Add(2*time.Hour))
			case "overlong":
				bad = certificateFixture(t, now.Add(-time.Minute), now.Add(8*24*time.Hour))
			}
			if _, err := secureCertificate(bad, now); err == nil {
				t.Fatal("unsafe certificate admitted")
			}
		})
	}
}

func TestExecutorConfigAndEnvironmentHaveNoFallback(t *testing.T) {
	c := certificateFixture(t, time.Now().Add(-time.Minute), time.Now().Add(time.Hour))
	path := filepath.Join(t.TempDir(), "identity.json")
	b, _ := json.Marshal(c)
	if err := os.WriteFile(path, b, 0600); err != nil {
		t.Fatal(err)
	}
	e, err := LoadExecutor(path)
	if err != nil {
		t.Fatal(err)
	}
	target := deployment.Target{TenantID: c.TenantID, Executor: c.Executor}
	for _, k := range []string{"ARM_CLIENT_SECRET", "ARM_CLIENT_CERTIFICATE", "ARM_CLIENT_CERTIFICATE_PATH", "AZURE_CLIENT_SECRET", "AZURE_CLIENT_ID", "AZURE_CONFIG_DIR", "TF_VAR_executor_client_id", "ARM_OIDC_TOKEN", "ARM_USE_CLI", "ARM_USE_MSI", "IDENTITY_ENDPOINT", "IDENTITY_HEADER"} {
		t.Setenv(k, "credential-canary")
	}
	env, err := e.terraformEnv(target)
	if err != nil || strings.Contains(strings.Join(env, "\n"), "canary") || !strings.Contains(strings.Join(env, "\n"), "ARM_CLIENT_CERTIFICATE_PATH="+c.CertificatePath) {
		t.Fatal("credential environment boundary failed", err)
	}
	changed := target
	changed.Executor.PrincipalID = "66666666-6666-6666-6666-666666666666"
	if _, err = e.terraformEnv(changed); err == nil {
		t.Fatal("executor substitution admitted")
	}
	if _, err = (*ExecutorClient)(nil).terraformEnv(target); err == nil {
		t.Fatal("missing executor admitted")
	}
	if err = os.WriteFile(path, []byte(strings.TrimSuffix(string(b), "}")+`,"client_secret":"credential-canary"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err = LoadExecutor(path); err == nil || strings.Contains(err.Error(), "canary") {
		t.Fatal("secret fallback or disclosure")
	}
	if err = os.Rename(c.CertificatePath, c.CertificatePath+".unavailable"); err != nil {
		t.Fatal(err)
	}
	if _, err = e.terraformEnv(target); err == nil {
		t.Fatal("missing certificate fell back")
	}
}

func TestIssuedTokenIdentityBinding(t *testing.T) {
	now := time.Now()
	c := executorConfig{Executor: deployment.Executor{ClientID: "client", PrincipalID: "principal"}, TenantID: "tenant"}
	base := map[string]any{"tid": "tenant", "oid": "principal", "appid": "client", "aud": "https://management.azure.com/", "exp": now.Add(time.Hour).Unix()}
	encoded := func(m map[string]any) string {
		b, _ := json.Marshal(m)
		return "unit-only." + base64.RawURLEncoding.EncodeToString(b) + ".not-a-live-token"
	}
	if err := tokenIdentity(encoded(base), c, now); err != nil {
		t.Fatal(err)
	}
	for k, v := range map[string]any{"tid": "wrong", "oid": "human", "appid": "wrong", "scp": "delegated", "aud": "https://graph.microsoft.com", "exp": now.Add(-time.Minute).Unix()} {
		bad := map[string]any{}
		for k, v := range base {
			bad[k] = v
		}
		bad[k] = v
		if tokenIdentity(encoded(bad), c, now) == nil {
			t.Fatal("incorrect token binding admitted", k)
		}
	}
}

type roundTripFunc func(*http.Request) (*http.Response, error)

func (f roundTripFunc) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestARMReadBoundaryAndRedaction(t *testing.T) {
	calls := 0
	e := &ExecutorClient{token: func(context.Context) (string, error) { return "unit-token-canary", nil }, http: &http.Client{CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect denied") }}}
	e.http.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		if r.Method != "GET" || r.URL.Scheme != "https" || r.URL.Host != "management.azure.com" || r.Header.Get("Authorization") != "Bearer unit-token-canary" {
			t.Fatal("wrong ARM request")
		}
		return &http.Response{StatusCode: 403, Body: io.NopCloser(strings.NewReader("raw-private-canary")), Header: http.Header{}, Request: r}, nil
	})
	for _, path := range []string{"https://evil.example/", "//evil.example/", "/tenants/test"} {
		if _, err := e.get(context.Background(), path); err == nil {
			t.Fatal("unapproved URL")
		}
	}
	if calls != 0 {
		t.Fatal("invalid URL sent")
	}
	if _, err := e.get(context.Background(), "/subscriptions/test"); err == nil || strings.Contains(err.Error(), "canary") {
		t.Fatal("raw ARM error exposed")
	}
	e.http.Transport = roundTripFunc(func(r *http.Request) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 302, Header: http.Header{"Location": []string{"https://evil.example/"}}, Body: io.NopCloser(strings.NewReader("")), Request: r}, nil
	})
	if _, err := e.get(context.Background(), "/subscriptions/test"); err == nil || calls != 2 {
		t.Fatal("ARM redirect followed")
	}
}

func TestIdentityProbeCannotContainManagedResources(t *testing.T) {
	b, err := identityCheckSource()
	if err != nil || strings.Contains(string(b), `resource "`) || !strings.Contains(string(b), `data "azapi_resource" "existing"`) || !strings.Contains(string(b), "use_cli                    = false") {
		t.Fatal("probe isn't data-only certificate auth", err)
	}
	target := deployment.Target{ResourceGroupID: "/approved", Name: "vault"}
	b, _ = json.Marshal(map[string]any{"planned_values": map[string]any{"outputs": map[string]any{"resource_id": map[string]string{"value": target.ResourceID()}, "provisioning_state": map[string]string{"value": "Succeeded"}}}})
	if validateIdentityCheck(b, target) != nil {
		t.Fatal("valid read rejected")
	}
	bad := strings.Replace(string(b), `"planned_values"`, `"resource_changes":[{"address":"azapi_resource.vault","mode":"managed"}],"planned_values"`, 1)
	if validateIdentityCheck([]byte(bad), target) == nil {
		t.Fatal("managed resource admitted")
	}
}
