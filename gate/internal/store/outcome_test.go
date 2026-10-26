package store

import (
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

func bp(b bool) *bool { return &b }

// The slow signal sees what the fast one cannot: q keeps its mean and spread but loses its ranking against
// `iterated` (AUC 1 → 0); h answers 0.5 throughout (AUC 0.5 both windows).
func TestSlowSignalFromOutcomes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "log.jsonl")
	s, _ := Open(path)
	now := time.Date(2026, 10, 25, 12, 0, 0, 0, time.UTC)
	add := func(pr, daysAgo int, y, anti bool) {
		p := 0.1
		if y != anti {
			p = 0.9
		}
		r := rec(pr, now.Add(-time.Duration(daysAgo)*24*time.Hour), "m", p)
		if err := s.Record(r); err != nil {
			t.Fatal(err)
		}
		if _, err := s.RecordOutcome(PROutcome{Repo: "o/r", PR: pr, Iterated: bp(y), Source: "api"}); err != nil {
			t.Fatal(err)
		}
	}
	for i := 0; i < 300; i++ {
		add(1000+i, 8+i%27, i%2 == 0, false)
	}
	for i := 0; i < 250; i++ {
		add(5000+i, i%7, i%2 == 0, true)
	}
	// An outcome without a decision, and a merged-only outcome, carry no iterated label: ignored.
	_, _ = s.RecordOutcome(PROutcome{Repo: "o/r", PR: 9999, Iterated: bp(true)})
	_, _ = s.RecordOutcome(PROutcome{Repo: "o/r", PR: 1000, Merged: bp(true)}) // merges, keeps iterated
	check := func(s *Store) {
		md := s.Drift(now)["m"]
		if len(md.Degraded) != 0 {
			t.Fatalf("fast signal flagged %v (mean and spread are unchanged)", md.Degraded)
		}
		sd := md.Slow
		if sd == nil || !reflect.DeepEqual(sd.Degraded, []string{"q"}) || sd.Label != "iterated" || sd.Reference != "gatelog" {
			t.Fatalf("slow %+v", sd)
		}
		q, h := sd.Questions["q"], sd.Questions["h"]
		if q.N != 250 || q.RefN != 300 || *q.AUC != 0 || *q.RefAUC != 1 || h.Degraded || *h.Delta != 0 {
			t.Fatalf("q %+v h %+v", q, h)
		}
		if deg := s.DegradedQuestions(now); !deg["q"] || deg["h"] {
			t.Fatalf("degraded set %v", deg)
		}
		if o := s.OutcomeFor("o/r", 1000); o == nil || !*o.Merged || !*o.Iterated {
			t.Fatalf("merged outcome %+v", o)
		}
		if got := s.Labelled("iterated", now.Add(-7*24*time.Hour), "m"); len(got) != 250 {
			t.Fatalf("labelled in the last 7 days: %d", len(got))
		}
	}
	check(s)
	s2, _ := Open(path) // rebuilt from the gatelog and the sidecar
	check(s2)
	// Under 200 labelled decisions in the current window nothing flags.
	s3, _ := Open(filepath.Join(t.TempDir(), "log.jsonl"))
	s = s3
	for i := 0; i < 300; i++ {
		add(1000+i, 8+i%27, i%2 == 0, false)
	}
	for i := 0; i < 199; i++ {
		add(5000+i, i%7, i%2 == 0, true)
	}
	if sd := s.Drift(now)["m"].Slow; sd == nil || len(sd.Degraded) != 0 || sd.Questions["q"].N != 199 {
		t.Fatalf("n < 200: %+v", sd)
	}
}
