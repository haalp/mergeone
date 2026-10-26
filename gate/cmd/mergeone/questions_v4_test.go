package main

import (
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// question_set: v4 resolves calibration_<model>_v4.json from the assets directory and asks eight questions.
func TestBuildGateQuestionSetV4(t *testing.T) {
	root, _ := filepath.Abs("../../..")
	pp := filepath.Join(t.TempDir(), "p.yaml")
	os.WriteFile(pp, []byte("model: jev\nquestion_set: v4\n"), 0o644)
	g, err := buildGate(pp, true, root)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.HasSuffix(g.Cal.Path, filepath.Join("mergeone", "gate", "calibration_jev_v4.json")) || len(g.Cal.FitStats) != 8 {
		t.Fatalf("calibration %q, %d fit_stats", g.Cal.Path, len(g.Cal.FitStats))
	}
	r := g.Decide("state", nil)
	if len(r.RawProbs) != len(engine.QuestionsV4) || len(r.RawProbs) != 8 {
		t.Fatalf("asked %d questions: %v", len(r.RawProbs), r.RawProbs)
	}
	for _, q := range engine.QuestionsV4 {
		if _, ok := r.RawProbs[q]; !ok {
			t.Fatalf("missing %s in %v", q, r.RawProbs)
		}
	}
}

// question_set: v3 ships no calibration map: buildGate refuses it with a message naming v2 and v4.
func TestBuildGateQuestionSetV3NoMap(t *testing.T) {
	root, _ := filepath.Abs("../../..")
	pp := filepath.Join(t.TempDir(), "p.yaml")
	os.WriteFile(pp, []byte("model: jev\nquestion_set: v3\n"), 0o644)
	for _, mock := range []bool{true, false} {
		_, err := buildGate(pp, mock, root)
		if err == nil || !strings.Contains(err.Error(), "no calibration map for v3; use v2 or v4") {
			t.Fatalf("mock=%v: want the v3 refusal, got %v", mock, err)
		}
	}
	os.WriteFile(pp, []byte("model: jev\nquestion_set: v3\ncalibration: none\n"), 0o644)
	if _, err := buildGate(pp, true, root); err == nil {
		t.Fatal("v3 with calibration: none must be refused too")
	}
}

// With calibration_<model>_v3.json beside the policy, v3 asks the ten v3 questions and applies that map.
func TestBuildGateQuestionSetV3WithMap(t *testing.T) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "p.yaml")
	os.WriteFile(pp, []byte("model: jev\nquestion_set: v3\n"), 0o644)
	os.WriteFile(filepath.Join(dir, "calibration_jev_v3.json"), []byte(`{"exceeds_scope": {"x": [0, 1], "y": [0.2, 0.4]}}`), 0o644)
	g, err := buildGate(pp, true, dir)
	if err != nil {
		t.Fatal(err)
	}
	if filepath.Base(g.Cal.Path) != "calibration_jev_v3.json" {
		t.Fatalf("calibration %q", g.Cal.Path)
	}
	r := g.Decide("state", nil)
	if len(r.RawProbs) != 10 || len(engine.QuestionsV3) != 10 {
		t.Fatalf("asked %d questions: %v", len(r.RawProbs), r.RawProbs)
	}
	for _, q := range engine.QuestionsV3 {
		if _, ok := r.RawProbs[q]; !ok {
			t.Fatalf("missing %s in %v", q, r.RawProbs)
		}
	}
	if want := 0.2 + 0.2*r.RawProbs["exceeds_scope"]; math.Abs(r.Probs["exceeds_scope"]-want) > 1e-9 {
		t.Fatalf("v3 map not applied: raw %.3f → %.3f, want %.3f", r.RawProbs["exceeds_scope"], r.Probs["exceeds_scope"], want)
	}
}
