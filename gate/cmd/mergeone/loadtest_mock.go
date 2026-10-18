package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/memory"
	"github.com/haalp/mergeone/gate/internal/models"
)

// timedModel wraps one decision's model calls and records when the main (QuestionsV2) call and the
// pair (same_change) calls ran. It is per decision, so the breakdown does not depend on engine internals.
type timedModel struct {
	inner              engine.Model
	mu                 sync.Mutex
	mainMS             float64
	pairStart, pairEnd time.Time
	pairCalls          int
}

func (t *timedModel) Name() string { return t.inner.Name() }

func (t *timedModel) Decide(state string, qs []string) engine.Decision {
	s := time.Now()
	d := t.inner.Decide(state, qs)
	e := time.Now()
	t.mu.Lock()
	defer t.mu.Unlock()
	if len(qs) == 1 && qs[0] == "same_change" {
		if t.pairCalls == 0 || s.Before(t.pairStart) {
			t.pairStart = s
		}
		if e.After(t.pairEnd) {
			t.pairEnd = e
		}
		t.pairCalls++
	} else {
		t.mainMS = float64(e.Sub(s).Microseconds()) / 1000
	}
	return d
}

func pct(xs []float64, p float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	s := append([]float64(nil), xs...)
	sort.Float64s(s)
	return s[min(int(float64(len(s))*p), len(s)-1)]
}

// loadtestMock runs open-loop decisions in-process through engine.Gate.DecideFor with the mock model
// (300±60 ms) and a synthetic PR memory, and prints a per-decision latency breakdown plus a digest of
// every decision's verdict/probabilities/candidates (equal digests ⇒ identical decisions).
func loadtestMock(policyPath string, rate float64, n, k, memN int, tune func(*engine.Gate)) error {
	p, err := engine.LoadPolicy(policyPath)
	if err != nil {
		return err
	}
	mem, prs := memory.Synthetic(memN, n, 7)
	mock := &models.Mock{LatencyMS: 300, JitterMS: 60}
	base := engine.Gate{Policy: p, Model: mock, Cal: &engine.Calibrator{}, Memory: mem, PairK: k}
	if tune != nil {
		tune(&base)
	}
	type row struct {
		wall, model, pairs, latency float64
		calls                       int
		digest                      string
	}
	rows := make([]row, n)
	var wg sync.WaitGroup
	t0 := time.Now()
	for i := 0; i < n; i++ {
		time.Sleep(time.Until(t0.Add(time.Duration(float64(i) / rate * float64(time.Second)))))
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			tm := &timedModel{inner: mock}
			g := base
			g.Model = tm
			s := time.Now()
			res := g.DecideFor(prs[i].State, prs[i].Meta)
			wall := float64(time.Since(s).Microseconds()) / 1000
			b, _ := json.Marshal(map[string]any{"verdict": res.Verdict, "route": res.Route, "rule": res.Rule, "reason": res.Reason,
				"probs": res.Probs, "raw_probs": res.RawProbs, "extra": res.Extra, "cost": res.CostUSD})
			pairs := 0.0
			if tm.pairCalls > 0 {
				pairs = float64(tm.pairEnd.Sub(tm.pairStart).Microseconds()) / 1000
			}
			rows[i] = row{wall, tm.mainMS, pairs, res.LatencyMS, tm.pairCalls, string(b)}
		}(i)
	}
	wg.Wait()
	elapsed := time.Since(t0).Seconds()
	var wall, model, pairs, lat []float64
	calls := 0
	h := sha256.New()
	for i, r := range rows {
		wall, model, pairs, lat = append(wall, r.wall), append(model, r.model), append(pairs, r.pairs), append(lat, r.latency)
		calls += r.calls
		fmt.Fprintf(h, "%d\t%s\n", i, r.digest)
	}
	out := map[string]any{"mode": "mock-inprocess", "rate_per_s": rate, "n": n, "k": k, "memory_prs": memN, "wall_s": elapsed,
		"model_p50": pct(model, 0.5), "model_p99": pct(model, 0.99),
		"pairs_p50": pct(pairs, 0.5), "pairs_p99": pct(pairs, 0.99),
		"wall_p50": pct(wall, 0.5), "wall_p99": pct(wall, 0.99),
		"result_latency_ms_p50":   pct(lat, 0.5),
		"pair_calls_per_decision": float64(calls) / float64(n),
		"decisions_sha256":        hex.EncodeToString(h.Sum(nil))[:16]}
	b, _ := json.Marshal(out)
	fmt.Println(string(b))
	return nil
}
