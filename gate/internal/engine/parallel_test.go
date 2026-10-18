package engine_test

import (
	"encoding/json"
	"math"
	"path/filepath"
	"sync/atomic"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/memory"
	"github.com/haalp/mergeone/gate/internal/models"
)

func loadPolicy(t *testing.T) *engine.Policy {
	p, err := engine.LoadPolicy(filepath.Join("..", "..", "..", "mergeone", "gate", "policy.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

// TestPairConcurrencyIdenticalDecisions replays 50 synthetic PRs against a 200-PR synthetic memory through the
// sequential pair path (PairConcurrency 1) and the parallel one (8), with the deterministic mock model, and
// asserts every decision is the same: probabilities, verdict, rule, reason, candidate order and duplicate_of.
func TestPairConcurrencyIdenticalDecisions(t *testing.T) {
	mem, prs := memory.Synthetic(200, 50, 7)
	pol := loadPolicy(t)
	for _, k := range []int{5, 20} {
		// Small jitter so parallel completion order differs from candidate order.
		mock := &models.Mock{LatencyMS: 1, JitterMS: 1, ErrorRate: 0.05}
		seq := &engine.Gate{Policy: pol, Model: mock, Cal: &engine.Calibrator{}, Memory: mem, PairK: k, PairConcurrency: 1}
		par := &engine.Gate{Policy: pol, Model: mock, Cal: &engine.Calibrator{}, Memory: mem, PairK: k, PairConcurrency: 8}
		rules := map[string]int{}
		for i, pr := range prs {
			a, b := seq.DecideFor(pr.State, pr.Meta), par.DecideFor(pr.State, pr.Meta)
			rules[a.Rule]++
			if a.Verdict != b.Verdict || a.Rule != b.Rule || a.Reason != b.Reason || a.Route != b.Route || a.Error != b.Error {
				t.Fatalf("k=%d PR %d: seq %s/%s/%q vs par %s/%s/%q", k, i, a.Verdict, a.Rule, a.Reason, b.Verdict, b.Rule, b.Reason)
			}
			if !sameProbs(a.Probs, b.Probs) || !sameProbs(a.RawProbs, b.RawProbs) {
				t.Fatalf("k=%d PR %d: probs differ\n seq %v\n par %v", k, i, a.Probs, b.Probs)
			}
			if a.Probs["duplicate_of"] != b.Probs["duplicate_of"] {
				t.Fatalf("k=%d PR %d: duplicate_of %v vs %v", k, i, a.Probs["duplicate_of"], b.Probs["duplicate_of"])
			}
			ja, _ := json.Marshal(a.Extra)
			jb, _ := json.Marshal(b.Extra)
			if string(ja) != string(jb) {
				t.Fatalf("k=%d PR %d: extra (candidates order / duplicate_candidate) differ\n seq %s\n par %s", k, i, ja, jb)
			}
			if a.Error == "" {
				if c, _ := a.Extra["candidates"].([]map[string]any); len(c) != k {
					t.Fatalf("k=%d PR %d: %d candidates, want %d", k, i, len(c), k)
				}
			}
			if a.CostUSD != b.CostUSD {
				t.Fatalf("k=%d PR %d: cost %v vs %v", k, i, a.CostUSD, b.CostUSD)
			}
		}
		if rules["duplicate-of"] == 0 || len(rules) < 2 {
			t.Fatalf("k=%d: synthetic replay not discriminating enough: %v", k, rules)
		}
	}
}

func sameProbs(a, b map[string]float64) bool {
	if len(a) != len(b) {
		return false
	}
	for q, x := range a {
		y, ok := b[q]
		if !ok || !(x == y || (math.IsNaN(x) && math.IsNaN(y))) {
			return false
		}
	}
	return true
}

// slowModel counts concurrent calls and sleeps a fixed time, to check the overlap and the wall-clock latency.
type slowModel struct {
	d             time.Duration
	inFlight, max atomic.Int32
}

func (s *slowModel) Name() string { return "slow" }
func (s *slowModel) Decide(state string, qs []string) engine.Decision {
	n := s.inFlight.Add(1)
	for {
		m := s.max.Load()
		if n <= m || s.max.CompareAndSwap(m, n) {
			break
		}
	}
	time.Sleep(s.d)
	s.inFlight.Add(-1)
	p := map[string]float64{}
	for _, q := range qs {
		p[q] = 0.1
	}
	return engine.Decision{Probs: p, LatencyMS: float64(s.d.Milliseconds())}
}

func TestDecideForOverlapsAndReportsWallClock(t *testing.T) {
	mem, prs := memory.Synthetic(200, 1, 7)
	m := &slowModel{d: 40 * time.Millisecond}
	g := &engine.Gate{Policy: loadPolicy(t), Model: m, Cal: &engine.Calibrator{}, Memory: mem, PairK: 5}
	res := g.DecideFor(prs[0].State, prs[0].Meta)
	if got := m.max.Load(); got != 6 { // main + 5 pair calls in flight together
		t.Fatalf("max concurrent model calls %d, want 6", got)
	}
	if res.ModelLatencySumMS != 6*40 {
		t.Fatalf("ModelLatencySumMS %v, want 240", res.ModelLatencySumMS)
	}
	if res.LatencyMS >= 200 || res.LatencyMS < 40 {
		t.Fatalf("LatencyMS %v should be wall clock (~40 ms), not the 240 ms sum", res.LatencyMS)
	}
	// No memory: unchanged path, LatencyMS is the main call's.
	g.Memory = nil
	res = g.DecideFor(prs[0].State, prs[0].Meta)
	if _, ok := res.Probs["duplicate_of"]; ok || res.LatencyMS != 40 || res.ModelLatencySumMS != 40 {
		t.Fatalf("no-memory path changed: %+v", res)
	}
}
