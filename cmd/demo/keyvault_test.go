package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestKeyVaultWalkthroughReplaysCompletedDeployment(t *testing.T) {
	approvals := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.Method == "GET" && r.URL.Path == "/deployment-patterns":
			fmt.Fprint(w, `{"items":[]}`)
		case r.Method == "POST" && r.URL.Path == "/deployments":
			w.WriteHeader(202)
			fmt.Fprint(w, `{"deployment_id":"dep_existing","state":"accepted"}`)
		case r.Method == "GET" && r.URL.Path == "/deployments/dep_existing":
			fmt.Fprint(w, `{"deployment_id":"dep_existing","state":"succeeded","resource_id":"/approved/vault"}`)
		default:
			approvals++
			w.WriteHeader(500)
		}
	}))
	defer server.Close()
	d := demo{base: server.URL, token: "unit-test-only", client: *server.Client(), ctx: context.Background()}
	if err := d.keyVault(); err != nil || approvals != 0 {
		t.Fatalf("repeat attempted apply or failed: %v", err)
	}
}

func TestRetainedRejectedDeploymentStopsPolling(t *testing.T) {
	calls := 0
	s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"state":"rejected_no_effect"}`)
	}))
	defer s.Close()
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	d := demo{base: s.URL, token: "unit-test-only", client: *s.Client(), ctx: ctx}
	if _, err := d.waitDeployment("retained", "planned"); err == nil || calls != 1 || ctx.Err() != nil {
		t.Fatal("historical rejection did not stop immediately", err)
	}
}
