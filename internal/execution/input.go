package execution

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"
)

// Object rejects duplicate keys at every depth and preserves omitted fields.
// Hashing caller input (not expanded defaults) keeps idempotency semantics stable.
func Object(body []byte) (map[string]json.RawMessage, string, error) {
	d := json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	var value func() error
	value = func() error {
		t, err := d.Token()
		if err != nil {
			return err
		}
		switch t {
		case json.Delim('{'):
			seen := map[string]bool{}
			for d.More() {
				k, err := d.Token()
				if err != nil {
					return err
				}
				key, ok := k.(string)
				if !ok || seen[key] {
					return errors.New("duplicate or invalid JSON key")
				}
				seen[key] = true
				if err := value(); err != nil {
					return err
				}
			}
			_, err = d.Token()
			return err
		case json.Delim('['):
			for d.More() {
				if err := value(); err != nil {
					return err
				}
			}
			_, err = d.Token()
			return err
		}
		return nil
	}
	if err := value(); err != nil {
		return nil, "", errors.New("body must be valid JSON without duplicate keys")
	}
	if _, err := d.Token(); err != io.EOF {
		return nil, "", errors.New("body must contain one JSON object")
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(body, &fields); err != nil || fields == nil {
		return nil, "", errors.New("body must be a JSON object")
	}
	// Canonicalize recursively, retaining JSON numbers and array order.
	var canonical any
	d = json.NewDecoder(bytes.NewReader(body))
	d.UseNumber()
	if err := d.Decode(&canonical); err != nil {
		return nil, "", err
	}
	encoded, err := json.Marshal(canonical)
	if err != nil {
		return nil, "", err
	}
	hash := sha256.Sum256(encoded)
	return fields, hex.EncodeToString(hash[:]), nil
}

func Defaults() Spec {
	return Spec{ApplicationID: Application, Environment: Environment, TemplateID: TemplateID, TemplateVersion: TemplateVersion,
		InputArtifactRefs: []string{"source-01"}, Image: "registry.example.com/software-factory/reviewer@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
		Command: []string{"review", "--source-ref", "artifact:source-01"}, ComputeProfile: "general-medium", ExecutionClass: "isolated-vm",
		PlacementPolicy: "us-data-residency", NetworkProfile: "software-factory-restricted", TimeoutSeconds: 1800,
		EnvironmentVariables: map[string]string{"RUN_MODE": "pull-request"}, SecretRefs: []string{}}
}

func Resolve(body []byte, templatePath string) (Spec, string, error) {
	fields, hash, err := Object(body)
	if err != nil {
		return Spec{}, "", err
	}
	for _, key := range []string{"application_id", "environment", "template_id", "template_version", "input_artifact_refs"} {
		if _, ok := fields[key]; !ok {
			return Spec{}, "", fmt.Errorf("%s is required", key)
		}
	}
	defaults := Defaults()
	raw, _ := json.Marshal(defaults)
	var allowed map[string]json.RawMessage
	_ = json.Unmarshal(raw, &allowed)
	for key, val := range fields {
		fixed, ok := allowed[key]
		if !ok {
			return Spec{}, "", errors.New("unknown input field")
		}
		if bytes.Equal(bytes.TrimSpace(val), []byte("null")) {
			return Spec{}, "", errors.New("null input values are not allowed")
		}
		if key == "timeout_seconds" {
			continue
		}
		var want, got any
		_ = json.Unmarshal(fixed, &want)
		_ = json.Unmarshal(val, &got)
		if !reflect.DeepEqual(want, got) {
			return Spec{}, "", fmt.Errorf("%s is not allowed by the local fixture template", key)
		}
	}
	resolved := defaults
	if err := json.Unmarshal(body, &resolved); err != nil {
		return Spec{}, "", errors.New("invalid input field type")
	}
	if resolved.TimeoutSeconds < 1 || resolved.TimeoutSeconds > 1800 {
		return Spec{}, "", errors.New("timeout_seconds must be from 1 through 1800")
	}
	if templatePath != "" && templatePath != resolved.TemplateID {
		return Spec{}, "", errors.New("template_id must match the path")
	}
	return resolved, hash, nil
}

func CancelInput(body []byte) (reason, hash string, err error) {
	if len(bytes.TrimSpace(body)) == 0 {
		body = []byte("{}")
	}
	fields, hash, err := Object(body)
	if err != nil {
		return "", "", err
	}
	for key := range fields {
		if key != "reason" {
			return "", "", errors.New("unknown cancellation field")
		}
	}
	if v, ok := fields["reason"]; ok {
		if json.Unmarshal(v, &reason) != nil {
			return "", "", errors.New("invalid cancellation reason")
		}
		switch reason {
		case "no_longer_needed", "superseded_by_new_request", "operator_request":
		default:
			return "", "", errors.New("invalid cancellation reason")
		}
	}
	return reason, hash, nil
}
