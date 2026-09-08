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
	"sync/atomic"
	"time"
	"unicode/utf8"

	"forgeapi/internal/auth"
	"forgeapi/internal/deployment"
	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"forgeapi/internal/telemetry"
	"github.com/go-chi/chi/v5"
	oteltrace "go.opentelemetry.io/otel/trace"
)

type Repository interface {
	Get(context.Context, string) (execution.Record, error)
	Submit(context.Context, string, string, string, string, execution.Spec) (store.Accepted, error)
	Cancel(context.Context, string, string, string, string, string) (store.Accepted, error)
	TemplateRevoked(context.Context) (bool, error)
}

type API struct {
	Repo             Repository
	BaseURL          string
	CursorKey        []byte
	Auth             auth.Authenticator
	Deployments      DeploymentRepository
	DeploymentTarget func() (deployment.Target, error)
	waiters          atomic.Int64
}
type identityKey struct{}

var keyPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{16,128}$`)
var flowPattern = regexp.MustCompile(`^[A-Za-z0-9/+_=-]{1,128}$`)
var tracePattern = regexp.MustCompile(`^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$`)

func (a *API) Handler() http.Handler {
	r := chi.NewRouter()
	r.Use(a.localBoundary)
	r.Use(telemetry.HTTP)
	r.Get("/healthz", func(w http.ResponseWriter, r *http.Request) {
		a.json(w, 200, map[string]string{"status": "ok", "mode": "local-demo"})
	})
	r.Group(func(r chi.Router) {
		r.Use(a.authenticate)
		r.Get("/identity-context", a.identity)
		r.Get("/deployment-patterns", a.deploymentPatterns)
		r.Post("/deployments", a.createDeployment)
		r.Get("/deployments/{deployment_id}", a.getDeployment)
		r.Post("/deployments/{deployment_id}/approvals", a.approveDeployment)
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
		if len(r.Header.Values("X-Flow-ID")) > 1 || len(r.Header.Values("traceparent")) > 1 {
			a.problem(w, r, 400, "invalid_request", "Repeated correlation header.")
			return
		}
		w.Header().Set("X-Flow-ID", flow)
		// This binary is exclusively a loopback local environment. Protected routes
		// additionally require Entra; host checks do not replace authentication.
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
		state := strings.Join(r.Header.Values("tracestate"), ",")
		if _, err := oteltrace.ParseTraceState(state); err != nil || len(state) > 512 || (state != "" && trace == "") {
			a.problem(w, r, 400, "invalid_request", "Invalid tracestate.")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 35*time.Second)
		defer cancel()
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func (a *API) authenticate(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if a.Auth == nil {
			w.Header().Set("Retry-After", "15")
			a.problem(w, r, 503, "authentication_unavailable", "Authentication is not configured.")
			return
		}
		identity, err := a.Auth.Authenticate(r)
		if err != nil {
			if errors.Is(err, auth.ErrUnavailable) {
				w.Header().Set("Retry-After", "15")
				a.problem(w, r, 503, "authentication_unavailable", "Authentication or current grant policy is unavailable.")
			} else {
				w.Header().Set("WWW-Authenticate", `Bearer realm="forgeapi"`)
				a.problem(w, r, 401, "unauthenticated", "Authentication failed. Supply credentials for the configured AUTH_MODE.")
			}
			return
		}
		media := "application/json"
		if strings.Contains(r.URL.Path, "/artifacts/") {
			media = "application/octet-stream"
		}
		if !accepts(strings.Join(r.Header.Values("Accept"), ","), media) {
			a.problem(w, r, 406, "unacceptable_representation", "No acceptable representation is available.")
			return
		}
		ctx := context.WithValue(r.Context(), identityKey{}, identity)
		ctx = execution.WithCorrelation(ctx, execution.Correlation{RequestID: w.Header().Get("X-Request-ID"), FlowID: w.Header().Get("X-Flow-ID"), TraceParent: r.Header.Get("traceparent"), PrincipalKind: identity.Kind})
		next.ServeHTTP(w, r.WithContext(ctx))
	})
}

func principal(r *http.Request) auth.Principal {
	return r.Context().Value(identityKey{}).(auth.Principal)
}

func (a *API) identity(w http.ResponseWriter, r *http.Request) {
	p := principal(r)
	contexts := []any{}
	if permissions := p.Permissions(); len(permissions) > 0 {
		contexts = append(contexts, map[string]any{"application_id": execution.Application, "environment": execution.Environment, "permissions": permissions})
	}
	a.json(w, 200, map[string]any{"principal_id": p.ID, "principal_kind": p.Kind, "authorized_contexts": contexts})
}

func (a *API) templates(w http.ResponseWriter, r *http.Request) {
	if !principal(r).Can("catalog.read") {
		a.problem(w, r, 403, "policy_denied", "Catalog permission required.")
		return
	}
	start, end, _, page, ok := a.pagination(w, r, 1)
	if !ok {
		return
	}
	revoked, err := a.Repo.TemplateRevoked(r.Context())
	if err != nil {
		a.storeError(w, r, err)
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
	items := []any{map[string]any{"template_id": execution.TemplateID, "version": execution.TemplateVersion, "description": "LOCAL SIMULATION: no image is pulled, no source tests run; publishes a synthetic sbom artifact.", "allowed_overrides": []string{"compute_profile", "timeout_seconds"}, "defaults": values, "revoked": revoked,
		"input_schema": map[string]any{"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": []string{"application_id", "environment", "template_id", "template_version", "input_artifact_refs"}, "additionalProperties": false, "properties": properties}}}
	a.json(w, 200, map[string]any{"items": items[start:end], "page": page})
}

func (a *API) body(w http.ResponseWriter, r *http.Request, optional bool) ([]byte, bool) {
	if len(r.Header.Values("Idempotency-Key")) != 1 || !keyPattern.MatchString(r.Header.Get("Idempotency-Key")) {
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
	if !principal(r).Can("execution.submit") {
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
	accepted, err := a.Repo.Submit(r.Context(), principal(r).OwnerKey, r.Header.Get("Idempotency-Key"), hash, a.BaseURL, spec)
	if err != nil {
		a.storeError(w, r, err)
		return
	}
	a.accept(w, accepted)
}

func (a *API) authorized(w http.ResponseWriter, r *http.Request, data bool) (execution.Record, bool) {
	if !principal(r).Can("execution.read") {
		a.problem(w, r, 403, "policy_denied", "Execution read permission required.")
		return execution.Record{}, false
	}
	item, err := a.Repo.Get(r.Context(), chi.URLParam(r, "execution_id"))
	if err != nil {
		a.storeError(w, r, err)
		return item, false
	}
	p := principal(r)
	if !p.CanRead(item) {
		a.storeError(w, r, store.ErrNotFound)
		return item, false
	}
	if data && !p.Can("execution.data.read") {
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
	if matchesETag(strings.Join(r.Header.Values("If-None-Match"), ","), etag) {
		w.WriteHeader(304)
		return
	}
	a.json(w, 200, item.Execution)
}

func (a *API) cancel(w http.ResponseWriter, r *http.Request) {
	if !principal(r).Can("execution.cancel") {
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
	accepted, err := a.Repo.Cancel(r.Context(), principal(r).OwnerKey, chi.URLParam(r, "execution_id"), r.Header.Get("Idempotency-Key"), hash, reason)
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
	q, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		a.problem(w, r, 400, "invalid_request", "Malformed query.")
		return
	}
	limit := 50
	for key, values := range q {
		if len(values) != 1 || values[0] == "" || (key != "limit" && key != "cursor" && !(key == "wait_seconds" && strings.HasSuffix(r.URL.Path, "/events"))) {
			a.problem(w, r, 400, "invalid_request", "Unsupported or repeated query parameter.")
			return
		}
	}
	if q.Has("wait_seconds") {
		wait, err := strconv.Atoi(q.Get("wait_seconds"))
		if err != nil || wait < 0 || wait > 25 {
			a.problem(w, r, 400, "invalid_request", "wait_seconds must be 0–25.")
			return
		}
	}
	if q.Has("limit") {
		n, err := strconv.Atoi(q.Get("limit"))
		if err != nil || n < 1 || n > 200 {
			a.problem(w, r, 400, "invalid_request", "limit must be 1–200.")
			return
		}
		limit = n
	}
	c := cursor{Resource: r.URL.Path, Principal: principal(r).OwnerKey, Expires: time.Now().Add(time.Hour).Unix()}
	if token := q.Get("cursor"); token != "" {
		parts := strings.Split(token, ".")
		valid := len(parts) == 2 && len(token) <= 2048
		if valid {
			b, err := base64.RawURLEncoding.DecodeString(parts[0])
			sig, e2 := base64.RawURLEncoding.DecodeString(parts[1])
			m := hmac.New(sha256.New, a.CursorKey)
			m.Write(b)
			valid = err == nil && e2 == nil && hmac.Equal(sig, m.Sum(nil)) && json.Unmarshal(b, &c) == nil && c.Resource == r.URL.Path && c.Principal == principal(r).OwnerKey && c.Offset >= 0 && c.Offset <= total
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
	wait, _ := strconv.Atoi(r.URL.Query().Get("wait_seconds"))
	if wait > 0 && start == end {
		if a.waiters.Add(1) > 64 {
			a.waiters.Add(-1)
			w.Header().Set("Retry-After", "1")
			a.problem(w, r, 429, "rate_limited", "Local long-poll capacity is full.")
			return
		}
		defer a.waiters.Add(-1)
		timer := time.NewTimer(time.Duration(wait) * time.Second)
		defer timer.Stop()
		ticker := time.NewTicker(250 * time.Millisecond)
		defer ticker.Stop()
		for {
			expired := false
			select {
			case <-r.Context().Done():
				return
			case <-timer.C:
				expired = true
			case <-ticker.C:
			}
			// Re-run token and current-grant validation, including on an empty
			// timeout. A connection is never a lease on previously granted access.
			var checked bool
			a.authenticate(http.HandlerFunc(func(w http.ResponseWriter, refreshed *http.Request) {
				r = refreshed
				item, checked = a.authorized(w, r, false)
			})).ServeHTTP(w, r)
			if !checked {
				return
			}
			if expired || len(item.Events) > start {
				break
			}
		}
		start, end, tail, page, ok = a.pagination(w, r, len(item.Events))
		if !ok {
			return
		}
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
	logs := make([]execution.Log, 0, end-start)
	truncated, size := false, 0
	for _, line := range item.Logs[start:end] {
		if len(line.Text) > 16384 {
			line.Text = line.Text[:16384]
			for !utf8.ValidString(line.Text) {
				line.Text = line.Text[:len(line.Text)-1]
			}
			truncated = true
		}
		b, _ := json.Marshal(line)
		// Reserve 8 KiB for the envelope, signed cursor and continuation URL.
		if size+len(b)+1 > 248*1024 {
			break
		}
		logs = append(logs, line)
		size += len(b) + 1
	}
	if start+len(logs) < end {
		q := r.URL.Query()
		q.Set("limit", strconv.Itoa(len(logs)))
		copyRequest := r.Clone(r.Context())
		copyURL := *r.URL
		copyURL.RawQuery = q.Encode()
		copyRequest.URL = &copyURL
		_, _, tail, page, ok = a.pagination(w, copyRequest, len(item.Logs))
		if !ok {
			return
		}
	}
	a.json(w, 200, map[string]any{"items": logs, "cursor": tail, "page": page, "truncated": truncated})
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
	if len(item.ArtifactData) != f.SizeBytes || fmt.Sprintf("sha256:%x", sha256.Sum256(item.ArtifactData)) != f.Digest {
		w.Header().Set("Retry-After", "1")
		a.problem(w, r, 503, "artifact_integrity_unavailable", "Artifact integrity could not be verified.")
		return
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Disposition", `attachment; filename="simulated-sbom.json"`)
	w.Header().Set("Content-Length", strconv.Itoa(len(item.ArtifactData)))
	w.Header().Set("Accept-Ranges", "none")
	w.WriteHeader(200)
	_, _ = w.Write(item.ArtifactData)
}

func (a *API) accept(w http.ResponseWriter, v store.Accepted) {
	w.Header().Set("Location", v.Location)
	w.Header().Set("Retry-After", "1")
	var accepted struct {
		Revision int64 `json:"revision"`
	}
	if json.Unmarshal(v.Body, &accepted) == nil && accepted.Revision > 0 {
		w.Header().Set("ETag", fmt.Sprintf(`"%d"`, accepted.Revision))
	}
	a.json(w, 202, v.Body)
}
func (a *API) json(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func (a *API) problem(w http.ResponseWriter, r *http.Request, status int, code, detail string) {
	oteltrace.SpanFromContext(r.Context()).AddEvent("request." + code)
	w.Header().Set("Content-Type", "application/problem+json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"type": strings.TrimRight(a.BaseURL, "/") + "/problems/" + code, "title": http.StatusText(status), "status": status, "detail": detail, "instance": "urn:forgeapi:request:" + w.Header().Get("X-Request-ID"), "code": code, "request_id": w.Header().Get("X-Request-ID")})
}
func (a *API) storeError(w http.ResponseWriter, r *http.Request, err error) {
	switch {
	case errors.Is(err, store.ErrCallerCapacity):
		w.Header().Set("Retry-After", "5")
		a.problem(w, r, 429, "admission_limited", "Caller active-execution limit reached. Retry with the same key.")
	case errors.Is(err, store.ErrServiceCapacity):
		w.Header().Set("Retry-After", "5")
		a.problem(w, r, 503, "admission_unavailable", "Local execution or dispatch capacity is full. Retry with the same key.")
	case errors.Is(err, store.ErrTemplateRevoked):
		a.problem(w, r, 403, "template_revoked", "This template version is revoked.")
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
