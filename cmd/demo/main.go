// The demo is an executable acceptance walkthrough, not a substitute for unit tests.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"forgeapi/internal/execution"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

const input = `{"application_id":"software-factory","environment":"development","template_id":"pr-validation-v1","template_version":"1.0.0","input_artifact_refs":["source-01"]}`

type demo struct {
	base   string
	client http.Client
	ctx    context.Context
}

func main() {
	base := flag.String("url", "http://localhost:8080", "local API URL")
	flag.Parse()
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	d := demo{base: strings.TrimRight(*base, "/"), client: http.Client{Timeout: 5 * time.Second}, ctx: ctx}
	if err := d.run(); err != nil {
		fmt.Fprintln(os.Stderr, "DEMO FAILED:", err)
		os.Exit(1)
	}
}
func (d *demo) request(method, path, principal, key, body string, want int) ([]byte, error) {
	r, err := http.NewRequestWithContext(d.ctx, method, d.base+path, strings.NewReader(body))
	if err != nil {
		return nil, err
	}
	r.Header.Set("X-Demo-Principal", principal)
	if key != "" {
		r.Header.Set("Idempotency-Key", key)
	}
	if body != "" {
		r.Header.Set("Content-Type", "application/json")
	}
	resp, err := d.client.Do(r)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(io.LimitReader(resp.Body, 1024*1024))
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != want {
		return nil, fmt.Errorf("%s %s: wanted %d, got %d: %s", method, path, want, resp.StatusCode, b)
	}
	return b, nil
}
func (d *demo) wait(id, want string) error {
	ticker := time.NewTicker(300 * time.Millisecond)
	defer ticker.Stop()
	last := ""
	for {
		b, err := d.request("GET", "/executions/"+id, "alice", "", "", 200)
		if err != nil {
			return err
		}
		var e execution.Execution
		if err = json.Unmarshal(b, &e); err != nil {
			return err
		}
		if last != e.State {
			fmt.Printf("  %s → %s\n", id, e.State)
			last = e.State
		}
		if e.State == want {
			return nil
		}
		if execution.Terminal(e.State) {
			return fmt.Errorf("wanted %s, got %s", want, e.State)
		}
		select {
		case <-d.ctx.Done():
			return d.ctx.Err()
		case <-ticker.C:
		}
	}
}
func (d *demo) submit(key, body string) (execution.Execution, []byte, error) {
	b, err := d.request("POST", "/executions", "alice", key, body, 202)
	var e execution.Execution
	if err == nil {
		err = json.Unmarshal(b, &e)
	}
	return e, b, err
}
func (d *demo) run() error {
	fmt.Println("ForgeAPI local demo — real API/PostgreSQL/Temporal; simulated compute and fixture identities.")
	if _, err := d.request("GET", "/identity-context", "alice", "", "", 200); err != nil {
		return err
	}
	if _, err := d.request("GET", "/execution-templates", "alice", "", "", 200); err != nil {
		return err
	}
	key := execution.ID("demo-")
	e, accepted, err := d.submit(key, input)
	if err != nil {
		return err
	}
	fmt.Println("1. Accepted durably:", e.ID, "— find this ID in Temporal UI http://localhost:8233")
	replay, err := d.request("POST", "/execution-templates/pr-validation-v1/executions", "alice", key, input, 202)
	if err != nil {
		return err
	}
	if !bytes.Equal(accepted, replay) {
		return errors.New("idempotency replay changed the accepted response")
	}
	changed := strings.TrimSuffix(input, "}") + `,"timeout_seconds":60}`
	if _, err = d.request("POST", "/executions", "alice", key, changed, 409); err != nil {
		return err
	}
	fmt.Println("2. Same key + same request = same execution; changed request = 409.")
	if _, err = d.request("GET", "/executions/"+e.ID, "bob", "", "", 404); err != nil {
		return err
	}
	if _, err = d.request("GET", "/executions/"+e.ID+"/logs", "auditor", "", "", 403); err != nil {
		return err
	}
	fmt.Println("3. Bob cannot read Alice's job; auditor cannot read workload data (fixture policy).")
	if err = d.wait(e.ID, "succeeded"); err != nil {
		return err
	}
	replay, err = d.request("POST", "/executions", "alice", key, input, 202)
	if err != nil {
		return err
	}
	if !bytes.Equal(accepted, replay) {
		return errors.New("terminal replay changed the original response")
	}
	for _, kind := range []string{"events", "logs"} {
		b, err := d.request("GET", "/executions/"+e.ID+"/"+kind, "alice", "", "", 200)
		if err != nil {
			return err
		}
		var page struct {
			Items  []json.RawMessage `json:"items"`
			Cursor string            `json:"cursor"`
		}
		if err := json.Unmarshal(b, &page); err != nil {
			return err
		}
		if len(page.Items) == 0 || page.Cursor == "" {
			return fmt.Errorf("missing %s or resume cursor", kind)
		}
		fmt.Printf("4. Read %d %s with a resume cursor.\n", len(page.Items), kind)
		if kind == "logs" {
			for _, item := range page.Items {
				var line execution.Log
				if err := json.Unmarshal(item, &line); err != nil {
					return err
				}
				fmt.Println("  " + line.Text)
			}
		}
	}
	b, err := d.request("GET", "/executions/"+e.ID+"/results", "alice", "", "", 200)
	if err != nil {
		return err
	}
	var result execution.Result
	if err = json.Unmarshal(b, &result); err != nil {
		return err
	}
	if !result.ResultComplete || len(result.Artifacts) != 1 {
		return errors.New("missing completed result")
	}
	artifact, err := d.request("GET", "/executions/"+e.ID+"/artifacts/"+result.Artifacts[0].ID, "alice", "", "", 200)
	if err != nil {
		return err
	}
	h := sha256.Sum256(artifact)
	if fmt.Sprintf("sha256:%x", h) != result.Artifacts[0].Digest || len(artifact) != result.Artifacts[0].SizeBytes {
		return errors.New("artifact integrity mismatch")
	}
	fmt.Printf("5. Downloaded and verified synthetic artifact: %s", artifact)
	second, _, err := d.submit(execution.ID("demo-"), input)
	if err != nil {
		return err
	}
	if err = d.wait(second.ID, "running"); err != nil {
		return err
	}
	cancelKey := execution.ID("cancel-")
	cancelled, err := d.request("POST", "/executions/"+second.ID+"/cancellations", "alice", cancelKey, `{"reason":"operator_request"}`, 202)
	if err != nil {
		return err
	}
	if err = d.wait(second.ID, "cancelled"); err != nil {
		return err
	}
	again, err := d.request("POST", "/executions/"+second.ID+"/cancellations", "alice", cancelKey, `{"reason":"operator_request"}`, 202)
	if err != nil {
		return err
	}
	if !bytes.Equal(cancelled, again) {
		return errors.New("cancellation replay changed")
	}
	third, _, err := d.submit(execution.ID("demo-"), strings.TrimSuffix(input, "}")+`,"timeout_seconds":1}`)
	if err != nil {
		return err
	}
	if err = d.wait(third.ID, "timed_out"); err != nil {
		return err
	}
	fmt.Println("6. Running-job cancellation and acceptance-relative timeout verified.")
	fmt.Println("PASS: local walkthrough complete. No Azure resources or real workloads were created.")
	return nil
}
