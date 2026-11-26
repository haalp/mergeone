package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

func TestRelatedStatusAnswersOneQuestionAndFailsClosed(t *testing.T) {
	dupe := map[string]any{"duplicate_candidate": 41}
	for _, c := range []struct {
		name      string
		res       engine.Result
		state, in string
	}{
		{"nothing related", engine.Result{Verdict: "pass", Probs: map[string]float64{"duplicate_of": 0.12}}, "success", "no open pull request"},
		{"held for another reason, nothing related", engine.Result{Verdict: "hold", Route: "human", Probs: map[string]float64{"duplicate_of": 0.2}}, "success", "no open pull request"},
		{"duplicate by route", engine.Result{Verdict: "hold", Route: "dedup", Probs: map[string]float64{"duplicate_of": 0.91}, Extra: dupe}, "failure", "same change as #41 (0.91)"},
		{"duplicate by answer though another rule matched first", engine.Result{Verdict: "hold", Route: "human", Probs: map[string]float64{"duplicate_of": 0.8}, Extra: dupe}, "failure", "#41"},
		{"at the threshold", engine.Result{Verdict: "pass", Probs: map[string]float64{"duplicate_of": 0.7}}, "failure", "another open pull request"},
		{"the decision failed", engine.Result{Verdict: "hold", Route: "human", Error: "model: 503"}, "error", "decision failed"},
		{"a comparison did not answer", engine.Result{Verdict: "pass", Probs: map[string]float64{"duplicate_of": 0.1}, PairErrors: 2}, "error", "2 comparison(s)"},
		{"no duplicate answer at all", engine.Result{Verdict: "pass", Probs: map[string]float64{"is_logic_change": 0.1}}, "error", "mergeone index"},
	} {
		state, desc := RelatedStatus(c.res, 0.7)
		if state != c.state || !strings.Contains(desc, c.in) || len([]rune(desc)) > 140 {
			t.Errorf("%s: %s %q (want %s … %q, ≤ 140 chars)", c.name, state, desc, c.state, c.in)
		}
	}
}

func relatedActions(t *testing.T) engine.Actions {
	t.Helper()
	a := engine.Actions{Enabled: true, RelatedContext: "mergeone/related"}
	if err := a.Normalize(); err != nil {
		t.Fatal(err)
	}
	return a
}

func statusesOf(f *fakeGH, context string) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for _, s := range f.statuses {
		if s["context"] == context {
			out = append(out, s["state"].(string))
		}
	}
	return out
}

func TestRelatedCheckIsASecondStatusAndAPersonCanAnswerIt(t *testing.T) {
	f, gh := newFakeGH(t)
	cfg := relatedActions(t)
	res := engine.Result{Verdict: "hold", Route: "dedup", Rule: "duplicate-of", Probs: map[string]float64{"duplicate_of": 0.93}, Extra: map[string]any{"duplicate_candidate": 12}}
	rep := ApplyActions(gh, cfg, "o/r", 7, "sha1", res, "")
	if got := strings.Join(rep.Applied, " "); !strings.Contains(got, "status:failure") || !strings.Contains(got, "related:failure") {
		t.Fatalf("applied: %v, errors %v", rep.Applied, rep.Errors)
	}
	if got := statusesOf(f, "mergeone/related"); len(got) != 1 || got[0] != "failure" {
		t.Fatalf("related statuses: %v", got)
	}
	if rep.Related == nil || rep.Related.Context != "mergeone/related" || rep.Related.State != "failure" {
		t.Fatalf("the report does not carry the related check: %+v", rep.Related)
	}
	// link_duplicate and request_rebase leave the check failed; an approval by a named person answers it
	for _, action := range []string{"link_duplicate", "request_rebase"} {
		ApplyResolution(gh, cfg, store.Resolution{Repo: "o/r", PR: 7, HeadSHA: "sha1", Action: action, TargetPR: 12, Login: "ada"})
	}
	if got := statusesOf(f, "mergeone/related"); len(got) != 1 {
		t.Fatalf("a non-approve resolution wrote the related check: %v", got)
	}
	ApplyResolution(gh, cfg, store.Resolution{Repo: "o/r", PR: 7, HeadSHA: "sha1", Action: "approve", TargetPR: 12}) // nobody named: not a person's decision
	if got := statusesOf(f, "mergeone/related"); len(got) != 1 {
		t.Fatalf("an approval with no login wrote the related check: %v", got)
	}
	rep = ApplyResolution(gh, cfg, store.Resolution{Repo: "o/r", PR: 7, HeadSHA: "sha1", Action: "approve", TargetPR: 12, Login: "ada"})
	got := statusesOf(f, "mergeone/related")
	if len(got) != 2 || got[1] != "success" || !strings.Contains(strings.Join(rep.Applied, " "), "related:success") {
		t.Fatalf("after an approval: %v, applied %v", got, rep.Applied)
	}
	f.mu.Lock()
	last := f.statuses[len(f.statuses)-1]
	f.mu.Unlock()
	if d := last["description"].(string); !strings.Contains(d, "kept by ada") || !strings.Contains(d, "#12") {
		t.Errorf("description %q", d)
	}
	// the verdict's own status is never written by a resolution
	if got := statusesOf(f, "mergeone"); len(got) != 1 || got[0] != "failure" {
		t.Errorf("verdict statuses: %v", got)
	}
}

func TestRelatedCheckOffByDefaultAndNeedsMemory(t *testing.T) {
	f, gh := newFakeGH(t)
	plain := engine.Actions{Enabled: true}
	_ = plain.Normalize()
	ApplyActions(gh, plain, "o/r", 3, "s", engine.Result{Verdict: "pass", Rule: "default", Probs: map[string]float64{"duplicate_of": 0.99}}, "")
	ApplyResolution(gh, plain, store.Resolution{Repo: "o/r", PR: 3, HeadSHA: "s", Action: "approve", Login: "ada"})
	if got := statusesOf(f, "mergeone/related"); len(got) != 0 || len(f.statuses) != 1 {
		t.Fatalf("no related_context, yet: %v (%d statuses)", got, len(f.statuses))
	}
	same := engine.Actions{Enabled: true, StatusContext: "x", RelatedContext: "x"}
	if err := same.Normalize(); err == nil {
		t.Error("the related check may not share the verdict's context")
	}
	bad := engine.Actions{RelatedThreshold: 1.5}
	if err := bad.Normalize(); err == nil {
		t.Error("related_threshold 1.5 must be refused")
	}
	// LoadPolicy: related_context without PR memory is refused, with it accepted
	dir := t.TempDir()
	base := "model: mock\nrules: []\ndefault: {verdict: pass, route: auto-merge, reason: ok}\nactions:\n  enabled: true\n  related_context: mergeone/related\n"
	p := filepath.Join(dir, "p.yaml")
	if err := os.WriteFile(p, []byte(base), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := engine.LoadPolicy(p); err == nil || !strings.Contains(err.Error(), "memory.enabled") {
		t.Errorf("without memory: %v", err)
	}
	if err := os.WriteFile(p, []byte(base+"memory:\n  enabled: true\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if pol, err := engine.LoadPolicy(p); err != nil || pol.Actions.RelatedThreshold != 0.7 {
		t.Errorf("with memory: %v", err)
	}
}
