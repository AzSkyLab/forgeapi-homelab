package keyvaultrunner

import (
	"encoding/json"
	"testing"
)

func TestRejectedCreateProof(t *testing.T) {
	message, _ := json.Marshal(map[string]any{"error": map[string]string{"code": "BadRequest", "message": purgeRejection}})
	event := map[string]any{"correlationId": "correlation", "resourceId": "/approved/vault", "operationName": map[string]string{"value": "Microsoft.KeyVault/vaults/write"}, "status": map[string]string{"value": "Failed"}, "properties": map[string]string{"statusCode": "BadRequest", "statusMessage": string(message)}}
	raw, _ := json.Marshal([]any{event})
	if !rejectedPurgeCreate(raw, "/approved/vault", "correlation") {
		t.Fatal("valid specific rejection denied")
	}
	if rejectedPurgeCreate(raw, "/another/vault", "correlation") || rejectedPurgeCreate(raw, "/approved/vault", "other") {
		t.Fatal("unbound rejection accepted")
	}
	for _, status := range []string{"Succeeded", "Accepted", "Started"} {
		event["status"] = map[string]string{"value": status}
		raw, _ = json.Marshal([]any{event})
		if rejectedPurgeCreate(raw, "/approved/vault", "correlation") {
			t.Fatalf("uncertain status accepted: %s", status)
		}
	}
	event["status"] = map[string]string{"value": "Failed"}
	event["properties"] = map[string]string{"statusCode": "BadRequest", "statusMessage": `{"error":{"code":"BadRequest","message":"different failure"}}`}
	raw, _ = json.Marshal([]any{event})
	if rejectedPurgeCreate(raw, "/approved/vault", "correlation") {
		t.Fatal("unrelated failure accepted")
	}
}
