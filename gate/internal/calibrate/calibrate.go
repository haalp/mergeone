// Package calibrate refits the isotonic calibration maps from the gatelog (`mergeone calibrate
// --from-gatelog`, POST /api/calibrate; docs/drift-monitor.md "Recalibration"). Per question it fits raw
// answers against an outcome label of closed PRs with the same isotonic regression `gate fit-calibration`
// uses, and writes the JSON shape the engine loads plus `fit_stats` (the drift monitor's reference) and
// `fit_meta`. It never runs on its own and never changes the map a running gateway uses: the new file takes
// effect when it is put where the policy loads its map and the gateway restarts.
package calibrate

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// MinLabelled is the fewest labelled decisions a refit accepts (the drift monitor's n, R8DriftMinN).
const MinLabelled = engine.R8DriftMinN

// Options select the decisions a refit uses.
type Options struct {
	Since time.Duration // decisions made in the trailing window (default 28 days)
	Model string        // model version, or a registry name matching "<name>-*"; "" = the newest labelled decision's version
	Label string        // merged | reverted | iterated (default iterated)
	Now   time.Time     // default time.Now()
}

// File is calibration_<model>_<qset>.json.
type File struct {
	Maps     map[string]engine.QuestionMap
	FitStats map[string]engine.QStats
	Meta     engine.FitMeta
}

// MarshalJSON writes the flat shape the engine (and gatebench's Calibrator) loads: {question: {x, y, n,
// positives}, ..., "fit_stats": {...}, "fit_meta": {...}}.
func (f File) MarshalJSON() ([]byte, error) {
	m := make(map[string]any, len(f.Maps)+2)
	for q, v := range f.Maps {
		m[q] = v
	}
	m["fit_stats"] = f.FitStats
	m["fit_meta"] = f.Meta
	return json.Marshal(m)
}

// QuestionSummary is one question's line in the fit summary.
type QuestionSummary struct {
	N         int     `json:"n"`
	Positives int     `json:"positives"`
	Mean      float64 `json:"mean"`
	YesRate   float64 `json:"yes_rate"`
	Spread    float64 `json:"spread"`
	Fitted    bool    `json:"fitted"` // false: one class in the labels, no map written for this question
}

// Summary is what calibrate reports (and POST /api/calibrate returns).
type Summary struct {
	Label         string                     `json:"label"`
	Since         time.Time                  `json:"since"`
	FitAt         time.Time                  `json:"fit_at"`
	ModelVersions []string                   `json:"model_versions"`
	N             int                        `json:"n"`
	Positives     int                        `json:"positives"`
	Questions     map[string]QuestionSummary `json:"questions"`
	File          string                     `json:"file,omitempty"`
}

// ErrTooFew is returned (wrapped) when fewer than MinLabelled decisions have the label.
var ErrTooFew = errors.New("too few labelled decisions")

// ParseAge reads "28d", "36h", "3600" (seconds) or a Go duration.
func ParseAge(v string) (time.Duration, error) {
	if n, ok := strings.CutSuffix(v, "d"); ok {
		d, err := strconv.Atoi(n)
		if err != nil || d <= 0 {
			return 0, fmt.Errorf("bad age %q", v)
		}
		return time.Duration(d) * 24 * time.Hour, nil
	}
	if n, err := strconv.Atoi(v); err == nil && n > 0 {
		return time.Duration(n) * time.Second, nil
	}
	d, err := time.ParseDuration(v)
	if err != nil || d <= 0 {
		return 0, fmt.Errorf("bad age %q", v)
	}
	return d, nil
}

func matchModel(version, want string) bool {
	return version == want || strings.HasPrefix(version, want+"-")
}

// Fit refits every question from the gatelog's labelled decisions. It refuses (ErrTooFew) under MinLabelled.
func Fit(st *store.Store, opt Options) (File, Summary, error) {
	if opt.Since <= 0 {
		opt.Since = 28 * 24 * time.Hour
	}
	if opt.Label == "" {
		opt.Label = engine.R8SlowLabel
	}
	if !store.OutcomeLabels[opt.Label] {
		return File{}, Summary{}, fmt.Errorf("label %q: want merged, reverted or iterated", opt.Label)
	}
	if opt.Now.IsZero() {
		opt.Now = time.Now().UTC()
	}
	since := opt.Now.Add(-opt.Since)
	all := st.Labelled(opt.Label, since, "")
	model := opt.Model
	if model == "" && len(all) > 0 {
		r := all[len(all)-1].Record.Result
		model = r.ModelVersion
		if model == "" {
			model = "unknown"
		}
	}
	versions := map[string]bool{}
	samples := map[string]engine.Labelled{}
	aggs := map[string]*engine.DriftAgg{}
	n, pos := 0, 0
	for _, lr := range all {
		v := lr.Record.Result.ModelVersion
		if v == "" {
			v = "unknown"
		}
		if !matchModel(v, model) {
			continue
		}
		versions[v] = true
		n++
		if lr.Y {
			pos++
		}
		for q, p := range lr.Record.Result.RawProbs {
			l := samples[q]
			l.Add(lr.Y, p)
			samples[q] = l
			if aggs[q] == nil {
				aggs[q] = &engine.DriftAgg{}
			}
			aggs[q].Add(p)
		}
	}
	sum := Summary{Label: opt.Label, Since: since, FitAt: opt.Now, ModelVersions: sortedSet(versions), N: n, Positives: pos,
		Questions: map[string]QuestionSummary{}}
	if model == "" {
		model = "any model"
	}
	if n < MinLabelled {
		return File{}, sum, fmt.Errorf("%w: %d decisions of %s since %s have an outcome with %q; need at least %d. "+
			"Outcomes come from closed PRs: the webhook's closed event (merged, iterated) or POST /api/outcome from CI",
			ErrTooFew, n, model, since.Format("2006-01-02"), opt.Label, MinLabelled)
	}
	f := File{Maps: map[string]engine.QuestionMap{}, FitStats: map[string]engine.QStats{},
		Meta: engine.FitMeta{Source: "gatelog", Label: opt.Label, Since: since, FitAt: opt.Now, N: n, ModelVersions: sum.ModelVersions}}
	for q, l := range samples {
		qs := aggs[q].Stats()
		f.FitStats[q] = qs
		p := 0
		for _, y := range l.Y {
			if y {
				p++
			}
		}
		m, ok := engine.FitQuestionMap(l)
		if ok {
			f.Maps[q] = m
		}
		sum.Questions[q] = QuestionSummary{N: len(l.P), Positives: p, Mean: qs.Mean, YesRate: qs.YesRate, Spread: qs.Spread, Fitted: ok}
	}
	return f, sum, nil
}

// Write writes f to path; an existing file is replaced only with force.
func Write(path string, f File, force bool) error {
	if _, err := os.Stat(path); err == nil && !force {
		return fmt.Errorf("%s exists (it may be the map the gateway loads); pass --force to replace it, or --out elsewhere", path)
	}
	b, err := json.MarshalIndent(f, "", " ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(b, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func sortedSet(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
