package execution

import "context"

// Correlation is allowlisted metadata, never a token, raw body, idempotency key,
// baggage, or authentication error. It stays private in persisted records.
type Correlation struct {
	RequestID     string
	FlowID        string
	TraceParent   string
	PrincipalKind string
}

type correlationKey struct{}

func WithCorrelation(ctx context.Context, c Correlation) context.Context {
	return context.WithValue(ctx, correlationKey{}, c)
}
func CorrelationFrom(ctx context.Context) Correlation {
	c, _ := ctx.Value(correlationKey{}).(Correlation)
	return c
}
