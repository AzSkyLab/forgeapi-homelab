package telemetry

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"forgeapi/internal/execution"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/propagation"
	collector "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

func TestRealOTLPWireExportAndRedaction(t *testing.T) {
	received := make(chan []byte, 2)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/traces" {
			t.Errorf("unexpected OTLP path %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "" || r.Header.Get("baggage") != "" {
			t.Error("credential/baggage reached collector")
		}
		b, _ := io.ReadAll(r.Body)
		received <- b
		w.Header().Set("Content-Type", "application/x-protobuf")
		w.WriteHeader(200)
	}))
	defer server.Close()
	t.Setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=Bearer-env-canary")
	old := otel.GetTracerProvider()
	defer otel.SetTracerProvider(old)
	shutdown, err := Start(context.Background(), server.URL+"/v1/traces", "forgeapi-test", false)
	if err != nil {
		t.Fatal(err)
	}
	ctx := propagation.Baggage{}.Extract(context.Background(), propagation.MapCarrier{"baggage": "secret=body-canary"})
	r := execution.NewRecord("identity-canary", "http://localhost:8080", execution.Defaults(), time.Now())
	r.Correlation = execution.Correlation{FlowID: "flow-proof", TraceParent: "00-11111111111111111111111111111111-2222222222222222-01"}
	_, span := Span(ctx, r, "execution.cleanup")
	span.End()
	if err = shutdown(context.Background()); err != nil {
		t.Fatal(err)
	}
	select {
	case b := <-received:
		var exported collector.ExportTraceServiceRequest
		if err = proto.Unmarshal(b, &exported); err != nil {
			t.Fatal("invalid OTLP protobuf", err)
		}
		text := exported.String()
		for _, canary := range []string{"Bearer-env-canary", "body-canary", "identity-canary"} {
			if strings.Contains(text, canary) {
				t.Fatal("sensitive field exported")
			}
		}
		if !strings.Contains(text, r.Execution.ID) || !strings.Contains(text, "execution.cleanup") {
			t.Fatal("missing correlated stage")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("no actual OTLP export")
	}
}

func TestRemoteTelemetryDenied(t *testing.T) {
	for _, endpoint := range []string{"https://remote.example/v1/traces", "http://localhost:4318/v1/traces?token=x", "http://user:secret@localhost:4318/v1/traces", "http://collector:4318/v1/traces"} {
		if _, err := Start(context.Background(), endpoint, "test", false); err == nil {
			t.Fatalf("unsafe exporter allowed: %s", endpoint)
		}
	}
}

func TestCollectorCannotRedirectTraces(t *testing.T) {
	var calls atomic.Int64
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) { calls.Add(1) }))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	old := otel.GetTracerProvider()
	defer otel.SetTracerProvider(old)
	shutdown, err := Start(context.Background(), redirect.URL+"/v1/traces", "test", false)
	if err != nil {
		t.Fatal(err)
	}
	_, span := otel.Tracer("test").Start(context.Background(), "safe-stage")
	span.End()
	_ = shutdown(context.Background())
	if calls.Load() != 0 {
		t.Fatal("collector redirect exported outside configured endpoint")
	}
}
