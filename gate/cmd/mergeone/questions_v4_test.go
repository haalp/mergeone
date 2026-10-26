package main

import (
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
