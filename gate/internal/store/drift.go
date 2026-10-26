package store

import (
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// driftLog keeps per-(model, question) daily sums of raw probabilities for the drift monitor
// (docs/drift-monitor.md): a ring of the last current+reference days per model, rebuilt from the
// gatelog on Open like every other index, so a stats call never scans rows.
type driftLog struct {
	days     map[string]map[int64]map[string]*engine.DriftAgg // model → day → question → sums
	first    map[string]int64                                 // model → first day seen (log age)
	last     string                                           // model of the newest decision
	lastDay  int64
	fitStats map[string]engine.QStats
	fitAt    time.Time // when fitStats were fit from the gatelog (zero: a shipped map)
}

const driftKeepDays = engine.R8DriftCurrentDays + engine.R8DriftRefDays

func newDriftLog() driftLog {
	return driftLog{days: map[string]map[int64]map[string]*engine.DriftAgg{}, first: map[string]int64{}}
}

func dayOf(t time.Time) int64 { return t.UTC().Unix() / 86400 }

// add feeds a decision's raw probabilities in. Errors and decisions without raw answers are skipped.
func (d *driftLog) add(r *Record) {
	res := r.Result
	if res.Error != "" || len(res.RawProbs) == 0 {
		return
	}
	ts := res.TS
	if ts.IsZero() {
		ts = r.TS
	}
	model, day := res.ModelVersion, dayOf(ts)
	if model == "" {
		model = "unknown"
	}
	byDay := d.days[model]
	if byDay == nil {
		byDay = map[int64]map[string]*engine.DriftAgg{}
		d.days[model] = byDay
		d.first[model] = day
	}
	if day < d.first[model] {
		d.first[model] = day
	}
	qs := byDay[day]
	if qs == nil {
		qs = map[string]*engine.DriftAgg{}
		byDay[day] = qs
		for k := range byDay { // keep the ring bounded
			if k <= day-driftKeepDays {
				delete(byDay, k)
			}
		}
	}
	for q, p := range res.RawProbs {
		a := qs[q]
		if a == nil {
			a = &engine.DriftAgg{}
			qs[q] = a
		}
		a.Add(p)
	}
	if day >= d.lastDay {
		d.last, d.lastDay = model, day
	}
}

// window sums days [from, to] for one model.
func (d *driftLog) window(model string, from, to int64) map[string]engine.QStats {
	sum := map[string]engine.DriftAgg{}
	for day, qs := range d.days[model] {
		if day < from || day > to {
			continue
		}
		for q, a := range qs {
			s := sum[q]
			s.Merge(*a)
			sum[q] = s
		}
	}
	out := make(map[string]engine.QStats, len(sum))
	for q, a := range sum {
		out[q] = a.Stats()
	}
	return out
}

// model compares the trailing 7 days with the 28 before them, or with the calibration's fit_stats
// while the model's log is younger than 35 days or the map was refit from the gatelog after the reference
// window began (`mergeone calibrate --from-gatelog`: the new fit set is the reference, so a refit clears
// the flag it answered).
func (d *driftLog) model(model string, now time.Time) engine.ModelDrift {
	today := dayOf(now)
	cur := d.window(model, today-engine.R8DriftCurrentDays+1, today)
	refit := len(d.fitStats) > 0 && !d.fitAt.IsZero() && dayOf(d.fitAt) > today-driftKeepDays
	if today-d.first[model]+1 >= driftKeepDays && !refit {
		return engine.DriftReport(cur, d.window(model, today-driftKeepDays+1, today-engine.R8DriftCurrentDays), "gatelog")
	}
	if len(d.fitStats) > 0 {
		return engine.DriftReport(cur, d.fitStats, "fit_stats")
	}
	return engine.DriftReport(cur, nil, "none")
}

// SetFitStats gives the drift monitor the shipped calibration's fit_stats (the reference while the
// gatelog is younger than 35 days).
func (s *Store) SetFitStats(fs map[string]engine.QStats) { s.SetFitStatsAt(fs, time.Time{}) }

// SetFitStatsAt is SetFitStats for a map refit from the gatelog at fitAt: while fitAt is inside the last 35
// days its fit_stats are the reference even for an old gatelog.
func (s *Store) SetFitStatsAt(fs map[string]engine.QStats, fitAt time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.drift.fitStats, s.drift.fitAt = fs, fitAt
}

// Drift is the drift block of Stats: per model, per question, at now.
func (s *Store) Drift(now time.Time) map[string]engine.ModelDrift {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.driftReport(now)
}

// DegradedQuestions is the degraded set (fast or slow signal) for the model of the newest decision (the one
// being served).
func (s *Store) DegradedQuestions(now time.Time) map[string]bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.drift.last == "" {
		return nil
	}
	var out map[string]bool
	for _, q := range s.modelDrift(s.drift.last, now).AllDegraded() {
		if out == nil {
			out = map[string]bool{}
		}
		out[q] = true
	}
	return out
}
