package deployment

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestExecutorRequiredForNewTargetButLegacyRecordReadable(t *testing.T) {
	target := testTarget()
	if err := target.Executor.Validate(); err != nil {
		t.Fatal(err)
	}
	for _, change := range []func(*Executor){
		func(e *Executor) { *e = Executor{} },
		func(e *Executor) { e.Mode = "azure_cli" },
		func(e *Executor) { e.Mode = "client_secret" },
		func(e *Executor) { e.ClientID = "bad" },
		func(e *Executor) { e.PrincipalID = "bad" },
		func(e *Executor) { e.CertificateSHA256 = "bad" },
	} {
		bad := target
		change(&bad.Executor)
		if bad.Validate() == nil {
			t.Fatal("invalid executor admitted")
		}
	}
	target.Executor.PrincipalID = strings.Split(target.Owner, ":")[2]
	if target.Validate() == nil {
		t.Fatal("caller reused as executor")
	}
	target = testTarget()
	target.Executor = Executor{}
	b, err := json.Marshal(Record{ID: "historical", Target: target, State: "succeeded"})
	if err != nil || strings.Contains(string(b), `"executor"`) {
		t.Fatal("legacy JSON changed", err)
	}
	var old Record
	if json.Unmarshal(b, &old) != nil || old.State != "succeeded" || old.Target.Executor != (Executor{}) {
		t.Fatal("legacy record unreadable")
	}
}

func TestPlanCannotSwitchExecutorClient(t *testing.T) {
	target := testTarget()
	b := testPlan(target)
	target.Executor.ClientID = "66666666-6666-6666-6666-666666666666"
	if ValidatePlan(b, target) == nil {
		t.Fatal("saved plan client differs from accepted executor")
	}
}
