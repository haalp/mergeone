package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestStateDir(t *testing.T) {
	t.Setenv("MERGEONE_STATE_DIR", "")
	if got, want := stateDir("/policy/.mergeone.yaml"), filepath.Join("/policy", ".mergeone"); got != want {
		t.Errorf("default: %q, want %q", got, want)
	}
	t.Setenv("MERGEONE_STATE_DIR", "/data/state")
	if got := stateDir("/policy/.mergeone.yaml"); got != "/data/state" {
		t.Errorf("env: %q", got)
	}
}

// A policy whose own directory cannot hold .mergeone/ (a read-only image; here a file is in the way, which also stops
// root) still gets its PR memory, in the state directory.
func TestMemoryOpensInStateDirWhenPolicyDirCannotHoldIt(t *testing.T) {
	dir, state := t.TempDir(), t.TempDir()
	policy := filepath.Join(dir, ".mergeone.yaml")
	src, err := os.ReadFile(filepath.Join("..", "..", "..", "mergeone", "gate", "policy.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(policy, src, 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, ".mergeone"), []byte("in the way"), 0o644); err != nil {
		t.Fatal(err)
	}
	assets := filepath.Join("..", "..", "..", "mergeone", "gate")
	t.Setenv("MERGEONE_STATE_DIR", "")
	if _, err := buildGateOpts(policy, true, assets, true); err == nil {
		t.Fatal("expected the memory directory beside the policy to fail")
	}
	t.Setenv("MERGEONE_STATE_DIR", state)
	if _, err := buildGateOpts(policy, true, assets, true); err != nil {
		t.Fatalf("with a state directory: %v", err)
	}
	if _, err := os.Stat(filepath.Join(state, "memory")); err != nil {
		t.Errorf("memory was not created in the state directory: %v", err)
	}
}
