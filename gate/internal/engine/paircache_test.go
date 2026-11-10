package engine_test

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// countingModel answers the main call and pair calls and counts them; with block set, a call waits for its
// context to be cancelled.
type countingModel struct {
	main, pair atomic.Int64
	block      bool
}

func (m *countingModel) Name() string { return "counting" }
func (m *countingModel) Decide(state string, qs []string) engine.Decision {
	return m.DecideCtx(context.Background(), state, qs)
}
func (m *countingModel) DecideCtx(ctx context.Context, state string, qs []string) engine.Decision {
	if len(qs) == 1 && qs[0] == "same_change" {
		m.pair.Add(1)
	} else {
		m.main.Add(1)
	}
	if m.block {
		<-ctx.Done()
		return engine.Decision{Probs: map[string]float64{}, Error: engine.ErrCanceled}
	}
	pr := map[string]float64{}
	for _, q := range qs {
		pr[q] = 0.1
	}
	return engine.Decision{Probs: pr, CostUSD: 0.001, InputTokens: 100, ModelVersion: "m1"}
}

type fixedMemory struct{ cands []engine.Candidate }

func (f fixedMemory) Candidates(engine.PRMeta, string, int) []engine.Candidate { return f.cands }

func cacheGate(t *testing.T, m engine.Model, cands []engine.Candidate) *engine.Gate {
	t.Helper()
	pol, err := engine.LoadPolicy(writePolicy(t, minimalPolicy))
	if err != nil {
		t.Fatal(err)
	}
	return &engine.Gate{Policy: pol, Model: m, Cal: &engine.Calibrator{}, Memory: fixedMemory{cands}, PairK: 10, PairCache: engine.NewPairCache()}
}

func TestPairCacheSkipsIdenticalPairInputs(t *testing.T) {
	cands := []engine.Candidate{{Number: 1, PairState: "A|B1"}, {Number: 2, PairState: "A|B2"}, {Number: 3, PairState: "A|B3"}}
	m := &countingModel{}
	g := cacheGate(t, m, cands)
	meta := engine.PRMeta{Repo: "o/r", Number: 9}
	r1 := g.DecideFor("state v1", meta)
	if m.pair.Load() != 3 || r1.PairCacheHits != 0 || r1.ModelCalls != 4 {
		t.Fatalf("first decision: pair calls %d, hits %d, model_calls %d", m.pair.Load(), r1.PairCacheHits, r1.ModelCalls)
	}
	// a new push: the main state changed, two pair inputs are identical, one changed
	g.Memory = fixedMemory{[]engine.Candidate{cands[0], cands[1], {Number: 3, PairState: "A'|B3"}}}
	r2 := g.DecideFor("state v2", meta)
	if m.pair.Load() != 4 || r2.PairCacheHits != 2 || r2.ModelCalls != 2 {
		t.Fatalf("second decision: pair calls %d (want 4), hits %d (want 2), model_calls %d (want 2)", m.pair.Load(), r2.PairCacheHits, r2.ModelCalls)
	}
	if r2.Probs["duplicate_of"] != r1.Probs["duplicate_of"] {
		t.Fatalf("cached answers must give the same duplicate_of: %v vs %v", r2.Probs["duplicate_of"], r1.Probs["duplicate_of"])
	}
	if r2.CostUSD >= r1.CostUSD {
		t.Fatalf("cached pair answers cost nothing: %v vs %v", r2.CostUSD, r1.CostUSD)
	}
	// without a cache every pair call is made
	g.PairCache = nil
	g.DecideFor("state v3", meta)
	if m.pair.Load() != 7 {
		t.Fatalf("no cache: want 3 more pair calls, total %d", m.pair.Load())
	}
}

func TestCancelAbandonsTheDecision(t *testing.T) {
	cands := make([]engine.Candidate, 10)
	for i := range cands {
		cands[i] = engine.Candidate{Number: i + 1, PairState: "p" + string(rune('a'+i))}
	}
	m := &countingModel{block: true}
	g := cacheGate(t, m, cands)
	g.PairConcurrency = 2
	ctx, cancel := context.WithCancel(context.Background())
	var res engine.Result
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { defer wg.Done(); res = g.DecideForCtx(ctx, "s", engine.PRMeta{Repo: "o/r", Number: 1}) }()
	time.Sleep(50 * time.Millisecond)
	cancel()
	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("a cancelled decision must return")
	}
	if res.Error != engine.ErrCanceled {
		t.Fatalf("want ErrCanceled, got %+v", res)
	}
	if n := m.pair.Load(); n > 3 {
		t.Fatalf("pair calls not yet started must not be made after cancel: %d of 10 were", n)
	}
	if g.PairCache.Len() != 0 {
		t.Fatal("a cancelled answer must not be cached")
	}
}

func TestBudgetBlockValidated(t *testing.T) {
	if _, err := engine.LoadPolicy(writePolicy(t, minimalPolicy+"budget: { daily_usd: -1 }\n")); err == nil {
		t.Fatal("negative budget accepted")
	}
	if _, err := engine.LoadPolicy(writePolicy(t, minimalPolicy+"budget: { daily_usd: 1, on_exceeded: { verdict: pass, route: auto-merge } }\n")); err == nil {
		t.Fatal("a budget must never pass a PR")
	}
	p, err := engine.LoadPolicy(writePolicy(t, minimalPolicy+"budget: { daily_usd: 2.5 }\n"))
	if err != nil || p.Budget.OnExceeded.Verdict != "hold" || p.Budget.OnExceeded.Route != "human" {
		t.Fatalf("default on_exceeded: %+v %v", p.Budget, err)
	}
}
