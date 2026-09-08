package httpapi

import (
	"context"
	"encoding/json"
	"forgeapi/internal/auth"
	"forgeapi/internal/execution"
	"forgeapi/internal/store"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type fixtureRepo struct {
	record execution.Record
	err    error
	calls  int
	owner  string
}

func (f *fixtureRepo) TemplateRevoked(context.Context) (bool, error) { return false, f.err }

func (f *fixtureRepo) Get(_ context.Context, id string) (execution.Record, error) {
	if f.err != nil {
		return execution.Record{}, f.err
	}
	if id != f.record.Execution.ID {
		return execution.Record{}, store.ErrNotFound
	}
	return f.record, nil
}
func (f *fixtureRepo) Submit(_ context.Context, owner, _, _, _ string, _ execution.Spec) (store.Accepted, error) {
	f.calls++
	f.owner = owner
	b, _ := json.Marshal(f.record.Execution)
	return store.Accepted{Body: b, Location: f.record.Execution.Links["self"]}, f.err
}
func (f *fixtureRepo) Cancel(_ context.Context, _, _, _, _, _ string) (store.Accepted, error) {
	f.calls++
	now := time.Now().UTC()
	b, _ := json.Marshal(execution.Cancellation{ID: "cancel-test", ExecutionID: f.record.Execution.ID, Status: "requested", RequestedAt: now, DeadlineAt: now.Add(time.Minute)})
	return store.Accepted{Body: b, Location: f.record.Execution.Links["self"]}, f.err
}
func fixture() (*API, *fixtureRepo) {
	r := execution.NewRecord("alice", "http://localhost:8080", execution.Defaults(), time.Now().UTC())
	repo := &fixtureRepo{record: r}
	return &API{Repo: repo, BaseURL: "http://localhost:8080", CursorKey: []byte("unit-test-key"), Auth: fixtureIdentity{}}, repo
}

// Selectable identities exist only in this test binary, never the running API.
type fixtureIdentity struct{}

func (fixtureIdentity) Authenticate(r *http.Request) (auth.Principal, error) {
	if len(r.Header.Values("Authorization")) != 0 || len(r.Header.Values("X-Demo-Principal")) != 1 {
		return auth.Principal{}, auth.ErrUnauthenticated
	}
	id := r.Header.Get("X-Demo-Principal")
	role := "developer"
	switch id {
	case "alice", "bob":
	case "auditor":
		role = "auditor"
	default:
		return auth.Principal{}, auth.ErrUnauthenticated
	}
	return auth.Principal{ID: id, Kind: "human", Tenant: "fixture", OwnerKey: id, Role: role}, nil
}
func request(h http.Handler, method, path, p, body string, headers map[string]string) *httptest.ResponseRecorder {
	r := httptest.NewRequest(method, "http://localhost:8080"+path, strings.NewReader(body))
	r.Header.Set("X-Demo-Principal", p)
	for k, v := range headers {
		r.Header.Set(k, v)
	}
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	return w
}

func TestObjectAuthorization(t *testing.T) {
	a, repo := fixture()
	id := repo.record.Execution.ID
	for _, tc := range []struct {
		name, who, path string
		status          int
	}{{"owner metadata", "alice", "", 200}, {"other user metadata", "bob", "", 404}, {"auditor metadata", "auditor", "", 200}, {"auditor logs", "auditor", "/logs", 403}, {"auditor results", "auditor", "/results", 403}, {"auditor artifact", "auditor", "/artifacts/artifact-sbom", 403}, {"missing identity", "", "", 401}, {"unknown identity", "admin", "", 401}} {
		t.Run(tc.name, func(t *testing.T) {
			w := request(a.Handler(), "GET", "/executions/"+id+tc.path, tc.who, "", nil)
			if w.Code != tc.status {
				t.Fatalf("got %d: %s", w.Code, w.Body)
			}
			for _, key := range []string{"X-Request-ID", "X-Flow-ID", "Cache-Control"} {
				if w.Header().Get(key) == "" {
					t.Errorf("missing %s", key)
				}
			}
		})
	}
	owner := request(a.Handler(), "GET", "/executions/"+id, "alice", "", nil)
	etag := owner.Header().Get("ETag")
	if w := request(a.Handler(), "GET", "/executions/"+id, "bob", "", map[string]string{"If-None-Match": etag}); w.Code != 404 {
		t.Fatal("ETag bypassed authorization")
	}
	if w := request(a.Handler(), "GET", "/executions/"+id, "alice", "", map[string]string{"If-None-Match": etag}); w.Code != 304 {
		t.Fatal("ETag did not return 304")
	}
}

func TestHTTPInputAndProblemBoundaries(t *testing.T) {
	valid := `{"application_id":"software-factory","environment":"development","template_id":"pr-validation-v1","template_version":"1.0.0","input_artifact_refs":["source-01"]}`
	for _, tc := range []struct {
		name, body, key, content string
		status                   int
	}{{"accepted", valid, "valid-request-key-01", "application/json", 202}, {"missing key", valid, "", "application/json", 400}, {"wrong media type", valid, "valid-request-key-01", "text/plain", 415}, {"unknown input", `{"secret_value":"never echo this"}`, "valid-request-key-01", "application/json", 400}, {"oversized", strings.Repeat("x", 65537), "valid-request-key-01", "application/json", 413}} {
		t.Run(tc.name, func(t *testing.T) {
			a, repo := fixture()
			w := request(a.Handler(), "POST", "/executions", "alice", tc.body, map[string]string{"Idempotency-Key": tc.key, "Content-Type": tc.content})
			if w.Code != tc.status {
				t.Fatalf("got %d: %s", w.Code, w.Body)
			}
			if tc.status != 202 && repo.calls != 0 {
				t.Fatal("invalid input reached persistence")
			}
			if strings.Contains(w.Body.String(), "never echo this") {
				t.Fatal("rejected value leaked")
			}
			if tc.status != 202 && w.Header().Get("Content-Type") != "application/problem+json" {
				t.Fatal("missing problem media type")
			}
		})
	}
}

func TestCursorBindingAndTail(t *testing.T) {
	a, repo := fixture()
	repo.record.Event("execution.dispatch_changed", time.Now())
	path := "/executions/" + repo.record.Execution.ID + "/events"
	w := request(a.Handler(), "GET", path+"?limit=1", "alice", "", nil)
	var first struct {
		Cursor string            `json:"cursor"`
		Page   map[string]string `json:"page"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &first); err != nil {
		t.Fatal(err)
	}
	if first.Page["next"] == "" {
		t.Fatal("missing next page")
	}
	w = request(a.Handler(), "GET", path+"?cursor="+first.Cursor, "alice", "", nil)
	var last struct {
		Cursor string            `json:"cursor"`
		Page   map[string]string `json:"page"`
		Items  []execution.Event `json:"items"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &last)
	if len(last.Items) != 1 || len(last.Page) != 0 {
		t.Fatal("incorrect second page")
	}
	w = request(a.Handler(), "GET", path+"?cursor="+last.Cursor, "alice", "", nil)
	var tail struct {
		Cursor string            `json:"cursor"`
		Items  []execution.Event `json:"items"`
	}
	_ = json.Unmarshal(w.Body.Bytes(), &tail)
	if tail.Cursor != last.Cursor || len(tail.Items) != 0 {
		t.Fatal("empty tail did not preserve cursor")
	}
	for _, tc := range []struct{ path, who string }{{path + "?cursor=" + first.Cursor, "auditor"}, {strings.TrimSuffix(path, "events") + "logs?cursor=" + first.Cursor, "alice"}, {path + "?cursor=invalid", "alice"}, {path + "?limit=0", "alice"}, {path + "?limit=1&limit=2", "alice"}} {
		if got := request(a.Handler(), "GET", tc.path, tc.who, "", nil); got.Code != 400 {
			t.Errorf("accepted invalid cursor/query: %d", got.Code)
		}
	}
}

func TestBrowserAndTokenBoundary(t *testing.T) {
	a, _ := fixture()
	for _, h := range []map[string]string{{"Origin": "https://example.com"}, {"Authorization": "Bearer real-token"}, {"traceparent": "garbage"}, {"X-Flow-ID": "not valid"}} {
		w := request(a.Handler(), "GET", "/identity-context", "alice", "", h)
		if w.Code < 400 {
			t.Fatal("unsafe request accepted")
		}
	}
	r := httptest.NewRequest("GET", "http://untrusted.example/identity-context", nil)
	r.Header.Set("X-Demo-Principal", "alice")
	w := httptest.NewRecorder()
	a.Handler().ServeHTTP(w, r)
	if w.Code != 403 {
		t.Fatal("unexpected Host accepted")
	}
}

func TestStoreErrorsAreSanitized(t *testing.T) {
	a, repo := fixture()
	repo.err = context.DeadlineExceeded
	w := request(a.Handler(), "GET", "/executions/"+repo.record.Execution.ID, "alice", "", nil)
	if w.Code != 503 || strings.Contains(w.Body.String(), repo.err.Error()) {
		t.Fatal("unsafe infrastructure error")
	}
}
