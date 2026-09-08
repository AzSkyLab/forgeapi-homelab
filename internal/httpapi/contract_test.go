package httpapi

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"forgeapi/internal/auth"
	"forgeapi/internal/execution"
)

func TestRepresentationContract(t *testing.T) {
	a, repo := fixture()
	path := "/executions/" + repo.record.Execution.ID
	for _, tc := range []struct {
		accept string
		code   int
	}{
		{"application/json, application/problem+json", 200},
		{"text/plain;q=1, application/*;q=0.5", 200},
		{"application/json;q=0, */*;q=1", 406},
		{"application/json;q=invalid", 406},
	} {
		if w := request(a.Handler(), "GET", path, "alice", "", map[string]string{"Accept": tc.accept}); w.Code != tc.code {
			t.Errorf("Accept %q: %d", tc.accept, w.Code)
		}
	}
	for _, value := range []string{`W/"1"`, `"no", W/"1"`, "*"} {
		if w := request(a.Handler(), "GET", path, "alice", "", map[string]string{"If-None-Match": value}); w.Code != 304 {
			t.Errorf("If-None-Match %q: %d", value, w.Code)
		}
	}
	w := request(a.Handler(), "GET", "/identity-context", "alice", "", map[string]string{"X-Flow-ID": "flow/a+b=c"})
	if w.Code != 200 || w.Header().Get("X-Flow-ID") != "flow/a+b=c" {
		t.Fatal("valid flow rejected")
	}
	for _, path := range []string{"/execution-templates?limit=1", "/execution-templates?cursor=", "/execution-templates?limit=%zz", path + "/logs?wait_seconds=0"} {
		w = request(a.Handler(), "GET", path, "alice", "", nil)
		want := 400
		if path == "/execution-templates?limit=1" {
			want = 200
		}
		if w.Code != want {
			t.Errorf("%s: %d", path, w.Code)
		}
	}
	w = request(a.Handler(), "GET", "/missing", "alice", "", nil)
	if !strings.Contains(w.Body.String(), "http://localhost:8080/problems/not_found") {
		t.Fatal("problem type is not same-origin")
	}
}

func TestArtifactIntegrity(t *testing.T) {
	for _, tamper := range []string{"", "digest", "size"} {
		t.Run(tamper, func(t *testing.T) {
			a, repo := fixture()
			repo.record.ArtifactData = []byte(`{"synthetic":true}`)
			f := execution.Artifact{ID: "artifact-sbom", SizeBytes: len(repo.record.ArtifactData), Digest: fmt.Sprintf("sha256:%x", sha256.Sum256(repo.record.ArtifactData)), MediaType: "application/json", ExpiresAt: time.Now().Add(time.Hour)}
			if tamper == "digest" {
				f.Digest = "sha256:bad"
			}
			if tamper == "size" {
				f.SizeBytes++
			}
			repo.record.Result.Artifacts = []execution.Artifact{f}
			w := request(a.Handler(), "GET", "/executions/"+repo.record.Execution.ID+"/artifacts/artifact-sbom", "alice", "", map[string]string{"Accept": "application/octet-stream"})
			if tamper == "" {
				if w.Code != 200 || w.Header().Get("Content-Type") != "application/octet-stream" {
					t.Fatalf("download: %d", w.Code)
				}
			} else if w.Code != 503 || strings.Contains(w.Body.String(), "synthetic") {
				t.Fatalf("corrupt bytes served: %d", w.Code)
			}
		})
	}
}

type changingIdentity struct {
	calls   int
	revoked bool
}

func (a *changingIdentity) Authenticate(r *http.Request) (auth.Principal, error) {
	a.calls++
	if a.revoked && a.calls > 1 {
		return auth.Principal{}, auth.ErrUnauthenticated
	}
	return fixtureIdentity{}.Authenticate(r)
}

func TestEventLongPollTimeoutAndReauthorization(t *testing.T) {
	for _, revoke := range []bool{false, true} {
		a, repo := fixture()
		identity := &changingIdentity{revoked: revoke}
		a.Auth = identity
		path := "/executions/" + repo.record.Execution.ID + "/events"
		tail := a.encode(cursor{Resource: path, Principal: "alice", Offset: len(repo.record.Events), Expires: time.Now().Add(time.Hour).Unix()})
		began := time.Now()
		w := request(a.Handler(), "GET", path+"?cursor="+tail+"&wait_seconds=1", "alice", "", nil)
		if revoke {
			if w.Code != 401 {
				t.Fatalf("revoked long poll: %d", w.Code)
			}
		} else {
			var result struct {
				Items  []execution.Event
				Cursor string
			}
			if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if w.Code != 200 || len(result.Items) != 0 || result.Cursor != tail || time.Since(began) < time.Second {
				t.Fatal("long poll did not preserve empty tail")
			}
		}
		if identity.calls < 2 {
			t.Fatal("no post-wait authentication")
		}
	}
}

func TestEventDisconnectDoesNotCancelExecution(t *testing.T) {
	a, repo := fixture()
	path := "/executions/" + repo.record.Execution.ID + "/events"
	tail := a.encode(cursor{Resource: path, Principal: "alice", Offset: len(repo.record.Events), Expires: time.Now().Add(time.Hour).Unix()})
	r := httptest.NewRequest("GET", "http://localhost:8080"+path+"?cursor="+tail+"&wait_seconds=25", nil)
	r.Header.Set("X-Demo-Principal", "alice")
	ctx, cancel := context.WithCancel(r.Context())
	cancel()
	start := time.Now()
	a.Handler().ServeHTTP(httptest.NewRecorder(), r.WithContext(ctx))
	if time.Since(start) > time.Second || repo.calls != 0 {
		t.Fatal("disconnect changed execution or held request")
	}
}

func TestLogPageByteBounds(t *testing.T) {
	a, repo := fixture()
	for i := range 100 {
		repo.record.Logs = append(repo.record.Logs, execution.Log{Sequence: int64(i + 1), Text: strings.Repeat("\x00", 20000)})
	}
	w := request(a.Handler(), "GET", "/executions/"+repo.record.Execution.ID+"/logs?limit=200", "alice", "", nil)
	var page struct {
		Items     []execution.Log
		Truncated bool
		Page      map[string]string
	}
	if err := json.Unmarshal(w.Body.Bytes(), &page); err != nil {
		t.Fatal(err)
	}
	if w.Code != 200 || w.Body.Len() > 256*1024 || !page.Truncated || len(page.Items) == 0 || page.Page["next"] == "" {
		t.Fatalf("unbounded/incorrect page: %d bytes", w.Body.Len())
	}
	for _, item := range page.Items {
		if len(item.Text) > 16384 {
			t.Fatal("unbounded log line")
		}
	}
}
