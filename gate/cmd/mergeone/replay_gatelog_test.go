package main

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

func replayPolicy(t *testing.T, threshold string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "policy.yaml")
	body := "version: 1\nmodel: mock\nquestion_set: v2\ncalibration: none\nrules:\n" +
		"  - { name: needs-human, when: { needs_human_review: \">= " + threshold + "\" }, verdict: hold, route: human, reason: h }\n" +
		"  - { name: weak-tests, when: { weakens_tests: \">= 0.6\" }, verdict: block, route: human, reason: b }\n" +
		"default: { verdict: pass, route: auto-merge }\n"
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func rec(sha string, verdict, route, rule string, probs map[string]float64, extra func(*store.Record)) string {
	r := store.Record{TS: time.Now().UTC(), Repo: "o/r", PR: 1, HeadSHA: sha,
		Result: engine.Result{Verdict: verdict, Route: route, Rule: rule, RawProbs: probs, Probs: probs}}
	if extra != nil {
		extra(&r)
	}
	b, _ := json.Marshal(r)
	return string(b)
}

func TestReplayGatelogFindsFlips(t *testing.T) {
	// recorded under threshold 0.5
	lines := []string{
		rec("a1", "hold", "human", "needs-human", map[string]float64{"needs_human_review": 0.7, "weakens_tests": 0.1}, nil),
		rec("a2", "hold", "human", "needs-human", map[string]float64{"needs_human_review": 0.95, "weakens_tests": 0.1}, nil),
		rec("a3", "pass", "auto-merge", "default", map[string]float64{"needs_human_review": 0.2, "weakens_tests": 0.1}, nil),
		rec("a4", "block", "human", "weak-tests", map[string]float64{"needs_human_review": 0.2, "weakens_tests": 0.9}, nil),
		rec("a5", "hold", "human", "on_error", nil, func(r *store.Record) { r.Result.Error = "HTTP 503" }),
		rec("a6", "hold", "human", "always_human_paths", nil, nil),
		rec("a7", "hold", "human", "max_rounds", map[string]float64{"needs_human_review": 0.1, "weakens_tests": 0.1},
			func(r *store.Record) { r.Result.CappedRule = "default"; r.Result.Verdict = "hold" }),
	}
	g, err := buildGate(replayPolicy(t, "0.9"), true, "")
	if err != nil {
		t.Fatal(err)
	}
	rep, err := replayGatelog(g, strings.NewReader(strings.Join(lines, "\n")+"\n"), time.Time{}, "p.yaml", "g.jsonl", 20)
	if err != nil {
		t.Fatal(err)
	}
	if rep.Records != 7 || rep.Replayed != 4 || rep.Skipped["on_error"] != 1 || rep.Skipped["always_human_paths"] != 1 || rep.Skipped["max_rounds"] != 1 {
		t.Fatalf("counts: %+v", rep)
	}
	// a1 flips hold→pass (0.7 < 0.9); a2 stays hold; a3 pass; a4 block; a7 (max_rounds) is not replayed
	if rep.Changed != 1 || rep.VerdictFlip != 1 || rep.Transitions["hold→pass"] != 1 || rep.Same != 3 {
		t.Fatalf("want one hold→pass flip, got changed=%d flips=%d transitions=%v same=%d", rep.Changed, rep.VerdictFlip, rep.Transitions, rep.Same)
	}
	if rep.ByRule["needs-human"]["default"] != 1 || len(rep.Changes) != 1 || rep.Changes[0].HeadSHA != "a1" {
		t.Fatalf("by_rule %v changes %+v", rep.ByRule, rep.Changes)
	}
	var buf bytes.Buffer
	rep.print(&buf)
	out := buf.String()
	for _, want := range []string{"replayed 4 of 7", "hold→pass    1", "needs-human", "o/r#1@a1", "not replayed", "on_error 1"} {
		if !strings.Contains(out, want) {
			t.Errorf("report missing %q:\n%s", want, out)
		}
	}
}

func TestReplayGatelogSamePolicyNoChanges(t *testing.T) {
	lines := []string{
		rec("b1", "hold", "human", "needs-human", map[string]float64{"needs_human_review": 0.7, "weakens_tests": 0.1}, nil),
		rec("b2", "pass", "auto-merge", "default", map[string]float64{"needs_human_review": 0.2, "weakens_tests": 0.1}, nil),
	}
	g, _ := buildGate(replayPolicy(t, "0.5"), true, "")
	rep, _ := replayGatelog(g, strings.NewReader(strings.Join(lines, "\n")), time.Time{}, "", "", 0)
	if rep.Changed != 0 || rep.Same != 2 || rep.VerdictFlip != 0 {
		t.Fatalf("same policy must replay identically: %+v", rep)
	}
}

func TestReplayGatelogCLIExitCode(t *testing.T) {
	dir := t.TempDir()
	log := filepath.Join(dir, "gatelog.jsonl")
	_ = os.WriteFile(log, []byte(rec("c1", "hold", "human", "needs-human", map[string]float64{"needs_human_review": 0.7, "weakens_tests": 0.1}, nil)+"\n"), 0o644)
	var out bytes.Buffer
	old := stdout
	stdout = &out
	defer func() { stdout = old }()
	if code := run([]string{"replay", "--policy", replayPolicy(t, "0.9"), "--gatelog", log}); code != 1 {
		t.Fatalf("a verdict flip exits 1, got %d: %s", code, out.String())
	}
	out.Reset()
	if code := run([]string{"replay", "--policy", replayPolicy(t, "0.5"), "--gatelog", log, "--json"}); code != 0 {
		t.Fatalf("no flip exits 0, got %d: %s", code, out.String())
	}
	var rep replayReport
	if err := json.Unmarshal(out.Bytes(), &rep); err != nil || rep.Replayed != 1 {
		t.Fatalf("json report: %v %s", err, out.String())
	}
}
