package engine

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func near(a, b float64) bool { return math.Abs(a-b) < 1e-12 }

func TestDriftAggMath(t *testing.T) {
	xs := []float64{0.1, 0.5, 0.9, 0.3, math.NaN()}
	var a DriftAgg
	for _, x := range xs {
		a.Add(x)
	}
	s := a.Stats()
	// mean 0.45, population variance ((.35²+.05²+.45²+.15²)/4) = 0.0875
	if s.N != 4 || !near(s.Mean, 0.45) || !near(s.YesRate, 0.5) || !near(s.Spread, math.Sqrt(0.0875)) {
		t.Fatalf("stats %+v", s)
	}
	var b, c DriftAgg
	b.Add(0.1)
	b.Add(0.5)
	c.Add(0.9)
	c.Add(0.3)
	b.Merge(c)
	if bs := b.Stats(); !near(bs.Mean, s.Mean) || !near(bs.Spread, s.Spread) || !near(bs.YesRate, 0.5) {
		t.Fatalf("merge %+v", bs)
	}
	if (DriftAgg{}).Stats() != (QStats{}) {
		t.Fatal("empty agg")
	}
}

func TestCompareDriftThresholds(t *testing.T) {
	ref := QStats{Mean: 0.5, Spread: 0.2, N: 1000}
	for _, c := range []struct {
		name string
		cur  QStats
		want bool
	}{
		{"shift above", QStats{Mean: 0.71, Spread: 0.2, N: 200}, true},
		{"shift at 0.20 is not above", QStats{Mean: 0.70, Spread: 0.2, N: 500}, false},
		{"small shift", QStats{Mean: 0.6, Spread: 0.2, N: 500}, false},
		{"spread halved", QStats{Mean: 0.5, Spread: 0.1, N: 500}, true},
		{"spread not quite halved", QStats{Mean: 0.5, Spread: 0.11, N: 500}, false},
		{"n below 200", QStats{Mean: 0.9, Spread: 0.01, N: 199}, false},
	} {
		if got := CompareDrift(c.cur, ref).Degraded; got != c.want {
			t.Errorf("%s: degraded=%v", c.name, got)
		}
	}
	if d := CompareDrift(QStats{Mean: 0.9, N: 500}, QStats{}); d.Degraded || d.Shift != 0 {
		t.Fatalf("no reference must not flag: %+v", d)
	}
	md := DriftReport(map[string]QStats{"a": {Mean: 0.9, Spread: 0.2, N: 300}, "b": {Mean: 0.5, Spread: 0.2, N: 300}},
		map[string]QStats{"a": ref, "b": ref, "c": ref}, "gatelog")
	if len(md.Degraded) != 1 || md.Degraded[0] != "a" || len(md.Questions) != 3 {
		t.Fatalf("report %+v", md)
	}
}

func driftGate(t *testing.T, extra string, degraded ...string) *Gate {
	t.Helper()
	b, err := os.ReadFile(filepath.Join("..", "..", "..", "mergeone", "gate", "policy.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "p.yaml")
	base := strings.Replace(string(b), "drift:\n  enabled: true\n", "", 1) // the shipped default, replaced by extra
	if err := os.WriteFile(path, []byte(base+"\n"+extra+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	p, err := LoadPolicy(path)
	if err != nil {
		t.Fatal(err)
	}
	set := map[string]bool{}
	for _, q := range degraded {
		set[q] = true
	}
	return &Gate{Policy: p, Cal: &Calibrator{}, Degraded: func() map[string]bool { return set }}
}

func TestDriftSkipsRulesReadingDegradedQuestion(t *testing.T) {
	// maintainer-judgement reads needs_human_review; trivial below it would pass this PR.
	probs := map[string]float64{"weakens_tests": 0.1, "is_logic_change": 0.1, "touches_concurrency": 0.1, "needs_human_review": 0.1}
	g := driftGate(t, "drift: {enabled: true}", "needs_human_review")
	g.Model = fixed{p: probs}
	r := g.Decide("x", nil)
	if r.Rule != "on_drift" || r.Verdict != "hold" || r.Route != "human" || r.Reason != "drift:needs_human_review" {
		t.Fatalf("got %+v", r)
	}
	if len(r.Drift) != 1 || r.Drift[0] != "needs_human_review" {
		t.Fatalf("drift list %v", r.Drift)
	}
	// A rule above the degraded one still decides.
	probs["weakens_tests"] = 0.9
	if r := g.Decide("y", nil); r.Rule != "weakened-tests" || r.Verdict != "block" {
		t.Fatalf("healthy rule above: %+v", r)
	}
	// A degraded question no rule reads changes nothing (follows_conventions, as in run 7).
	g = driftGate(t, "drift: {enabled: true}", "follows_conventions")
	g.Model = fixed{p: map[string]float64{"weakens_tests": 0.1, "is_logic_change": 0.1, "touches_concurrency": 0.1, "needs_human_review": 0.1}}
	if r := g.Decide("x", nil); r.Rule != "trivial" || r.Drift[0] != "follows_conventions" {
		t.Fatalf("unread degraded question: %+v", r)
	}
}

func TestDriftIgnoreAndDisabled(t *testing.T) {
	probs := map[string]float64{"weakens_tests": 0.1, "is_logic_change": 0.1, "touches_concurrency": 0.1, "needs_human_review": 0.1}
	g := driftGate(t, "drift: {enabled: true}\non_drift: ignore", "needs_human_review")
	g.Model = fixed{p: probs}
	if r := g.Decide("x", nil); r.Rule != "trivial" {
		t.Fatalf("ignore must keep the rules: %+v", r)
	}
	g = driftGate(t, "drift: {enabled: false}", "needs_human_review")
	g.Model = fixed{p: probs}
	if r := g.Decide("x", nil); r.Rule != "trivial" || r.Drift != nil {
		t.Fatalf("disabled monitor must not act: %+v", r)
	}
	g = driftGate(t, "", "needs_human_review") // v0.3: drift.enabled defaults to true when the key is absent
	g.Model = fixed{p: probs}
	if r := g.Decide("x", nil); r.Rule != "on_drift" || !g.Policy.Drift.Enabled {
		t.Fatalf("default-on monitor must act: %+v", r)
	}
	if g.Policy.OnDrift.Verdict != "hold" || g.Policy.OnDrift.Route != "human" {
		t.Fatalf("default on_drift %+v", g.Policy.OnDrift)
	}
	g = driftGate(t, "drift: {enabled: true}\non_drift: {verdict: block, route: domain-review, reason: refit the map}", "needs_human_review")
	g.Model = fixed{p: probs}
	if r := g.Decide("x", nil); r.Verdict != "block" || r.Route != "domain-review" || r.Reason != "drift:needs_human_review: refit the map" {
		t.Fatalf("custom on_drift: %+v", r)
	}
	path := filepath.Join(t.TempDir(), "bad.yaml")
	_ = os.WriteFile(path, []byte("on_drift: maybe\n"), 0o644)
	if _, err := LoadPolicy(path); err == nil {
		t.Fatal("on_drift: maybe must be an error")
	}
}

func TestCalibrationFitStatsOptional(t *testing.T) {
	path := filepath.Join(t.TempDir(), "cal.json")
	_ = os.WriteFile(path, []byte(`{"q": {"x": [0, 1], "y": [0.2, 0.8], "n": 10},
		"fit_stats": {"q": {"mean": 0.4, "yes_rate": 0.3, "spread": 0.2, "n": 1491}}}`), 0o644)
	c, err := LoadCalibration(path)
	if err != nil {
		t.Fatal(err)
	}
	if c.FitStats["q"].N != 1491 || !near(c.Apply(map[string]float64{"q": 0.5})["q"], 0.5) {
		t.Fatalf("fit_stats %+v", c.FitStats)
	}
	if _, ok := c.maps["fit_stats"]; ok {
		t.Fatal("fit_stats read as a question")
	}
}
