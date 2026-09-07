// Package httpapi translates HTTP requests to portable domain/store operations.
package httpapi

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"time"

	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"github.com/go-chi/chi/v5"
)

type Repository interface {
	Get(context.Context, string) (execution.Record, error)
	Submit(context.Context, string, string, string, string, execution.Spec) (store.Accepted, error)
	Cancel(context.Context, string, string, string, string, string) (store.Accepted, error)
}

type API struct {
	Repo      Repository
	BaseURL   string
	CursorKey []byte
}
type identityKey struct{}

var keyPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{16,128}$`)
var flowPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var tracePattern = regexp.MustCompile(`^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$`)

func (a *API) Handler() http.Handler {
	r := chi.NewRouter()
	r.Use(a.localBoundary)
	r.Get("/healthz", func(w http.ResponseWriter, r *http.Request) {
		a.json(w, 200, map[string]string{"status": "ok", "mode": "local-demo"})
	})
	r.Group(func(r chi.Router) {
		r.Use(a.fixtureIdentity)
		r.Get("/identity-context", a.identity)
		r.Get("/execution-templates", a.templates)
		r.Post("/executions", a.submit)
		r.Post("/execution-templates/{template_id}/executions", a.submit)
		r.Get("/executions/{execution_id}", a.status)
		r.Post("/executions/{execution_id}/cancellations", a.cancel)
		r.Get("/executions/{execution_id}/events", a.events)
		r.Get("/executions/{execution_id}/logs", a.logs)
		r.Get("/executions/{execution_id}/results", a.results)
		r.Get("/executions/{execution_id}/artifacts/{artifact_id}", a.artifact)
	})
	r.NotFound(func(w http.ResponseWriter, r *http.Request) { a.problem(w, r, 404, "not_found", "Resource not found.") })
	r.MethodNotAllowed(func(w http.ResponseWriter, r *http.Request) {
		a.problem(w, r, 405, "method_not_allowed", "Method not allowed.")
	})
	return r
}

func (a *API) localBoundary(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestID := execution.ID("req-")
		w.Header().Set("X-Request-ID", requestID)
		flow := r.Header.Get("X-Flow-ID")
		if flow == "" {
			flow = requestID
		}
		w.Header().Set("X-Flow-ID", requestID)
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		if !flowPattern.MatchString(flow) {
			a.problem(w, r, 400, "invalid_request", "Invalid X-Flow-ID.")
			return
		}
		w.Header().Set("X-Flow-ID", flow)
		// This binary is exclusively a trusted-laptop demo. Reject browser-origin calls
		// and unexpected hosts; require a custom fixture header on every API operation.
		host := r.Host
		if h, _, err := net.SplitHostPort(host); err == nil {
			host = h
		}
		if host != "localhost" && host != "127.0.0.1" && host != "::1" && host != "api" {
			a.problem(w, r, 403, "local_only", "Only local demo hosts are allowed.")
			return
		}
		if r.Header.Get("Origin") != "" {
			a.problem(w, r, 403, "local_only", "Browser-origin requests are disabled in the local demo.")
			return
		}
		trace := r.Header.Get("traceparent")
		if trace != "" && (!tracePattern.MatchString(trace) || strings.Contains(trace, "-00000000000000000000000000000000-") || strings.Contains(trace, "-0000000000000000-")) {
			a.problem(w, r, 400, "invalid_request", "Invalid traceparent.")
			return
		}
		if r.Header.Get("tracestate") != "" {
			a.problem(w, r, 400, "unsupported_demo_feature", "tracestate propagation is not implemented in this local slice.")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func (a *API) fixtureIdentity(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		principal := r.Header.Get("X-Demo-Principal")
		if r.Header.Get("Authorization") != "" || (principal != "alice" && principal != "bob" && principal != "auditor") {
			a.problem(w, r, 401, "unauthenticated", "Use X-Demo-Principal: alice, bob, or auditor. Real authentication is not implemented.")
			return
		}
		if accept := r.Header.Get("Accept"); accept != "" && accept != "application/json" && accept != "*/*" {
			a.problem(w, r, 406, "unacceptable_representation", "This demo accepts application/json or */*.")
			return
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), identityKey{}, principal)))
	})
}

func principal(r *http.Request) string { return r.Context().Value(identityKey{}).(string) }

func (a *API) identity(w http.ResponseWriter, r *http.Request) {
	p := principal(r)
	permissions := []string{"execution.submit", "execution.read", "execution.cancel", "execution.data.read", "catalog.read"}
	if p == "auditor" {
		permissions = []string{"execution.read", "audit.read"}
	}
	a.json(w, 200, map[string]any{"principal_id": p, "principal_kind": "human", "authorized_contexts": []any{map[string]any{"application_id": execution.Application, "environment": execution.Environment, "permissions": permissions}}})
}

func (a *API) templates(w http.ResponseWriter, r *http.Request) {
	if principal(r) == "auditor" {
		a.problem(w, r, 403, "policy_denied", "Catalog permission required.")
		return
	}
	if len(r.URL.Query()) != 0 {
		a.problem(w, r, 400, "unsupported_demo_feature", "The local catalog contains one fixture and does not paginate yet.")
		return
	}
	defaults := execution.Defaults()
	b, _ := json.Marshal(defaults)
	var values map[string]any
	_ = json.Unmarshal(b, &values)
	for _, k := range []string{"application_id", "environment", "template_id", "template_version", "input_artifact_refs", "command"} {
		delete(values, k)
	}
	properties := map[string]any{}
	raw, _ := json.Marshal(defaults)
	var fixed map[string]any
	_ = json.Unmarshal(raw, &fixed)
	for k, v := range fixed {
		properties[k] = map[string]any{"const": v}
	}
	properties["timeout_seconds"] = map[string]any{"type": "integer", "minimum": 1, "maximum": 1800}
	a.json(w, 200, map[string]any{"items": []any{map[string]any{"template_id": execution.TemplateID, "version": execution.TemplateVersion, "description": "LOCAL SIMULATION: no image is pulled, no source tests run; publishes a synthetic sbom artifact.", "allowed_overrides": []string{"compute_profile", "timeout_seconds"}, "defaults": values, "revoked": false,
		"input_schema": map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": []string{"application_id", "environment", "template_id", "template_version", "input_artifact_refs"}, "additionalProperties": false, "properties": properties}}}, "page": map[string]any{}})
}

func (a *API) body(w http.ResponseWriter, r *http.Request, optional bool) ([]byte, bool) {
	if !keyPattern.MatchString(r.Header.Get("Idempotency-Key")) {
		a.problem(w, r, 400, "invalid_request", "Idempotency-Key must contain 16–128 letters, digits, underscores, or hyphens.")
		return nil, false
	}
	if r.ContentLength != 0 || !optional {
		media, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
		if err != nil || media != "application/json" {
			a.problem(w, r, 415, "unsupported_media_type", "Content-Type must be application/json.")
			return nil, false
		}
	}
	b, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64*1024))
	if err != nil {
		var limit *http.MaxBytesError
		if errors.As(err, &limit) {
			a.problem(w, r, 413, "payload_too_large", "Maximum request body is 64 KiB.")
		} else {
			a.problem(w, r, 400, "invalid_request", "Unable to read request body.")
		}
		return nil, false
	}
	return b, true
}

func (a *API) submit(w http.ResponseWriter, r *http.Request) {
	if principal(r) == "auditor" {
		a.problem(w, r, 403, "policy_denied", "Submission permission required.")
		return
	}
	b, ok := a.body(w, r, false)
	if !ok {
		return
	}
	spec, hash, err := execution.Resolve(b, chi.URLParam(r, "template_id"))
	if err != nil {
		a.problem(w, r, 400, "invalid_execution", err.Error())
		return
	}
	accepted, err := a.Repo.Submit(r.Context(), principal(r), r.Header.Get("Idempotency-Key"), hash, a.BaseURL, spec)
	if err != nil {
		a.storeError(w, r, err)
		return
	}
	a.accept(w, accepted)
}

func (a *API) authorized(w http.ResponseWriter, r *http.Request, data bool) (execution.Record, bool) {
	item, err := a.Repo.Get(r.Context(), chi.URLParam(r, "execution_id"))
	if err != nil {
		a.storeError(w, r, err)
		return item, false
	}
	p := principal(r)
	if p != "auditor" && item.Owner != p {
		a.storeError(w, r, store.ErrNotFound)
		return item, false
	}
	if data && p == "auditor" {
		a.problem(w, r, 403, "policy_denied", "Execution data permission required.")
		return item, false
	}
	return item, true
}

func (a *API) status(w http.ResponseWriter, r *http.Request) {
	item, ok := a.authorized(w, r, false)
	if !ok {
		return
	}
	etag := fmt.Sprintf(`"%d"`, item.Execution.Revision)
	w.Header().Set("ETag", etag)
	if r.Header.Get("If-None-Match") == etag {
		w.WriteHeader(304)
		return
	}
	a.json(w, 200, item.Execution)
}

func (a *API) cancel(w http.ResponseWriter, r *http.Request) {
	if principal(r) == "auditor" {
		a.problem(w, r, 403, "policy_denied", "Cancellation permission required.")
		return
	}
	if _, ok := a.authorized(w, r, false); !ok {
		return
	}
	b, ok := a.body(w, r, true)
	if !ok {
		return
	}
	reason, hash, err := execution.CancelInput(b)
	if err != nil {
		a.problem(w, r, 400, "invalid_request", err.Error())
		return
	}
	accepted, err := a.Repo.Cancel(r.Context(), principal(r), chi.URLParam(r, "execution_id"), r.Header.Get("Idempotency-Key"), hash, reason)
	if err != nil {
		a.storeError(w, r, err)
		return
	}
	a.accept(w, accepted)
}

type cursor struct {
	Resource  string
	Principal string
	Offset    int
	Expires   int64
}

func (a *API) encode(c cursor) string {
	b, _ := json.Marshal(c)
	m := hmac.New(sha256.New, a.CursorKey)
	m.Write(b)
	return base64.RawURLEncoding.EncodeToString(b) + "." + base64.RawURLEncoding.EncodeToString(m.Sum(nil))
}

func (a *API) pagination(w http.ResponseWriter, r *http.Request, total int) (start, end int, tail string, page map[string]string, ok bool) {
	q := r.URL.Query()
	limit := 50
	for key, values := range q {
		if len(values) != 1 || (key != "limit" && key != "cursor" && key != "wait_seconds") {
			a.problem(w, r, 400, "invalid_request", "Unsupported or repeated query parameter.")
			return
		}
	}
	if q.Has("wait_seconds") && q.Get("wait_seconds") != "0" {
		a.problem(w, r, 400, "unsupported_demo_feature", "Long polling is not implemented yet; poll with the returned cursor.")
		return
	}
	if q.Has("limit") {
		n, err := strconv.Atoi(q.Get("limit"))
		if err != nil || n < 1 || n > 200 {
			a.problem(w, r, 400, "invalid_request", "limit must be 1–200.")
			return
		}
		limit = n
	}
	c := cursor{Resource: r.URL.Path, Principal: principal(r), Expires: time.Now().Add(time.Hour).Unix()}
	if token := q.Get("cursor"); token != "" {
		parts := strings.Split(token, ".")
		valid := len(parts) == 2 && len(token) <= 2048
		if valid {
			b, err := base64.RawURLEncoding.DecodeString(parts[0])
			sig, e2 := base64.RawURLEncoding.DecodeString(parts[1])
			m := hmac.New(sha256.New, a.CursorKey)
			m.Write(b)
			valid = err == nil && e2 == nil && hmac.Equal(sig, m.Sum(nil)) && json.Unmarshal(b, &c) == nil && c.Resource == r.URL.Path && c.Principal == principal(r) && c.Offset >= 0 && c.Offset <= total
		}
		if !valid {
			a.problem(w, r, 400, "invalid_cursor", "Cursor is invalid for this request.")
			return
		}
		if time.Now().Unix() >= c.Expires {
			a.problem(w, r, 410, "cursor_expired", "Cursor has expired.")
			return
		}
	}
	start = c.Offset
	end = min(start+limit, total)
	c.Offset = end
	tail = a.encode(c)
	page = map[string]string{}
	if end < total {
		params := url.Values{"cursor": {tail}, "limit": {strconv.Itoa(limit)}}
		page["next"] = a.BaseURL + r.URL.Path + "?" + params.Encode()
	}
	ok = true
	return
}

func (a *API) events(w http.ResponseWriter, r *http.Request) {
	item, ok := a.authorized(w, r, false)
	if !ok {
		return
	}
	start, end, tail, page, ok := a.pagination(w, r, len(item.Events))
	if !ok {
		return
	}
	a.json(w, 200, map[string]any{"items": item.Events[start:end], "cursor": tail, "page": page})
}
func (a *API) logs(w http.ResponseWriter, r *http.Request) {
	item, ok := a.authorized(w, r, true)
	if !ok {
		return
	}
	start, end, tail, page, ok := a.pagination(w, r, len(item.Logs))
	if !ok {
		return
	}
	a.json(w, 200, map[string]any{"items": item.Logs[start:end], "cursor": tail, "page": page, "truncated": false})
}
func (a *API) results(w http.ResponseWriter, r *http.Request) {
	item, ok := a.authorized(w, r, true)
	if ok {
		a.json(w, 200, item.Result)
	}
}
func (a *API) artifact(w http.ResponseWriter, r *http.Request) {
	item, ok := a.authorized(w, r, true)
	if !ok {
		return
	}
	if len(item.Result.Artifacts) != 1 || chi.URLParam(r, "artifact_id") != item.Result.Artifacts[0].ID {
		a.storeError(w, r, store.ErrNotFound)
		return
	}
	f := item.Result.Artifacts[0]
	if !time.Now().Before(f.ExpiresAt) {
		a.problem(w, r, 410, "artifact_expired", "Artifact has expired.")
		return
	}
	w.Header().Set("Content-Type", f.MediaType)
	w.Header().Set("Content-Disposition", `attachment; filename="simulated-sbom.json"`)
	w.Header().Set("Content-Length", strconv.Itoa(len(item.ArtifactData)))
	w.Header().Set("Accept-Ranges", "none")
	w.WriteHeader(200)
	_, _ = w.Write(item.ArtifactData)
}

func (a *API) accept(w http.ResponseWriter, v store.Accepted) {
	w.Header().Set("Location", v.Location)
	a.json(w, 202, v.Body)
}
func (a *API) json(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func (a *API) problem(w http.ResponseWriter, r *http.Request, status int, code, detail string) {
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"type": "urn:forgeapi:problem:" + code, "title": http.StatusText(status), "status": status, "detail": detail, "instance": "urn:forgeapi:request:" + w.Header().Get("X-Request-ID"), "code": code, "request_id": w.Header().Get("X-Request-ID")})
}
func (a *API) storeError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		a.problem(w, r, 404, "not_found", "Resource not found.")
	case errors.Is(err, store.ErrConflict):
		a.problem(w, r, 409, "idempotency_conflict", "This key was accepted with a different request.")
	case errors.Is(err, store.ErrTerminal):
		a.problem(w, r, 409, "execution_terminal", "The execution is already terminal.")
	default:
		w.Header().Set("Retry-After", "1")
		a.problem(w, r, 503, "service_unavailable", "Local persistence is unavailable. Retry with the same idempotency key.")
	}
}
