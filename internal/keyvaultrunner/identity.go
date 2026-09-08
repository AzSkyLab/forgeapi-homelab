package keyvaultrunner

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"forgeapi/internal/deployment"
	"forgeapi/internal/execution"
	"github.com/AzureAD/microsoft-authentication-library-for-go/apps/confidential"
)

type executorConfig struct {
	deployment.Executor
	TenantID        string `json:"tenant_id"`
	CertificatePath string `json:"certificate_path"`
}

// ExecutorClient is certificate-only. No DefaultAzureCredential, CLI, environment
// credential chain or persistent token cache is used. This is a lab exception,
// not an emulation of an Azure managed-identity endpoint.
type ExecutorClient struct {
	config executorConfig
	token  func(context.Context) (string, error)
	http   *http.Client
}

func secureCertificate(c executorConfig, now time.Time) ([]byte, error) {
	deny := errors.New("lab certificate missing, unsafe, expired or does not match approved fingerprint")
	path := c.CertificatePath
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil || !filepath.IsAbs(path) || resolved != filepath.Clean(path) {
		return nil, deny
	}
	info, err := os.Stat(filepath.Dir(path))
	if err != nil || !info.IsDir() || info.Mode().Perm()&0077 != 0 {
		return nil, deny
	}
	f, err := os.Open(path)
	if err != nil {
		return nil, deny
	}
	defer f.Close()
	info, err = f.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return nil, deny
	}
	b, err := io.ReadAll(io.LimitReader(f, 65537))
	if err != nil || len(b) > 65536 {
		return nil, deny
	}
	certs, key, err := confidential.CertFromPEM(b, "")
	if err != nil || len(certs) != 1 {
		return nil, deny
	}
	cert := certs[0]
	if now.Before(cert.NotBefore) || !now.Before(cert.NotAfter) || cert.NotAfter.Sub(cert.NotBefore) > 7*24*time.Hour || fmt.Sprintf("%x", sha256.Sum256(cert.Raw)) != c.CertificateSHA256 {
		return nil, deny
	}
	if _, err = confidential.NewCredFromCert(certs, key); err != nil {
		return nil, deny
	}
	return b, nil
}

func LoadExecutor(path string) (*ExecutorClient, error) {
	deny := errors.New("invalid lab executor configuration; no human CLI fallback")
	f, err := os.Open(path)
	if err != nil {
		return nil, deny
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 8193))
	if err != nil || len(b) > 8192 {
		return nil, deny
	}
	if _, _, err = execution.Object(b); err != nil {
		return nil, deny
	}
	var c executorConfig
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if d.Decode(&c) != nil || c.Executor.Validate() != nil {
		return nil, deny
	}
	// Reuse the UUID validation without accepting alternate authority hosts.
	probe := c.Executor
	probe.ClientID = c.TenantID
	if probe.Validate() != nil {
		return nil, deny
	}
	b, err = secureCertificate(c, time.Now())
	if err != nil {
		return nil, err
	}
	certs, key, _ := confidential.CertFromPEM(b, "")
	cred, err := confidential.NewCredFromCert(certs, key)
	if err != nil {
		return nil, deny
	}
	h := &http.Client{Timeout: 30 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("identity/ARM redirects forbidden") }}
	client, err := confidential.New("https://login.microsoftonline.com/"+c.TenantID, c.ClientID, cred, confidential.WithHTTPClient(h))
	if err != nil {
		return nil, deny
	}
	e := &ExecutorClient{config: c, http: h}
	e.token = func(ctx context.Context) (string, error) {
		if _, err := secureCertificate(c, time.Now()); err != nil {
			return "", err
		}
		result, err := client.AcquireTokenByCredential(ctx, []string{"https://management.azure.com/.default"})
		if err != nil || result.AccessToken == "" {
			return "", errors.New("lab certificate authentication failed; no fallback; raw diagnostics suppressed")
		}
		if err = tokenIdentity(result.AccessToken, c, time.Now()); err != nil {
			return "", err
		}
		return result.AccessToken, nil
	}
	return e, nil
}

// The token here comes only from MSAL's certificate-authenticated HTTPS exchange,
// never from an API caller. These are extra binding checks, not a JWT verifier.
// The ARM server performs actual access-token signature/authorization validation.
func tokenIdentity(token string, c executorConfig, now time.Time) error {
	deny := errors.New("issued ARM token does not match the approved non-human executor")
	parts := strings.Split(token, ".")
	if len(parts) != 3 {
		return deny
	}
	b, err := base64.RawURLEncoding.DecodeString(parts[1])
	var claims struct {
		Tenant    string `json:"tid"`
		Principal string `json:"oid"`
		App       string `json:"appid"`
		AZP       string `json:"azp"`
		Scope     string `json:"scp"`
		Audience  string `json:"aud"`
		Expires   int64  `json:"exp"`
	}
	if err != nil || json.Unmarshal(b, &claims) != nil {
		return deny
	}
	app := claims.App
	if app == "" {
		app = claims.AZP
	}
	if claims.Tenant != c.TenantID || claims.Principal != c.PrincipalID || app != c.ClientID || claims.Scope != "" || claims.Expires <= now.Unix() {
		return deny
	}
	if claims.Audience != "https://management.azure.com" && claims.Audience != "https://management.azure.com/" && claims.Audience != "https://management.core.windows.net/" {
		return deny
	}
	return nil
}

func (e *ExecutorClient) matches(t deployment.Target) bool {
	return e != nil && e.config.TenantID == t.TenantID && e.config.Executor == t.Executor && t.Executor.Validate() == nil
}

func (e *ExecutorClient) get(ctx context.Context, path string) ([]byte, error) {
	u, err := url.ParseRequestURI(path)
	if e == nil || err != nil || !strings.HasPrefix(path, "/subscriptions/") || u.IsAbs() || u.Host != "" {
		return nil, errors.New("ARM path rejected")
	}
	token, err := e.token(ctx)
	if err != nil {
		return nil, err
	}
	r, err := http.NewRequestWithContext(ctx, http.MethodGet, "https://management.azure.com"+path, nil)
	if err != nil {
		return nil, errors.New("ARM request failed")
	}
	r.Header.Set("Authorization", "Bearer "+token)
	resp, err := e.http.Do(r)
	if err != nil {
		return nil, errors.New("ARM request failed; raw diagnostics suppressed")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("ARM read failed (HTTP %d); raw diagnostics suppressed", resp.StatusCode)
	}
	b, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024+1))
	if err != nil || len(b) > 4*1024*1024 {
		return nil, errors.New("invalid ARM response")
	}
	return b, nil
}

func (e *ExecutorClient) terraformEnv(t deployment.Target) ([]string, error) {
	if !e.matches(t) {
		return nil, errors.New("Terraform executor binding changed")
	}
	if _, err := secureCertificate(e.config, time.Now()); err != nil {
		return nil, err
	}
	return append(commandEnv(), "ARM_CLIENT_CERTIFICATE_PATH="+e.config.CertificatePath), nil
}
