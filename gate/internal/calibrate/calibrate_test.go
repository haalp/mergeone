package calibrate

import (
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// seed records n decided PRs of model with an iterated outcome: q's raw answer predicts the label
// (positives at 0.71–0.8, negatives at 0.2–0.29), h is constant.
func seed(t *testing.T, st *store.Store, model string, first, n int, now time.Time) {
	t.Helper()
	for i := 0; i < n; i++ {
		pr := first + i
		y := i%3 == 0
		p := 0.2 + 0.01*float64(i%10)
		if y {
			p = 0.8 - 0.01*float64(i%10)
		}
		ts := now.Add(-time.Duration(i%20) * 24 * time.Hour)
		r := store.Record{Repo: "o/r", PR: pr, HeadSHA: fmt.Sprintf("s%d", pr), Result: engine.Result{Verdict: "pass",
			RawProbs: map[string]float64{"q": p, "h": 0.5}, ModelVersion: model, InputSHA256: fmt.Sprint(pr), TS: ts}}
		if err := st.Record(r); err != nil {
			t.Fatal(err)
		}
		if _, err := st.RecordOutcome(store.PROutcome{Repo: "o/r", PR: pr, Iterated: &y}); err != nil {
			t.Fatal(err)
		}
	}
}

func TestFitFromGatelogWritesLoadableMap(t *testing.T) {
	dir := t.TempDir()
	st, _ := store.Open(filepath.Join(dir, "log.jsonl"))
	now := time.Date(2026, 10, 26, 12, 0, 0, 0, time.UTC)
	seed(t, st, "jev-1.14.0", 1, 150, now)
	// 150 labelled: refused, with the count in the message.
	if _, sum, err := Fit(st, Options{Now: now}); !errors.Is(err, ErrTooFew) || sum.N != 150 {
		t.Fatalf("under 200: %v %+v", err, sum)
	}
	seed(t, st, "jev-1.14.0", 1000, 150, now)
	seed(t, st, "other-2", 5000, 300, now) // another model: not mixed in
	f, sum, err := Fit(st, Options{Now: now, Model: "jev"})
	if err != nil {
		t.Fatal(err)
	}
	if sum.N != 300 || len(sum.ModelVersions) != 1 || sum.ModelVersions[0] != "jev-1.14.0" || !sum.Questions["q"].Fitted || !sum.Questions["h"].Fitted {
		t.Fatalf("summary %+v", sum)
	}
	if fs := f.FitStats["h"]; fs.N != 300 || fs.Mean != 0.5 || fs.Spread != 0 || fs.YesRate != 1 {
		t.Fatalf("fit_stats %+v", fs)
	}
	path := filepath.Join(dir, "calibration_jev_v2.json")
	if err := Write(path, f, false); err != nil {
		t.Fatal(err)
	}
	if err := Write(path, f, false); err == nil {
		t.Fatal("an existing map must not be replaced without force")
	}
	if err := Write(path, f, true); err != nil {
		t.Fatal(err)
	}
	c, err := engine.LoadCalibration(path)
	if err != nil {
		t.Fatal(err)
	}
	if c.Meta == nil || c.Meta.Source != "gatelog" || c.Meta.N != 300 || c.Meta.Label != "iterated" || c.FitStats["q"].N != 300 {
		t.Fatalf("loaded meta %+v fit_stats %+v", c.Meta, c.FitStats)
	}
	cal := c.Apply(map[string]float64{"q": 0.9, "h": 0.5})
	// q above every negative calibrates to 1; constant h to the base rate (1/3), as sklearn fits it.
	if math.Abs(cal["q"]-1) > 1e-9 || math.Abs(cal["h"]-1.0/3) > 1e-9 {
		t.Fatalf("applied %v", cal)
	}
	// The newest labelled decision's model is the default.
	if _, sum, _ := Fit(st, Options{Now: now}); len(sum.ModelVersions) != 1 {
		t.Fatalf("default model: %+v", sum.ModelVersions)
	}
	if _, _, err := Fit(st, Options{Now: now, Label: "approved"}); err == nil {
		t.Fatal("unknown label accepted")
	}
	if d, err := ParseAge("28d"); err != nil || d != 28*24*time.Hour {
		t.Fatal("ParseAge")
	}
	_ = os.Remove(path)
}
