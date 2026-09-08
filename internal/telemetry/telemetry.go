// Package telemetry exports only explicit synthetic-operation metadata. It never
// records URLs, query strings, bodies, token headers, baggage or raw errors.
package telemetry

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"net/url"
	"time"

	"forgeapi/internal/execution"
	"github.com/go-chi/chi/v5"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	"go.opentelemetry.io/otel/trace"
)

func Start(ctx context.Context, endpoint, service string, container bool) (func(context.Context) error, error) {
	options := []sdktrace.TracerProviderOption{sdktrace.WithResource(resource.NewSchemaless(attribute.String("service.name", service))), sdktrace.WithSampler(sdktrace.AlwaysSample())}
	if endpoint != "" {
		u, err := url.Parse(endpoint)
		if err != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Hostname() != "localhost" && u.Hostname() != "127.0.0.1" && u.Hostname() != "::1" && !(container && u.Hostname() == "collector")) {
			return nil, errors.New("FORGE_OTLP_ENDPOINT must target the local collector without credentials")
		}
		transport := http.DefaultTransport.(*http.Transport).Clone()
		transport.Proxy = nil
		localClient := &http.Client{Transport: transport, Timeout: 2 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
		exporter, err := otlptracehttp.New(ctx, otlptracehttp.WithEndpointURL(endpoint), otlptracehttp.WithHTTPClient(localClient), otlptracehttp.WithHeaders(map[string]string{}), otlptracehttp.WithTimeout(2*time.Second), otlptracehttp.WithRetry(otlptracehttp.RetryConfig{Enabled: false}))
		if err != nil {
			return nil, errors.New("local telemetry initialization failed")
		}
		options = append(options, sdktrace.WithBatcher(exporter, sdktrace.WithMaxQueueSize(512), sdktrace.WithBatchTimeout(time.Second)))
	}
	provider := sdktrace.NewTracerProvider(options...)
	otel.SetTracerProvider(provider)
	otel.SetErrorHandler(otel.ErrorHandlerFunc(func(error) { slog.Warn("local trace export unavailable") }))
	return provider.Shutdown, nil
}

func Parent(ctx context.Context, parent string) context.Context {
	ctx = propagation.TraceContext{}.Extract(ctx, propagation.MapCarrier{"traceparent": parent})
	// No tracestate vendors are approved in this local environment. Valid
	// incoming state is accepted but not retained/exported. Baggage is ignored.
	sc := trace.SpanContextFromContext(ctx).WithTraceState(trace.TraceState{})
	return trace.ContextWithRemoteSpanContext(ctx, sc)
}

func Span(ctx context.Context, r execution.Record, stage string) (context.Context, trace.Span) {
	ctx = Parent(ctx, r.Correlation.TraceParent)
	return otel.Tracer("forgeapi").Start(ctx, stage, trace.WithAttributes(attribute.String("execution.id", r.Execution.ID), attribute.String("flow.id", r.Correlation.FlowID)))
}

type response struct {
	http.ResponseWriter
	status int
}

func (w *response) WriteHeader(code int) {
	if w.status == 0 {
		w.status = code
	}
	w.ResponseWriter.WriteHeader(code)
}
func (w *response) Write(b []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(200)
	}
	return w.ResponseWriter.Write(b)
}
func (w *response) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func HTTP(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx, span := otel.Tracer("forgeapi").Start(Parent(r.Context(), r.Header.Get("traceparent")), "http.request", trace.WithSpanKind(trace.SpanKindServer))
		defer span.End()
		r = r.Clone(ctx)
		carrier := propagation.MapCarrier{}
		propagation.TraceContext{}.Inject(ctx, carrier)
		r.Header.Set("traceparent", carrier.Get("traceparent"))
		w.Header().Set("traceparent", carrier.Get("traceparent"))
		out := &response{ResponseWriter: w}
		next.ServeHTTP(out, r)
		route := chi.RouteContext(ctx).RoutePattern()
		if route == "" {
			route = "unmatched"
		}
		method := r.Method
		if method != "GET" && method != "POST" {
			method = "OTHER"
		}
		span.SetName(method + " " + route)
		span.SetAttributes(attribute.String("http.route", route), attribute.String("http.request.method", method), attribute.Int("http.response.status_code", out.status), attribute.String("request.id", w.Header().Get("X-Request-ID")), attribute.String("flow.id", w.Header().Get("X-Flow-ID")))
		slog.Info("http request", "route", route, "method", method, "status", out.status, "request_id", w.Header().Get("X-Request-ID"), "trace_id", span.SpanContext().TraceID().String())
	})
}
