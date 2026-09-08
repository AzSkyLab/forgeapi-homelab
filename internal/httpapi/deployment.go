package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"

	"forgeapi/internal/deployment"
	"forgeapi/internal/execution"
	"github.com/go-chi/chi/v5"
)

type DeploymentRepository interface {
	CreateDeployment(context.Context, string, string, string, deployment.Target) ([]byte, error)
	GetDeployment(context.Context, string) (deployment.Record, error)
	ApproveDeployment(context.Context, string, string, string, deployment.Target) error
}

func (a *API) deploymentAccess(w http.ResponseWriter, r *http.Request) (deployment.Target, bool) {
	if a.Deployments == nil || a.DeploymentTarget == nil {
		a.problem(w, r, 404, "not_found", "Local deployment spike is not configured.")
		return deployment.Target{}, false
	}
	t, err := a.DeploymentTarget()
	if err != nil {
		a.problem(w, r, 503, "deployment_unavailable", "Current deployment target is unavailable.")
		return t, false
	}
	p := principal(r)
	// A synthetic developer grant alone never confers live Azure authority.
	// The separately configured exact human owner must still have current access.
	if p.Kind != "human" || p.Role != "developer" || p.OwnerKey != t.Owner || p.Tenant != t.TenantID {
		a.problem(w, r, 403, "policy_denied", "Explicit local deployment owner permission required.")
		return t, false
	}
	if len(r.URL.Query()) != 0 {
		a.problem(w, r, 400, "invalid_request", "Query parameters are not supported.")
		return t, false
	}
	return t, true
}
func (a *API) deploymentPatterns(w http.ResponseWriter, r *http.Request) {
	target, ok := a.deploymentAccess(w, r)
	if !ok {
		return
	}
	a.json(w, 200, map[string]any{"items": []any{map[string]any{"pattern_id": deployment.PatternID, "description": "LIVE AZURE: create one empty Standard vault; no data-plane operations; explicit saved-plan approval required", "target": target, "actions": []string{"create"}, "retained_after_success": true}}})
}
func (a *API) createDeployment(w http.ResponseWriter, r *http.Request) {
	target, ok := a.deploymentAccess(w, r)
	if !ok {
		return
	}
	b, ok := a.body(w, r, false)
	if !ok {
		return
	}
	fields, hash, err := execution.Object(b)
	var pattern string
	if err != nil || len(fields) != 1 || json.Unmarshal(fields["pattern_id"], &pattern) != nil || pattern != deployment.PatternID {
		a.problem(w, r, 400, "invalid_request", "Only the configured pattern_id is accepted; target and Terraform are server-owned.")
		return
	}
	receipt, err := a.Deployments.CreateDeployment(r.Context(), principal(r).OwnerKey, r.Header.Get("Idempotency-Key"), hash, target)
	if err != nil {
		a.deploymentError(w, r, err)
		return
	}
	var record deployment.Record
	if json.Unmarshal(receipt, &record) != nil {
		a.deploymentError(w, r, errors.New("invalid receipt"))
		return
	}
	w.Header().Set("Location", a.BaseURL+"/deployments/"+record.ID)
	w.Header().Set("Retry-After", "2")
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(202)
	_, _ = w.Write(receipt)
}
func (a *API) getDeployment(w http.ResponseWriter, r *http.Request) {
	_, ok := a.deploymentAccess(w, r)
	if !ok {
		return
	}
	record, err := a.Deployments.GetDeployment(r.Context(), chi.URLParam(r, "deployment_id"))
	if err == nil && record.Target.Owner != principal(r).OwnerKey {
		err = deployment.ErrNotFound
	}
	if err != nil {
		a.deploymentError(w, r, err)
		return
	}
	a.json(w, 200, record)
}
func (a *API) approveDeployment(w http.ResponseWriter, r *http.Request) {
	target, ok := a.deploymentAccess(w, r)
	if !ok {
		return
	}
	b, ok := a.body(w, r, false)
	if !ok {
		return
	}
	fields, _, err := execution.Object(b)
	var digest string
	if err != nil || len(fields) != 1 || json.Unmarshal(fields["plan_digest"], &digest) != nil || len(digest) != 71 {
		a.problem(w, r, 400, "invalid_request", "The exact reviewed plan_digest is required.")
		return
	}
	id := chi.URLParam(r, "deployment_id")
	if err = a.Deployments.ApproveDeployment(r.Context(), id, principal(r).OwnerKey, digest, target); err != nil {
		a.deploymentError(w, r, err)
		return
	}
	w.Header().Set("Location", a.BaseURL+"/deployments/"+id)
	w.Header().Set("Retry-After", "2")
	a.json(w, 202, map[string]string{"deployment_id": id, "plan_digest": digest, "status": "approved"})
}
func (a *API) deploymentError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, deployment.ErrNotFound):
		a.problem(w, r, 404, "not_found", "Deployment not found.")
	case errors.Is(err, deployment.ErrConflict):
		a.problem(w, r, 409, "deployment_conflict", "Request conflicts with an existing target, request, plan or deployment state.")
	case errors.Is(err, deployment.ErrDenied):
		a.problem(w, r, 403, "policy_denied", "Deployment permission denied.")
	default:
		w.Header().Set("Retry-After", "5")
		a.problem(w, r, 503, "deployment_unavailable", "Deployment storage is unavailable.")
	}
}
