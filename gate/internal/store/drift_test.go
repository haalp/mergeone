package store

import (
	"fmt"
	"math"
	"path/filepath"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func rec(i int, ts time.Time, model string, p float64) Record {
	return Record{Repo: "o/r", PR: i, HeadSHA: fmt.Sprintf("sha%d-%d", i, ts.Unix()), Result: engine.Result{Verdict: "pass",
		RawProbs: map[string]float64{"q": p, "h": 0.5}, ModelVersion: model, InputSHA256: fmt.Sprint(i), TS: ts}}
}

func TestDriftAggregatesFromRecordAndReopen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log.jsonl")
	s, _ := Open(path)
	now := time.Date(2026, 10, 25, 12, 0, 0, 0, time.UTC)
	// 40 days: reference days (8..35 days ago) answer q = 0.2, the last 7 days answer q = 0.6.
	n := 0
	for d := 39; d >= 0; d-- {
		p := 0.2
		if d < 7 {
			p = 0.6
		}
		for k := 0; k < 40; k++ {
			n++
			_ = s.Record(rec(n, now.Add(-time.Duration(d)*24*time.Hour), "m1", p))
		}
	}
	// An error and a re-record of a cached verdict are not counted.
	_ = s.Record(Record{Repo: "o/r", HeadSHA: "e", Result: engine.Result{Error: "boom", RawProbs: map[string]float64{"q": 1}, ModelVersion: "m1", TS: now}})
	last := rec(n, now.Add(-0*24*time.Hour), "m1", 0.6)
	_ = s.Record(last)
	check := func(s *Store) {
		md := s.Drift(now)["m1"]
		q := md.Questions["q"]
		if md.Reference != "gatelog" || q.N != 280 || q.RefN != 28*40 || math.Abs(q.Shift-0.4) > 1e-9 || q.YesRate != 1 || !q.Degraded {
			t.Fatalf("drift %+v %+v", md.Reference, q)
		}
		if h := md.Questions["h"]; h.Degraded || h.Shift != 0 || h.Spread != 0 {
			t.Fatalf("healthy %+v", h)
		}
		if deg := s.DegradedQuestions(now); !deg["q"] || deg["h"] {
			t.Fatalf("degraded set %v", deg)
		}
		if _, ok := s.Stats(time.Hour)["drift"]; !ok {
			t.Fatal("Stats has no drift block")
		}
	}
	check(s)
	s2, _ := Open(path) // rebuilt from the gatelog
	check(s2)
}

func TestDriftFitStatsReferenceWhileYoung(t *testing.T) {
	s, _ := Open(filepath.Join(t.TempDir(), "log.jsonl"))
	now := time.Now().UTC()
	for i := 0; i < 250; i++ {
		_ = s.Record(rec(i, now.Add(-time.Duration(i%5)*24*time.Hour), "m", 0.9))
	}
	if md := s.Drift(now)["m"]; md.Reference != "none" || len(md.Degraded) != 0 {
		t.Fatalf("no reference: %+v", md)
	}
	s.SetFitStats(map[string]engine.QStats{"q": {Mean: 0.3, Spread: 0.2, N: 1000}, "h": {Mean: 0.5, Spread: 0, N: 1000}})
	md := s.Drift(now)["m"]
	if md.Reference != "fit_stats" || len(md.Degraded) != 1 || md.Degraded[0] != "q" {
		t.Fatalf("fit_stats reference: %+v", md)
	}
}

// A map refit from the gatelog inside the last 35 days is the reference even for an old gatelog, so the refit
// clears the flag it answered; a shipped map's fit_stats (no refit time) are not, once the log is 35 days old.
func TestDriftRefitReference(t *testing.T) {
	s, _ := Open(filepath.Join(t.TempDir(), "log.jsonl"))
	now := time.Date(2026, 10, 25, 12, 0, 0, 0, time.UTC)
	n := 0
	for d := 39; d >= 0; d-- {
		p := 0.2
		if d < 7 {
			p = 0.6
		}
		for k := 0; k < 40; k++ {
			n++
			_ = s.Record(rec(n, now.Add(-time.Duration(d)*24*time.Hour), "m1", p))
		}
	}
	fs := map[string]engine.QStats{"q": {Mean: 0.6, Spread: 0, YesRate: 1, N: 300}, "h": {Mean: 0.5, N: 300}}
	s.SetFitStats(fs)
	if md := s.Drift(now)["m1"]; md.Reference != "gatelog" || len(md.Degraded) != 1 {
		t.Fatalf("shipped fit_stats on an old log: %+v", md)
	}
	s.SetFitStatsAt(fs, now.Add(-2*24*time.Hour))
	if md := s.Drift(now)["m1"]; md.Reference != "fit_stats" || len(md.Degraded) != 0 {
		t.Fatalf("refit reference: %+v", md)
	}
	s.SetFitStatsAt(fs, now.Add(-40*24*time.Hour))
	if md := s.Drift(now)["m1"]; md.Reference != "gatelog" {
		t.Fatalf("an old refit is not the reference: %+v", md.Reference)
	}
}
