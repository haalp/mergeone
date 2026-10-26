package engine

import (
	"math"
	"sort"
)

// Drift-monitor thresholds, pre-registered in PROTOCOL.md §R8 (C5, R8.4) and docs/drift-monitor.md.
// They may be moved only by a run. The slow signal (AUC vs outcome fell > 0.05) is in slow.go.
const (
	R8DriftShift        = 0.20 // |mean_raw(current) − mean_raw(reference)| above this is drift
	R8DriftSpreadFactor = 0.5  // spread (std of raw) at or below this fraction of the reference is drift
	R8DriftMinN         = 200  // fewer decisions than this in the current window never flag
	R8DriftCurrentDays  = 7    // current window: trailing 7 days
	R8DriftRefDays      = 28   // reference window: the 28 days before the current one
)

// DriftAgg is the per-(model, question) sufficient statistic over raw probabilities.
type DriftAgg struct {
	Sum   float64 `json:"sum"`
	SumSq float64 `json:"sumsq"`
	Yes   int     `json:"yes"` // raw >= 0.5
	N     int     `json:"n"`
}

// Add folds one raw probability in (NaN is ignored).
func (a *DriftAgg) Add(p float64) {
	if math.IsNaN(p) {
		return
	}
	a.Sum += p
	a.SumSq += p * p
	if p >= 0.5 {
		a.Yes++
	}
	a.N++
}

// Merge adds b into a.
func (a *DriftAgg) Merge(b DriftAgg) {
	a.Sum += b.Sum
	a.SumSq += b.SumSq
	a.Yes += b.Yes
	a.N += b.N
}

// Stats returns mean, yes-rate and (population) standard deviation.
func (a DriftAgg) Stats() QStats {
	if a.N == 0 {
		return QStats{}
	}
	n := float64(a.N)
	m := a.Sum / n
	return QStats{Mean: m, YesRate: float64(a.Yes) / n, Spread: math.Sqrt(math.Max(0, a.SumSq/n-m*m)), N: a.N}
}

// QStats summarises one question's raw answers; it is also the calibration file's fit_stats entry.
type QStats struct {
	Mean    float64 `json:"mean"`
	YesRate float64 `json:"yes_rate"`
	Spread  float64 `json:"spread"`
	N       int     `json:"n"`
}

// QuestionDrift is one question's row in the drift block.
type QuestionDrift struct {
	MeanRaw    float64 `json:"mean_raw"`
	Shift      float64 `json:"shift"`
	YesRate    float64 `json:"yes_rate"`
	Spread     float64 `json:"spread"`
	N          int     `json:"n"`
	RefMeanRaw float64 `json:"ref_mean_raw"`
	RefSpread  float64 `json:"ref_spread"`
	RefN       int     `json:"ref_n"`
	Degraded   bool    `json:"degraded"`
}

// CompareDrift applies the fast signal: degraded when n >= R8DriftMinN and the mean moved more than
// R8DriftShift or the spread fell to half the reference or less. No reference (RefN 0) never flags.
func CompareDrift(cur, ref QStats) QuestionDrift {
	d := QuestionDrift{MeanRaw: cur.Mean, YesRate: cur.YesRate, Spread: cur.Spread, N: cur.N,
		RefMeanRaw: ref.Mean, RefSpread: ref.Spread, RefN: ref.N}
	if ref.N == 0 || cur.N == 0 {
		return d
	}
	d.Shift = math.Abs(cur.Mean - ref.Mean)
	halved := ref.Spread > 0 && cur.Spread <= ref.Spread*R8DriftSpreadFactor
	d.Degraded = cur.N >= R8DriftMinN && (d.Shift > R8DriftShift || halved)
	return d
}

// ModelDrift is one model's drift block: every question seen in the current window or the reference.
type ModelDrift struct {
	Reference string                   `json:"reference"` // gatelog | fit_stats | none
	Questions map[string]QuestionDrift `json:"questions"`
	Degraded  []string                 `json:"degraded"` // fast signal
	// Slow is the AUC-vs-outcome signal, present when the gatelog has outcomes for this model (or a replay
	// was given labels). Its degraded questions are acted on like the fast signal's.
	Slow *SlowDrift `json:"slow,omitempty"`
}

// AllDegraded is the union of the fast and slow degraded questions, sorted.
func (md ModelDrift) AllDegraded() []string {
	set := map[string]bool{}
	for _, q := range md.Degraded {
		set[q] = true
	}
	if md.Slow != nil {
		for _, q := range md.Slow.Degraded {
			set[q] = true
		}
	}
	return sortedKeys(set)
}

// DriftReport compares current against reference question by question.
func DriftReport(cur, ref map[string]QStats, reference string) ModelDrift {
	md := ModelDrift{Reference: reference, Questions: map[string]QuestionDrift{}, Degraded: []string{}}
	for q, c := range cur {
		md.Questions[q] = CompareDrift(c, ref[q])
	}
	for q, r := range ref {
		if _, ok := md.Questions[q]; !ok {
			md.Questions[q] = CompareDrift(QStats{}, r)
		}
	}
	for q, d := range md.Questions {
		if d.Degraded {
			md.Degraded = append(md.Degraded, q)
		}
	}
	sort.Strings(md.Degraded)
	return md
}

// DriftOutcome is the policy's on_drift: an outcome, or the string "ignore" (rules run as they are).
type DriftOutcome struct {
	Outcome
	Ignore bool
}

// UnmarshalYAML accepts `on_drift: ignore` or `on_drift: {verdict: hold, route: human}`.
func (d *DriftOutcome) UnmarshalYAML(unmarshal func(any) error) error {
	var s string
	if err := unmarshal(&s); err == nil {
		if s != "ignore" {
			return errBadOnDrift(s)
		}
		d.Ignore = true
		return nil
	}
	return unmarshal(&d.Outcome)
}

type errBadOnDrift string

func (e errBadOnDrift) Error() string {
	return "on_drift " + string(e) + `: want "ignore" or {verdict, route}`
}

// degradedSet is the degraded questions to act on (nil when the monitor is off).
func (g *Gate) degradedSet() map[string]bool {
	if g.Degraded == nil || !g.Policy.Drift.Enabled {
		return nil
	}
	return g.Degraded()
}

// readsDegraded returns the first question of r that is degraded, or "".
func (r *Rule) readsDegraded(degraded map[string]bool) string {
	for _, c := range r.conds {
		if degraded[c.q] {
			return c.q
		}
	}
	return ""
}

func sortedKeys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k, v := range m {
		if v {
			out = append(out, k)
		}
	}
	sort.Strings(out)
	return out
}
