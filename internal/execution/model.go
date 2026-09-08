// Package execution owns portable input policy and execution state, not HTTP or cloud SDKs.
package execution

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"
)

const Application = "software-factory"
const Environment = "development"
const TemplateID = "pr-validation-v1"
const TemplateVersion = "1.0.0"

func ID(prefix string) string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		panic(err)
	}
	return prefix + hex.EncodeToString(b[:])
}

type Spec struct {
	ApplicationID        string            `json:"application_id"`
	Environment          string            `json:"environment"`
	TemplateID           string            `json:"template_id"`
	TemplateVersion      string            `json:"template_version"`
	InputArtifactRefs    []string          `json:"input_artifact_refs"`
	Image                string            `json:"image"`
	Command              []string          `json:"command"`
	ComputeProfile       string            `json:"compute_profile"`
	ExecutionClass       string            `json:"execution_class"`
	PlacementPolicy      string            `json:"placement_policy"`
	NetworkProfile       string            `json:"network_profile"`
	TimeoutSeconds       int               `json:"timeout_seconds"`
	EnvironmentVariables map[string]string `json:"environment_variables"`
	SecretRefs           []string          `json:"secret_refs"`
}

type Failure struct {
	Code    string `json:"code"`
	Message string `json:"message"`
	Origin  string `json:"origin"`
}

type Cancellation struct {
	ID          string    `json:"id"`
	ExecutionID string    `json:"execution_id"`
	Status      string    `json:"status"`
	RequestedAt time.Time `json:"requested_at"`
	DeadlineAt  time.Time `json:"deadline_at"`
	Reason      string    `json:"reason,omitempty"`
}

type Execution struct {
	ID                   string            `json:"id"`
	ApplicationID        string            `json:"application_id"`
	Environment          string            `json:"environment"`
	TemplateID           string            `json:"template_id"`
	TemplateVersion      string            `json:"template_version"`
	State                string            `json:"state"`
	CleanupState         string            `json:"cleanup_state"`
	DeliveryStatus       string            `json:"delivery_status"`
	InfrastructureStatus string            `json:"infrastructure_status"`
	DispatchStatus       string            `json:"dispatch_status"`
	ResultComplete       bool              `json:"result_complete"`
	Revision             int64             `json:"revision"`
	CreatedAt            time.Time         `json:"created_at"`
	DeadlineAt           time.Time         `json:"deadline_at"`
	StartedAt            *time.Time        `json:"started_at,omitempty"`
	ObservedAt           *time.Time        `json:"observed_at,omitempty"`
	CompletedAt          *time.Time        `json:"completed_at,omitempty"`
	TimedOutFrom         string            `json:"timed_out_from,omitempty"`
	Error                *Failure          `json:"error,omitempty"`
	CleanupError         *Failure          `json:"cleanup_error,omitempty"`
	DeliveryError        *Failure          `json:"delivery_error,omitempty"`
	Cancellation         *Cancellation     `json:"cancellation,omitempty"`
	Links                map[string]string `json:"links"`
}

func Terminal(state string) bool {
	switch state {
	case "succeeded", "failed", "cancelled", "timed_out":
		return true
	}
	return false
}

type Event struct {
	ID                   string        `json:"id"`
	ExecutionID          string        `json:"execution_id"`
	Sequence             int64         `json:"sequence"`
	EventType            string        `json:"event_type"`
	RecordedAt           time.Time     `json:"recorded_at"`
	Revision             int64         `json:"revision"`
	State                string        `json:"state"`
	CleanupState         string        `json:"cleanup_state"`
	DeliveryStatus       string        `json:"delivery_status"`
	InfrastructureStatus string        `json:"infrastructure_status"`
	DispatchStatus       string        `json:"dispatch_status"`
	ResultComplete       bool          `json:"result_complete"`
	Cancellation         *Cancellation `json:"cancellation,omitempty"`
	Error                *Failure      `json:"error,omitempty"`
	CleanupError         *Failure      `json:"cleanup_error,omitempty"`
	DeliveryError        *Failure      `json:"delivery_error,omitempty"`
}

type Log struct {
	Sequence   int64     `json:"sequence"`
	Stream     string    `json:"stream"`
	Source     string    `json:"source"`
	RecordedAt time.Time `json:"recorded_at"`
	Text       string    `json:"text"`
}

type Artifact struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Digest      string    `json:"digest"`
	SizeBytes   int       `json:"size_bytes"`
	MediaType   string    `json:"media_type"`
	DownloadURL string    `json:"download_url"`
	ExpiresAt   time.Time `json:"expires_at"`
}

type Result struct {
	ExecutionID    string     `json:"execution_id"`
	ExitCode       *int       `json:"exit_code,omitempty"`
	DeliveryStatus string     `json:"delivery_status"`
	ResultComplete bool       `json:"result_complete"`
	Error          *Failure   `json:"error,omitempty"`
	Artifacts      []Artifact `json:"artifacts"`
}

// Record is private storage. Never serialize it as a public API response.
type Record struct {
	Correlation  Correlation
	CoreVersion  int
	Execution    Execution
	Owner        string
	Spec         Spec
	Events       []Event
	Logs         []Log
	Result       Result
	ArtifactData []byte
	Applied      map[string]bool
}

func NewRecord(owner, base string, spec Spec, now time.Time) Record {
	id := ID("exec_")
	self := base + "/executions/" + id
	r := Record{Owner: owner, Spec: spec, Applied: map[string]bool{}, Logs: []Log{}, Events: []Event{},
		Execution: Execution{ID: id, ApplicationID: spec.ApplicationID, Environment: spec.Environment,
			TemplateID: spec.TemplateID, TemplateVersion: spec.TemplateVersion, State: "accepted",
			CleanupState: "pending", DeliveryStatus: "pending", InfrastructureStatus: "not_allocated",
			DispatchStatus: "pending", CreatedAt: now, DeadlineAt: now.Add(time.Duration(spec.TimeoutSeconds) * time.Second),
			Links: map[string]string{"self": self, "events": self + "/events", "logs": self + "/logs", "results": self + "/results"}},
		Result: Result{ExecutionID: id, DeliveryStatus: "pending", Artifacts: []Artifact{}},
	}
	r.Event("execution.accepted", now)
	return r
}

func (r *Record) Event(kind string, now time.Time) {
	r.Execution.Revision++
	e := r.Execution
	var cancel *Cancellation
	if e.Cancellation != nil {
		c := *e.Cancellation
		cancel = &c
	}
	r.Events = append(r.Events, Event{ID: fmt.Sprintf("event-%d", e.Revision), ExecutionID: e.ID,
		Sequence: int64(len(r.Events) + 1), EventType: kind, RecordedAt: now, Revision: e.Revision,
		State: e.State, CleanupState: e.CleanupState, DeliveryStatus: e.DeliveryStatus,
		InfrastructureStatus: e.InfrastructureStatus, DispatchStatus: e.DispatchStatus,
		ResultComplete: e.ResultComplete, Cancellation: cancel, Error: e.Error, CleanupError: e.CleanupError, DeliveryError: e.DeliveryError})
}
