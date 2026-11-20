package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

func TestLogListsFiltersAndNeverPrintsState(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "gatelog.jsonl")
	now := time.Now().UTC()
	recs := []store.Record{
		{TS: now.Add(-72 * time.Hour), Repo: "o/r", PR: 1, HeadSHA: "aaaaaaaaaaaaaaaa", Result: engine.Result{Verdict: "pass", Route: "auto-merge", Rule: "trivial"}, State: "SECRET-STATE"},
		{TS: now.Add(-2 * time.Hour), Repo: "o/r", PR: 2, HeadSHA: "bbbbbbbbbbbbbbbb", Result: engine.Result{Verdict: "hold", Route: "dedup", Rule: "duplicate-of"}, Shadow: true, State: "SECRET-STATE"},
		{TS: now.Add(-1 * time.Hour), Repo: "o/other", PR: 9, HeadSHA: "cccccccccccccccc", Result: engine.Result{Verdict: "block", Route: "human", Rule: "weak-tests"}, ReusedFrom: "dddd"},
		{TS: now.Add(-30 * time.Minute), Repo: "o/r", PR: 2, HeadSHA: "eeeeeeeeeeeeeeee", Result: engine.Result{Verdict: "hold", Route: "human", Error: "model: 503 Service Unavailable"}, DebouncedSHAs: []string{"x", "y"}},
	}
	f, _ := os.Create(path)
	for _, r := range recs {
		b, _ := json.Marshal(r)
		_, _ = f.Write(append(b, '\n'))
	}
	_, _ = f.WriteString("not json\n{\"queue\":true}\n")
	f.Close()

	code, out := capture(t, "log", "--log", path)
	lines := strings.Split(strings.TrimSpace(out), "\n")
	if code != 0 || len(lines) != 4 || strings.Contains(out, "SECRET-STATE") {
		t.Fatalf("all: %d\n%s", code, out)
	}
	for i, want := range []string{"pass  → auto-merge", "o/r#2 @ bbbbbbbbbb  duplicate-of  [shadow]", "o/other#9 @ cccccccccc  weak-tests  [reused]", "(no rule)  [error: "} {
		if !strings.Contains(lines[i], want) {
			t.Errorf("line %d: missing %q in %q", i, want, lines[i])
		}
	}
	if !strings.Contains(lines[3], "2 debounced") {
		t.Errorf("debounced note missing: %q", lines[3])
	}
	if _, out = capture(t, "log", "--log", path, "--verdict", "hold", "--repo", "o/r"); strings.Count(out, "\n") != 2 {
		t.Errorf("holds in o/r:\n%s", out)
	}
	if _, out = capture(t, "log", "--log", path, "-n", "1"); !strings.Contains(out, "eeeeeeeeee") || strings.Count(out, "\n") != 1 {
		t.Errorf("-n 1 keeps the newest:\n%s", out)
	}
	if _, out = capture(t, "log", "--log", path, "--since", "90m"); strings.Count(out, "\n") != 2 {
		t.Errorf("--since 90m:\n%s", out)
	}
	if _, out = capture(t, "log", "--log", path, "--pr", "77"); !strings.Contains(out, "no decisions match") {
		t.Errorf("no match:\n%s", out)
	}
	code, out = capture(t, "log", "--log", path, "--json", "--pr", "2")
	var first store.Record
	jl := strings.Split(strings.TrimSpace(out), "\n")
	if code != 0 || len(jl) != 2 || json.Unmarshal([]byte(jl[0]), &first) != nil || first.HeadSHA != "bbbbbbbbbbbbbbbb" || first.State != "" || strings.Contains(out, "SECRET-STATE") {
		t.Fatalf("json: %d\n%s", code, out)
	}
	if code, _ = capture(t, "log", "--log", path, "--verdict", "maybe"); code != 2 {
		t.Errorf("bad verdict: exit %d", code)
	}
	if code, _ = capture(t, "log", "--log", filepath.Join(dir, "nope.jsonl")); code != 2 {
		t.Errorf("missing log: exit %d", code)
	}
}
