package server

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// The gateway wires the store's degraded set into the gate: after 200 decisions whose raw
// needs_human_review sits far from the calibration's fit_stats, rules reading it give on_drift.
func TestGatewayDriftMonitor(t *testing.T) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "policy.yaml")
	_ = os.WriteFile(pp, []byte(testRules+"drift: {enabled: true}\n"), 0o644)
	p, err := engine.LoadPolicy(pp)
	if err != nil {
		t.Fatal(err)
	}
	cp := filepath.Join(dir, "cal.json")
	_ = os.WriteFile(cp, []byte(`{"fit_stats": {"needs_human_review": {"mean": 0.1, "yes_rate": 0.05, "spread": 0.1, "n": 1000},
		"is_logic_change": {"mean": 0.9, "yes_rate": 1, "spread": 0, "n": 1000}}}`), 0o644)
	cal, err := engine.LoadCalibration(cp)
	if err != nil {
		t.Fatal(err)
	}
	st, _ := store.Open(filepath.Join(dir, "gatelog.jsonl"))
	gw := New(&engine.Gate{Policy: p, Model: testModel, Cal: cal}, st, nil, 0, "")
	t.Cleanup(gw.Stop)

	if r := gw.Decide("o/r", 1, "s0", "hold", nil, "api"); r.Rule != "maintainer-judgement" || r.Drift != nil {
		t.Fatalf("before drift: %+v", r)
	}
	for i := 1; i < engine.R8DriftMinN; i++ {
		gw.Decide("o/r", 1, fmt.Sprintf("s%d", i), "hold", nil, "api")
	}
	r := gw.Decide("o/r", 1, "last", "hold", nil, "api")
	if r.Rule != "on_drift" || r.Reason != "drift:needs_human_review" || r.Verdict != "hold" || r.Route != "human" {
		t.Fatalf("after drift: %+v", r)
	}
	if d := StatusDescription(r); !strings.HasSuffix(d, " · drift: needs_human_review") {
		t.Fatalf("status description %q", d)
	}
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("GET", "/stats", nil))
	var stats struct {
		Drift map[string]engine.ModelDrift `json:"drift"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &stats); err != nil {
		t.Fatal(err)
	}
	md := stats.Drift["keyed-1"]
	if md.Reference != "fit_stats" || len(md.Degraded) != 1 || md.Questions["needs_human_review"].N != engine.R8DriftMinN+1 {
		t.Fatalf("/stats drift block %+v", md)
	}
}

func TestStatusDescriptionKeepsDriftSuffix(t *testing.T) {
	d := StatusDescription(engine.Result{Verdict: "hold", Rule: "r", Reason: strings.Repeat("é", 300), Drift: []string{"a", "b"}})
	if n := len([]rune(d)); n != 140 || !strings.HasSuffix(d, "… · drift: a,b") {
		t.Fatalf("len %d: %q", n, d)
	}
}
