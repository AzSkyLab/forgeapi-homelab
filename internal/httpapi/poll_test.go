package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"forgeapi/internal/execution"
)

type arrivingRepo struct {
	*fixtureRepo
	reads int
}

func (r *arrivingRepo) Get(ctx context.Context, id string) (execution.Record, error) {
	r.reads++
	item, err := r.fixtureRepo.Get(ctx, id)
	if r.reads > 1 {
		item.Events = append([]execution.Event{}, item.Events...)
		item.Event("execution.dispatch_changed", time.Now())
	}
	return item, err
}

func TestLongPollDeliversNewEventAndBoundsWaiters(t *testing.T) {
	for _, busy := range []bool{false, true} {
		a, repo := fixture()
		a.Repo = &arrivingRepo{fixtureRepo: repo}
		path := "/executions/" + repo.record.Execution.ID + "/events"
		tail := a.encode(cursor{Resource: path, Principal: "alice", Offset: len(repo.record.Events), Expires: time.Now().Add(time.Hour).Unix()})
		if busy {
			a.waiters.Store(64)
		}
		began := time.Now()
		w := request(a.Handler(), "GET", path+"?wait_seconds=25&cursor="+tail, "alice", "", nil)
		if busy {
			if w.Code != 429 || a.waiters.Load() != 64 {
				t.Fatal("poll budget not enforced")
			}
			continue
		}
		var result struct {
			Items  []execution.Event
			Cursor string
		}
		if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if w.Code != 200 || len(result.Items) != 1 || result.Cursor == tail || time.Since(began) > 3*time.Second || a.waiters.Load() != 0 {
			t.Fatal("new event did not end bounded wait")
		}
	}
}

func TestPrivateProbeAndPublicSeparation(t *testing.T) {
	a, _ := fixture()
	if w := request(a.Handler(), "GET", "/readyz", "alice", "", nil); w.Code != 404 {
		t.Fatal("admin probe leaked onto public router")
	}
	for _, healthy := range []bool{false, true} {
		h := AdminHandler(func(context.Context) error {
			if healthy {
				return nil
			}
			return context.DeadlineExceeded
		})
		for _, path := range []string{"/livez", "/readyz"} {
			w := httptest.NewRecorder()
			h.ServeHTTP(w, httptest.NewRequest(http.MethodGet, path, nil))
			want := 204
			if path == "/readyz" && !healthy {
				want = 503
			}
			if w.Code != want || w.Body.Len() != 0 {
				t.Fatal("probe status or data leakage")
			}
		}
	}
}
