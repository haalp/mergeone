package engine

import (
	"math"
	"os"
	"path/filepath"
	"testing"
)

type fixed struct {
	p   map[string]float64
	err string
}

func (f fixed) Name() string { return "fixed" }
func (f fixed) Decide(string, []string) Decision {
	return Decision{Probs: f.p, ModelVersion: "fixed", Error: f.err}
}

func policy(t *testing.T) *Policy {
	p, err := LoadPolicy(filepath.Join("..", "..", "..", "mergeone", "gate", "policy.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	return p
}

func TestFirstMatchWins(t *testing.T) {
	g := &Gate{Policy: policy(t), Model: fixed{p: map[string]float64{"weakens_tests": 0.9, "is_logic_change": 0.9, "message_matches_diff": 0.1, "touches_concurrency": 0.9, "needs_human_review": 0.9}}, Cal: &Calibrator{}}
	r := g.Decide("x", []string{"pkg/a.go"})
	if r.Rule != "weakened-tests" || r.Verdict != "block" {
		t.Fatalf("got %+v", r)
	}
}

func TestAlwaysHumanPathsBeforeModel(t *testing.T) {
	g := &Gate{Policy: policy(t), Model: fixed{p: map[string]float64{}}, Cal: &Calibrator{}}
	r := g.Decide("x", []string{"pkg/apis/core/types.go"})
	if r.Rule != "always_human_paths" || r.Verdict != "hold" {
		t.Fatalf("got %+v", r)
	}
}

func TestFailClosed(t *testing.T) {
	g := &Gate{Policy: policy(t), Model: fixed{p: map[string]float64{}, err: "boom"}, Cal: &Calibrator{}}
	r := g.Decide("x", nil)
	if r.Rule != "on_error" || r.Verdict != "hold" || r.Error == "" {
		t.Fatalf("got %+v", r)
	}
}

func TestCalibrationInterp(t *testing.T) {
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "c.json"), []byte(`{"q":{"x":[0,1],"y":[0.2,0.8]}}`), 0o644)
	c, err := LoadCalibration(filepath.Join(dir, "c.json"))
	if err != nil {
		t.Fatal(err)
	}
	out := c.Apply(map[string]float64{"q": 0.5, "other": 0.3})
	if math.Abs(out["q"]-0.5) > 1e-9 || out["other"] != 0.3 {
		t.Fatalf("got %v", out)
	}
}

func TestDefaultWhenNoRuleMatches(t *testing.T) {
	g := &Gate{Policy: policy(t), Model: fixed{p: map[string]float64{"weakens_tests": 0.1, "is_logic_change": 0.5, "message_matches_diff": 0.9, "touches_concurrency": 0.1, "needs_human_review": 0.1}}, Cal: &Calibrator{}}
	r := g.Decide("x", nil)
	if r.Rule != "default" || r.Verdict != "pass" {
		t.Fatalf("got %+v", r)
	}
}
