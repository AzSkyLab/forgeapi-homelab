package auth

import (
	"context"
	"strings"

	"forgeapi/internal/execution"
)

// DispatchAuthorizer re-reads the current local grant file before a new workflow
// is started. Persisted acceptance is evidence, not a perpetual authorization.
func DispatchAuthorizer(path, tenant string) func(context.Context, execution.Record) (bool, error) {
	return func(_ context.Context, r execution.Record) (bool, error) {
		policy, err := LoadPolicy(path)
		if err != nil {
			return false, ErrUnavailable
		}
		parts := strings.Split(r.Owner, ":")
		if len(parts) != 3 || parts[0] != "entra" || parts[1] != tenant {
			return false, nil
		}
		for _, grant := range policy.Grants {
			// Legacy local records predate PrincipalKind. Entra object IDs are
			// tenant-scoped identities; a grant still must match that exact ID.
			if grant.TenantID == tenant && grant.ObjectID == parts[2] && grant.Role == "developer" && (r.Correlation.PrincipalKind == "" || grant.PrincipalKind == r.Correlation.PrincipalKind) {
				return true, nil
			}
		}
		return false, nil
	}
}
