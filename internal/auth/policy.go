package auth

import (
	"encoding/json"
	"errors"
	"io"
	"os"

	"forgeapi/internal/execution"
)

type Grant struct {
	TenantID       string `json:"tenant_id"`
	ObjectID       string `json:"object_id"`
	PrincipalKind  string `json:"principal_kind"`
	ApplicationID  string `json:"application_id"`
	Environment    string `json:"environment"`
	Classification string `json:"classification"`
	Role           string `json:"role"`
}

type Policy struct {
	Grants []Grant `json:"grants"`
}

func LoadPolicy(path string) (Policy, error) {
	f, err := os.Open(path)
	if err != nil {
		return Policy{}, errors.New("cannot read AUTH_GRANTS_FILE")
	}
	defer f.Close()
	b, err := io.ReadAll(io.LimitReader(f, 65537))
	if err != nil || len(b) > 65536 {
		return Policy{}, errors.New("AUTH_GRANTS_FILE exceeds 64 KiB or cannot be read")
	}
	return ParsePolicy(b)
}

func ParsePolicy(b []byte) (Policy, error) {
	fields, _, err := execution.Object(b)
	if err != nil {
		return Policy{}, errors.New("invalid grants JSON")
	}
	if len(fields) != 1 || fields["grants"] == nil {
		return Policy{}, errors.New("policy requires only a grants array")
	}
	var p Policy
	if json.Unmarshal(b, &p) != nil || p.Grants == nil || len(p.Grants) > 200 {
		return Policy{}, errors.New("policy requires at most 200 grants")
	}
	var raw []map[string]json.RawMessage
	if json.Unmarshal(fields["grants"], &raw) != nil {
		return Policy{}, errors.New("invalid grants array")
	}
	seen := map[string]bool{}
	for i, g := range p.Grants {
		if len(raw[i]) != 7 || !uuid.MatchString(g.TenantID) || !uuid.MatchString(g.ObjectID) || (g.PrincipalKind != "human" && g.PrincipalKind != "application") || g.ApplicationID != execution.Application || g.Environment != execution.Environment || g.Classification != "synthetic" {
			return Policy{}, errors.New("grant must have explicit tenant/object/kind and the synthetic software-factory/development scope")
		}
		if g.Role != "developer" && g.Role != "auditor" && g.Role != "operator" {
			return Policy{}, errors.New("unsupported grant role")
		}
		key := g.TenantID + ":" + g.ObjectID + ":" + g.PrincipalKind
		if seen[key] {
			return Policy{}, errors.New("duplicate principal grant")
		}
		seen[key] = true
	}
	return p, nil
}

func (p Policy) Apply(identity Principal) Principal {
	for _, g := range p.Grants {
		if g.TenantID == identity.Tenant && g.ObjectID == identity.ID && g.PrincipalKind == identity.Kind {
			identity.Role = g.Role
			break
		}
	}
	return identity
}
