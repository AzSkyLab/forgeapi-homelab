// Package deployment is the bounded local infrastructure spike, independent of
// compute execution lifecycle and Azure SDKs. Created infrastructure is retained.
package deployment

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"os"
	"regexp"
	"strings"
	"time"

	"forgeapi/internal/execution"
)

const PatternID = "azure-key-vault-v1"
const TaskQueue = "forgeapi-local-key-vault"

var ErrConflict = errors.New("deployment conflict")
var ErrNotFound = errors.New("deployment not found")
var ErrDenied = errors.New("deployment denied")

type Target struct {
	TenantID        string   `json:"tenant_id"`
	SubscriptionID  string   `json:"subscription_id"`
	ResourceGroupID string   `json:"resource_group_id"`
	Location        string   `json:"location"`
	Name            string   `json:"name"`
	Owner           string   `json:"owner"`
	PatternDigest   string   `json:"pattern_digest"`
	Executor        Executor `json:"executor,omitzero"`
}

// Only public binding information belongs in accepted intent and saved plans.
// Certificate paths, private keys and tokens remain exclusively on the worker.
type Executor struct {
	Mode              string `json:"mode"`
	ClientID          string `json:"client_id"`
	PrincipalID       string `json:"principal_id"`
	CertificateSHA256 string `json:"certificate_sha256"`
}

func (e Executor) Validate() error {
	uuid := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	if e.Mode != "lab_certificate" || !uuid.MatchString(e.ClientID) || !uuid.MatchString(e.PrincipalID) || !regexp.MustCompile(`^[0-9a-f]{64}$`).MatchString(e.CertificateSHA256) {
		return errors.New("explicit lab certificate executor required; no human CLI fallback")
	}
	return nil
}

func (t Target) ResourceID() string {
	return t.ResourceGroupID + "/providers/Microsoft.KeyVault/vaults/" + t.Name
}

func (t Target) Validate() error {
	uuid := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	prefix := "/subscriptions/" + t.SubscriptionID + "/resourceGroups/"
	rg := strings.TrimPrefix(t.ResourceGroupID, prefix)
	if !uuid.MatchString(t.TenantID) || !uuid.MatchString(t.SubscriptionID) || rg == t.ResourceGroupID || !regexp.MustCompile(`^[A-Za-z0-9_()-]{1,90}$`).MatchString(rg) || !regexp.MustCompile(`^[a-z0-9]{2,40}$`).MatchString(t.Location) || !regexp.MustCompile(`^[a-z][a-z0-9-]{1,22}[a-z0-9]$`).MatchString(t.Name) || strings.Contains(t.Name, "--") || !regexp.MustCompile(`^sha256:[0-9a-f]{64}$`).MatchString(t.PatternDigest) {
		return errors.New("invalid local deployment target")
	}
	parts := strings.Split(t.Owner, ":")
	if len(parts) != 3 || parts[0] != "entra" || parts[1] != t.TenantID || !uuid.MatchString(parts[2]) {
		return errors.New("target needs one explicit Entra owner")
	}
	if t.Executor.PrincipalID == parts[2] {
		return errors.New("caller cannot be the infrastructure executor")
	}
	return t.Executor.Validate()
}

func LoadTarget(path string) (Target, error) {
	var t Target
	f, err := os.Open(path)
	if err != nil {
		return t, errors.New("deployment target unavailable")
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 8193))
	if err != nil || len(b) > 8192 {
		return t, errors.New("invalid deployment target")
	}
	if _, _, err = execution.Object(b); err != nil {
		return t, err
	}
	d := json.NewDecoder(bytes.NewReader(b))
	d.DisallowUnknownFields()
	if err = d.Decode(&t); err != nil {
		return t, errors.New("invalid deployment target")
	}
	return t, t.Validate()
}

type Event struct {
	State string    `json:"state"`
	At    time.Time `json:"at"`
}
type Record struct {
	ID                     string    `json:"deployment_id"`
	PatternID              string    `json:"pattern_id"`
	Target                 Target    `json:"target"`
	State                  string    `json:"state"`
	PlanDigest             string    `json:"plan_digest,omitempty"`
	PlanExpiresAt          time.Time `json:"plan_expires_at,omitempty"`
	ResourceID             string    `json:"resource_id,omitempty"`
	VaultURI               string    `json:"vault_uri,omitempty"`
	ErrorCode              string    `json:"error_code,omitempty"`
	RejectionCorrelationID string    `json:"rejection_correlation_id,omitempty"`
	Events                 []Event   `json:"events"`
}

func (r *Record) Transition(state string) {
	r.State = state
	r.Events = append(r.Events, Event{State: state, At: time.Now().UTC()})
}
func WorkflowID(id, phase string) string { return "forgeapi-deployment-" + id + "-" + phase }
