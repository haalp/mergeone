package engine

import (
	"testing"
	"time"
)

type fakeMem struct{}

func (fakeMem) Candidates(meta PRMeta, aState string, k int) []Candidate {
	return []Candidate{{Number: 41, Score: 0.9, State: "MERGED", Title: "same thing", PairState: "PAIR:41"}, {Number: 42, Score: 0.3, PairState: "PAIR:42"}}
}

type fakeModel struct{}

func (fakeModel) Name() string { return "fake" }
func (fakeModel) Decide(state string, qs []string) Decision {
	if len(qs) == 1 && qs[0] == "same_change" {
		if state == "PAIR:41" {
			return Decision{Probs: map[string]float64{"same_change": 0.85}}
		}
		return Decision{Probs: map[string]float64{"same_change": 0.1}}
	}
	return Decision{Probs: map[string]float64{"is_logic_change": 0.1, "weakens_tests": 0.01, "message_matches_diff": 0.9, "touches_concurrency": 0.02, "needs_human_review": 0.1}}
}

func testPolicy(t *testing.T) *Policy {
	p := &Policy{Rules: []Rule{
		{Name: "duplicate-of", When: map[string]string{"duplicate_of": ">= 0.7"}, Verdict: "hold", Route: "dedup", Reason: "dup"},
		{Name: "stale", When: map[string]string{"silent_days": ">= 14"}, Verdict: "hold", Route: "stale", Reason: "stale"},
		{Name: "trivial", When: map[string]string{"is_logic_change": "<= 0.2"}, Verdict: "pass", Route: "auto-merge", Reason: "trivial"},
	}, Default: Outcome{"pass", "auto", ""}, OnError: Outcome{"hold", "human", "err"}}
	if err := p.compile(); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestDecideForDuplicateAndStale(t *testing.T) {
	g := &Gate{Policy: testPolicy(t), Model: fakeModel{}, Cal: &Calibrator{}, Memory: fakeMem{}, PairK: 5}
	res := g.DecideFor("state", PRMeta{Repo: "o/n", Number: 50, UpdatedAt: time.Now().Add(-time.Hour)})
	if res.Rule != "duplicate-of" || res.Verdict != "hold" || res.Route != "dedup" {
		t.Fatalf("expected dedup hold, got %+v", res)
	}
	if res.Probs["duplicate_of"] != 0.85 || res.Extra["duplicate_candidate"] != 41 {
		t.Fatalf("duplicate_of/candidate not recorded: %v %v", res.Probs, res.Extra)
	}
	g2 := &Gate{Policy: testPolicy(t), Model: fakeModel{}, Cal: &Calibrator{}}
	res = g2.DecideFor("state", PRMeta{Repo: "o/n", UpdatedAt: time.Now().AddDate(0, 0, -20)})
	if res.Rule != "stale" {
		t.Fatalf("expected stale, got %s", res.Rule)
	}
	res = g2.DecideFor("state", PRMeta{Repo: "o/n", UpdatedAt: time.Now()})
	if res.Rule != "trivial" || res.Verdict != "pass" {
		t.Fatalf("expected trivial pass without memory, got %s/%s", res.Rule, res.Verdict)
	}
}
