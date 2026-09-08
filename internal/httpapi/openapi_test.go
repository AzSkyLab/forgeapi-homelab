package httpapi

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"forgeapi/internal/execution"
	"github.com/santhosh-tekuri/jsonschema/v6"
	"gopkg.in/yaml.v3"
)

// Validate real handler output, not copied OpenAPI examples. This runs in the
// ordinary Go/race suite and therefore needs neither Python nor a live tenant.
func openAPIResponseValidator(t *testing.T, path string) func(string, string, *httptest.ResponseRecorder) {
	t.Helper()
	b, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var document map[string]any
	if err = yaml.Unmarshal(b, &document); err != nil {
		t.Fatal(err)
	}
	// Normalize YAML numbers/maps through JSON for a JSON Schema compiler.
	b, err = json.Marshal(document)
	if err != nil {
		t.Fatal(err)
	}
	if err = json.Unmarshal(b, &document); err != nil {
		t.Fatal(err)
	}
	resolve := func(value any) map[string]any {
		m := value.(map[string]any)
		for m["$ref"] != nil {
			ref := m["$ref"].(string)
			if !strings.HasPrefix(ref, "#/") {
				t.Fatal("external response reference")
			}
			var node any = document
			for _, part := range strings.Split(ref[2:], "/") {
				node = node.(map[string]any)[strings.ReplaceAll(strings.ReplaceAll(part, "~1", "/"), "~0", "~")]
			}
			m = node.(map[string]any)
		}
		return m
	}
	validate := func(route, method string, w *httptest.ResponseRecorder) {
		t.Helper()
		op := document["paths"].(map[string]any)[route].(map[string]any)[method].(map[string]any)
		response, ok := op["responses"].(map[string]any)[strconv.Itoa(w.Code)]
		if !ok {
			t.Fatalf("undeclared %s %s response %d", method, route, w.Code)
		}
		for _, header := range []string{"X-Request-ID", "X-Flow-ID", "Cache-Control"} {
			if w.Header().Get(header) == "" {
				t.Fatalf("missing %s", header)
			}
		}
		if w.Code == 202 && (w.Header().Get("Location") == "" || w.Header().Get("Retry-After") == "") {
			t.Fatal("incomplete acceptance headers")
		}
		if w.Code == 304 {
			if w.Body.Len() != 0 {
				t.Fatal("304 body")
			}
			return
		}
		content := resolve(response)["content"].(map[string]any)
		media := w.Header().Get("Content-Type")
		item, ok := content[media]
		if !ok {
			t.Fatalf("undeclared media %s on %s", media, route)
		}
		document["validation"] = item.(map[string]any)["schema"]
		compiler := jsonschema.NewCompiler()
		compiler.DefaultDraft(jsonschema.Draft2020)
		compiler.AssertFormat()
		if err := compiler.AddResource("https://contract.invalid/openapi.json", document); err != nil {
			t.Fatal(err)
		}
		schema, err := compiler.Compile("https://contract.invalid/openapi.json#/validation")
		if err != nil {
			t.Fatal(err)
		}
		var value any
		if media == "application/octet-stream" {
			value = w.Body.String()
		} else if err = json.Unmarshal(w.Body.Bytes(), &value); err != nil {
			t.Fatal(err)
		}
		if err = schema.Validate(value); err != nil {
			t.Fatalf("%s %s %d: %v", method, route, w.Code, err)
		}
	}
	return validate
}

func TestOpenAPIResponses(t *testing.T) {
	validate := openAPIResponseValidator(t, "../../docs/design/openapi.yaml")
	a, repo := fixture()
	h := a.Handler()
	id := repo.record.Execution.ID
	for _, route := range []string{"/identity-context", "/execution-templates", "/executions/{execution_id}", "/executions/{execution_id}/events", "/executions/{execution_id}/logs", "/executions/{execution_id}/results"} {
		path := strings.ReplaceAll(route, "{execution_id}", id)
		validate(route, "get", request(h, "GET", path, "alice", "", nil))
		validate(route, "get", request(h, "GET", path, "", "", nil))
	}
	input := `{"application_id":"software-factory","environment":"development","template_id":"pr-validation-v1","template_version":"1.0.0","input_artifact_refs":["source-01"]}`
	headers := map[string]string{"Content-Type": "application/json", "Idempotency-Key": "openapi-test-0001"}
	for _, route := range []string{"/executions", "/execution-templates/{template_id}/executions"} {
		path := strings.ReplaceAll(route, "{template_id}", execution.TemplateID)
		validate(route, "post", request(h, "POST", path, "alice", input, headers))
		validate(route, "post", request(h, "POST", path, "alice", "{}", headers))
	}
	validate("/executions/{execution_id}", "get", request(h, "GET", "/executions/"+id, "alice", "", map[string]string{"If-None-Match": `W/"1"`}))
	validate("/executions/{execution_id}/cancellations", "post", request(h, "POST", "/executions/"+id+"/cancellations", "alice", "", headers))
	repo.record.ArtifactData = []byte("synthetic artifact")
	repo.record.Result.Artifacts = []execution.Artifact{{ID: "artifact-sbom", Name: "sbom", Digest: fmt.Sprintf("sha256:%x", sha256.Sum256(repo.record.ArtifactData)), SizeBytes: len(repo.record.ArtifactData), MediaType: "application/json", DownloadURL: "http://localhost:8080/executions/" + id + "/artifacts/artifact-sbom", ExpiresAt: time.Now().Add(time.Hour)}}
	validate("/executions/{execution_id}/artifacts/{artifact_id}", "get", request(h, "GET", "/executions/"+id+"/artifacts/artifact-sbom", "alice", "", nil))
	validate("/executions/{execution_id}/results", "get", request(h, "GET", "/executions/"+id+"/results", "alice", "", nil))
}
