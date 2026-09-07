// Package auth separates identity verification from current application grants.
package auth

import (
	"errors"
	"net/http"
	"slices"
	"strings"

	"forgeapi/internal/execution"
)

var ErrUnauthenticated = errors.New("authentication required")
var ErrUnavailable = errors.New("authentication or policy service unavailable")

type Authenticator interface {
	Authenticate(*http.Request) (Principal, error)
}

type Principal struct {
	ID       string
	Kind     string
	Tenant   string
	OwnerKey string
	Role     string
}

func (p Principal) Permissions() []string {
	switch p.Role {
	case "developer":
		return []string{"execution.submit", "execution.read", "execution.cancel", "execution.data.read", "catalog.read"}
	case "auditor":
		return []string{"execution.read", "audit.read"}
	case "operator":
		return []string{"platform.operate"}
	default:
		return []string{}
	}
}

func (p Principal) Can(permission string) bool { return slices.Contains(p.Permissions(), permission) }

// The current catalog contains only synthetic data in one application/environment.
// Broader classification, team, or app-owner grants must not be inferred here.
func (p Principal) CanRead(r execution.Record) bool {
	if !p.Can("execution.read") || r.Execution.ApplicationID != execution.Application || r.Execution.Environment != execution.Environment {
		return false
	}
	if p.Tenant == "fixture" {
		if r.Owner != "alice" && r.Owner != "bob" {
			return false
		}
	} else if !strings.HasPrefix(r.Owner, "entra:"+p.Tenant+":") {
		return false
	}
	return r.Owner == p.OwnerKey || p.Role == "auditor"
}
