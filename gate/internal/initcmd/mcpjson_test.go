package initcmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestWriteMCPJSONCreatesMergesAndIsIdempotent(t *testing.T) {
	p := filepath.Join(t.TempDir(), ".mcp.json")
	wrote, err := WriteMCPJSON(p)
	if err != nil || !wrote {
		t.Fatalf("create: %v %v", wrote, err)
	}
	var doc map[string]map[string]map[string]any
	b, _ := os.ReadFile(p)
	if err := json.Unmarshal(b, &doc); err != nil {
		t.Fatal(err)
	}
	m := doc["mcpServers"]["mergeone"]
	if m["command"] != "mergeone" || len(m["args"].([]any)) != 3 {
		t.Fatalf("entry: %+v", m)
	}
	if wrote, _ := WriteMCPJSON(p); wrote {
		t.Fatal("second write must be a no-op")
	}
	// merge into an existing file with another server
	_ = os.WriteFile(p, []byte(`{"mcpServers": {"other": {"command": "x"}}, "keep": 1}`), 0o644)
	if wrote, err := WriteMCPJSON(p); err != nil || !wrote {
		t.Fatalf("merge: %v %v", wrote, err)
	}
	var any1 map[string]any
	b, _ = os.ReadFile(p)
	_ = json.Unmarshal(b, &any1)
	srv := any1["mcpServers"].(map[string]any)
	if _, ok := srv["other"]; !ok || any1["keep"] != 1.0 || srv["mergeone"] == nil {
		t.Fatalf("merge lost data: %v", any1)
	}
	// malformed file is left alone
	_ = os.WriteFile(p, []byte("{not json"), 0o644)
	if _, err := WriteMCPJSON(p); err == nil {
		t.Fatal("malformed .mcp.json must error, not be overwritten")
	}
}
