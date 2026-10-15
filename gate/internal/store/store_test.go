package store

import (
	"path/filepath"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func TestIdempotentJobsAndReplayOnOpen(t *testing.T) {
	p := filepath.Join(t.TempDir(), "log.jsonl")
	s, _ := Open(p)
	if !s.Enqueue("r:1:a", nil) || s.Enqueue("r:1:a", nil) {
		t.Fatal("enqueue not idempotent")
	}
	_ = s.Record(Record{Repo: "r", PR: 7, HeadSHA: "abc", Result: engine.Result{Verdict: "pass", Route: "auto", Rule: "default", InputSHA256: "h"}})
	s2, _ := Open(p)
	if r := s2.Latest("r", "abc"); r == nil || r.Result.Verdict != "pass" || s2.LatestForPR("r", 7) == nil {
		t.Fatal("replay on open failed")
	}
}
