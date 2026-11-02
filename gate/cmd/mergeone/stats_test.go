package main

import (
	"bytes"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

func TestStatsCommandPrintsReused(t *testing.T) {
	logPath := filepath.Join(t.TempDir(), "gatelog.jsonl")
	st, err := store.Open(logPath)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	res := engine.Result{Verdict: "hold", Route: "human", Rule: "r", RawProbs: map[string]float64{"q": 0.7}, ModelVersion: "m-1", TS: now}
	_ = st.Record(store.Record{Repo: "o/r", PR: 1, HeadSHA: "a", PatchID: "p", Result: res})
	_ = st.Record(store.Record{Repo: "o/r", PR: 1, HeadSHA: "b", PatchID: "p", ReusedFrom: "a", DebouncedSHAs: []string{"x", "y"}, Result: res})
	var o, e bytes.Buffer
	oldOut, oldErr := stdout, stderr
	t.Cleanup(func() { stdout, stderr = oldOut, oldErr })
	stdout, stderr = &o, &e
	if code := run([]string{"stats", "--log", logPath, "--window", "1d"}); code != 0 {
		t.Fatalf("stats: %d %s", code, e.String())
	}
	out := o.String()
	if !strings.Contains(out, "2 decisions") || !strings.Contains(out, "reused   1 ") || !strings.Contains(out, "50.0%") || !strings.Contains(out, "debounced 2 ") {
		t.Fatalf("output:\n%s", out)
	}
}
