package execution

import (
	"strings"
	"testing"
)

const valid = `{"application_id":"software-factory","environment":"development","template_id":"pr-validation-v1","template_version":"1.0.0","input_artifact_refs":["source-01"]}`

func TestResolveTemplate(t *testing.T) {
	cases := []struct {
		name, body string
		valid      bool
	}{
		{"five required fields", valid, true},
		{"allowed timeout", strings.TrimSuffix(valid, "}") + `,"timeout_seconds":60}`, true},
		{"timeout above template ceiling", strings.TrimSuffix(valid, "}") + `,"timeout_seconds":1801}`, false},
		{"timeout zero", strings.TrimSuffix(valid, "}") + `,"timeout_seconds":0}`, false},
		{"timeout wrong type", strings.TrimSuffix(valid, "}") + `,"timeout_seconds":"60"}`, false},
		{"unknown field", strings.TrimSuffix(valid, "}") + `,"subscription_id":"not-portable"}`, false},
		{"missing field", `{}`, false},
		{"wrong application", strings.Replace(valid, "software-factory", "other-app", 1), false},
		{"unpublished input", strings.Replace(valid, "source-01", "source-02", 1), false},
		{"duplicate key", strings.TrimSuffix(valid, "}") + `,"application_id":"software-factory"}`, false},
		{"duplicate nested key", strings.TrimSuffix(valid, "}") + `,"environment_variables":{"RUN_MODE":"pull-request","RUN_MODE":"pull-request"}}`, false},
		{"null optional", strings.TrimSuffix(valid, "}") + `,"secret_refs":null}`, false},
		{"mutable image", strings.TrimSuffix(valid, "}") + `,"image":"example.com/image:latest"}`, false},
		{"second JSON value", valid + ` {}`, false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			s, _, err := Resolve([]byte(tc.body), "")
			if (err == nil) != tc.valid {
				t.Fatalf("valid=%v err=%v", tc.valid, err)
			}
			if err == nil && s.Command[2] != "artifact:source-01" {
				t.Fatal("command not bound to input")
			}
		})
	}
}

func TestIdempotencyCanonicalization(t *testing.T) {
	_, a, err := Resolve([]byte(valid), "")
	if err != nil {
		t.Fatal(err)
	}
	reordered := `{ "input_artifact_refs": ["source-01"], "template_version":"1.0.0", "template_id":"pr-validation-v1", "environment":"development", "application_id":"software-factory" }`
	_, b, err := Resolve([]byte(reordered), TemplateID)
	if err != nil {
		t.Fatal(err)
	}
	if a != b {
		t.Fatal("key order or alias changed hash")
	}
	_, c, err := Resolve([]byte(strings.TrimSuffix(valid, "}")+`,"timeout_seconds":1800}`), "")
	if err != nil {
		t.Fatal(err)
	}
	if c == a {
		t.Fatal("omitted and explicit defaults must remain distinct requests")
	}
	if _, _, err := Resolve([]byte(valid), "different-template"); err == nil {
		t.Fatal("alias mismatch accepted")
	}
}

func TestCancellationInput(t *testing.T) {
	_, a, err := CancelInput(nil)
	if err != nil {
		t.Fatal(err)
	}
	_, b, err := CancelInput([]byte("{}"))
	if err != nil || a != b {
		t.Fatal("absent body must equal empty object")
	}
	for _, bad := range []string{`null`, `{"reason":null}`, `{"reason":"anything"}`, `{"reason":"operator_request","extra":1}`} {
		if _, _, err := CancelInput([]byte(bad)); err == nil {
			t.Errorf("accepted %s", bad)
		}
	}
}

func FuzzResolve(f *testing.F) {
	f.Add(valid)
	f.Add(`{"a":null}`)
	f.Fuzz(func(t *testing.T, body string) {
		if len(body) > 65536 {
			return
		}
		_, _, _ = Resolve([]byte(body), "")
	})
}
