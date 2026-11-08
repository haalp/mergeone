package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func capture(t *testing.T, args ...string) (int, string) {
	t.Helper()
	var out bytes.Buffer
	old := stdout
	stdout = &out
	defer func() { stdout = old }()
	code := run(args)
	return code, out.String()
}

func TestPolicyCheckTestScaffold(t *testing.T) {
	dir := t.TempDir()
	pol := filepath.Join(dir, "policy.yaml")
	_ = os.WriteFile(pol, []byte("version: 1\nmodel: mock\nquestion_set: v2\ncalibration: none\nalways_human_paths: [migrations/]\nrules:\n"+
		"  - { name: weak-tests, when: { weakens_tests: \">= 0.6\" }, verdict: block, route: human, reason: b }\n"+
		"  - { name: needs-human, when: { needs_human_review: \">= 0.5\" }, verdict: hold, route: human, reason: h }\n"+
		"default: { verdict: pass, route: auto-merge }\n"), 0o644)

	code, out := capture(t, "policy", "check", "--policy", pol)
	if code != 0 || !strings.Contains(out, "2 rules") || !strings.Contains(out, "weak-tests") || !strings.Contains(out, "mode enforce") {
		t.Fatalf("check: %d\n%s", code, out)
	}

	tests := filepath.Join(dir, "tests.yaml")
	code, out = capture(t, "policy", "scaffold", "--policy", pol, "--tests", tests)
	if code != 0 || !strings.Contains(out, "wrote") {
		t.Fatalf("scaffold: %d\n%s", code, out)
	}
	code, out = capture(t, "policy", "test", "--policy", pol, "--tests", tests)
	if code != 0 || !strings.Contains(out, "4 cases, 0 failed") {
		t.Fatalf("scaffolded tests must pass against their own policy: %d\n%s", code, out)
	}
	if code, _ := capture(t, "policy", "scaffold", "--policy", pol, "--tests", tests); code == 0 {
		t.Fatal("scaffold must not overwrite an existing file")
	}

	// a hand-written file with one wrong expectation
	_ = os.WriteFile(tests, []byte(`cases:
  - name: block wins over hold
    probs: { weakens_tests: 0.9, needs_human_review: 0.9 }
    expect: { verdict: block, rule: weak-tests }
  - name: migration path
    paths: [migrations/0001.sql]
    expect: { verdict: hold, route: human, rule: always_human_paths }
  - name: wrong on purpose
    probs: { needs_human_review: 0.1 }
    expect: { verdict: hold }
`), 0o644)
	code, out = capture(t, "policy", "test", "--policy", pol, "--tests", tests)
	if code != 1 || !strings.Contains(out, "3 cases, 1 failed") || !strings.Contains(out, "FAIL  wrong on purpose") || !strings.Contains(out, "(want hold/*/*)") {
		t.Fatalf("test: %d\n%s", code, out)
	}
	code, out = capture(t, "policy", "test", "--policy", pol, "--tests", tests, "--json")
	if code != 1 || !strings.Contains(out, `"failed": 1`) {
		t.Fatalf("json: %d\n%s", code, out)
	}
}
