package server

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// Three non-pass heads on one PR, then a fourth: max-rounds routes it to a human; a pass is never overridden.
func TestMaxRoundsCapsAfterNHolds(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+"max_rounds: 3\n", nil)
	for i, sha := range []string{"h1", "h2", "h3"} {
		res := gw.Decide("o/r", 9, sha, "hold", nil, "test")
		if res.Rule != "maintainer-judgement" || res.Rounds != i+1 {
			t.Fatalf("round %d: %+v", i+1, res)
		}
	}
	if res := gw.Decide("o/r", 9, "h1", "hold", nil, "test"); res.Rule != "maintainer-judgement" {
		t.Fatalf("re-deciding a head is not a new round: %+v", res) // served from the gatelog
	}
	res := gw.Decide("o/r", 9, "h4", "domain", nil, "test")
	want := "4 verdicts without pass on this PR; routed to a human (max_rounds; last rule: concurrency-risk)"
	if res.Verdict != "hold" || res.Route != "human" || res.Rule != "max-rounds" || res.Reason != want || res.CappedRule != "concurrency-risk" || res.Rounds != 4 || res.Probs["touches_concurrency"] != 0.9 {
		t.Fatalf("want max-rounds: %+v", res)
	}
	if res := gw.Decide("o/r", 9, "h5", "pass", nil, "test"); res.Verdict != "pass" || res.Rule != "default" || res.Rounds != 0 || res.CappedRule != "" {
		t.Fatalf("pass overridden: %+v", res)
	}
	if res := gw.Decide("o/r", 10, "x1", "hold", nil, "test"); res.Rule != "maintainer-judgement" || res.Rounds != 1 {
		t.Fatalf("rounds leaked across PRs: %+v", res)
	}
}

func TestMaxRoundsOffOrNoPR(t *testing.T) {
	off, _ := newTestGateway(t, testRules, nil)
	on, _ := newTestGateway(t, testRules+"max_rounds: 1\n", nil)
	for _, sha := range []string{"a", "b", "c", "d"} {
		if res := off.Decide("o/r", 9, sha, "hold", nil, "test"); res.Rule != "maintainer-judgement" || res.Rounds != 0 {
			t.Fatalf("max_rounds 0 capped: %+v", res)
		}
		if res := on.Decide("o/r", 0, sha, "hold", nil, "test"); res.Rule != "maintainer-judgement" || res.Rounds != 0 {
			t.Fatalf("no PR number capped: %+v", res)
		}
	}
	pp := filepath.Join(t.TempDir(), "p.yaml")
	_ = os.WriteFile(pp, []byte(testRules+"max_rounds: -1\n"), 0o644)
	if _, err := engine.LoadPolicy(pp); err == nil {
		t.Fatal("want a policy error for max_rounds -1")
	}
}
