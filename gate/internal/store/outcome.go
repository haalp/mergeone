package store

import (
	"sort"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// PROutcome is what happened to a PR after the gate decided it: the run-5 outcome labels the slow drift
// signal and `mergeone calibrate --from-gatelog` read (docs/drift-monitor.md). A nil field is unknown.
// Outcomes for one PR merge field by field, the latest non-nil value winning, so the webhook can record
// merged/iterated at close and CI can add reverted later (POST /api/outcome).
type PROutcome struct {
	TS       time.Time `json:"ts"`
	Repo     string    `json:"repo"`
	PR       int       `json:"pr"`
	Merged   *bool     `json:"merged,omitempty"`
	Reverted *bool     `json:"reverted,omitempty"`
	Iterated *bool     `json:"iterated,omitempty"`
	Source   string    `json:"source,omitempty"` // webhook | api
}

// OutcomeLabels are the labels an outcome can carry.
var OutcomeLabels = map[string]bool{"merged": true, "reverted": true, "iterated": true}

// Label returns the named label, or nil when unknown.
func (o PROutcome) Label(name string) *bool {
	switch name {
	case "merged":
		return o.Merged
	case "reverted":
		return o.Reverted
	case "iterated":
		return o.Iterated
	}
	return nil
}

// IteratedHeads is the gateway's proxy for the run-5 `iterated` label (force_pushes >= 2): the PR was decided
// on at least this many distinct head SHAs, i.e. it got two or more new heads after its first decision.
const IteratedHeads = 3

func mergeOutcome(prev *PROutcome, o PROutcome) PROutcome {
	if prev == nil {
		return o
	}
	out := *prev
	out.TS, out.Source = o.TS, o.Source
	if o.Merged != nil {
		out.Merged = o.Merged
	}
	if o.Reverted != nil {
		out.Reverted = o.Reverted
	}
	if o.Iterated != nil {
		out.Iterated = o.Iterated
	}
	return out
}

// RecordOutcome writes an outcome to the sidecar log (fsync'd) and returns the PR's merged outcome.
func (s *Store) RecordOutcome(o PROutcome) (PROutcome, error) {
	if o.TS.IsZero() {
		o.TS = time.Now().UTC()
	}
	if err := s.emit(Event{Kind: "outcome", Outcome: &o, TS: o.TS}); err != nil {
		return PROutcome{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return *s.outcomes[o.Repo+"\x00"+itoa(o.PR)], nil
}

// OutcomeFor returns a PR's merged outcome, or nil.
func (s *Store) OutcomeFor(repo string, pr int) *PROutcome {
	s.mu.Lock()
	defer s.mu.Unlock()
	if o := s.outcomes[repo+"\x00"+itoa(pr)]; o != nil {
		cp := *o
		return &cp
	}
	return nil
}

// DistinctHeads counts the head SHAs a PR was decided on (errors excluded).
func (s *Store) DistinctHeads(repo string, pr int) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.shasPR[repo+"\x00"+itoa(pr)])
}

// LabelledRecord is a PR's last error-free decision with raw answers, joined with one outcome label.
type LabelledRecord struct {
	Record Record
	Y      bool
}

func decidedAt(r *Record) time.Time {
	if !r.Result.TS.IsZero() {
		return r.Result.TS
	}
	return r.TS
}

func modelOf(r *Record) string {
	if r.Result.ModelVersion == "" {
		return "unknown"
	}
	return r.Result.ModelVersion
}

// Labelled returns, oldest first, every PR decided at or after since whose outcome carries label, with its last
// error-free decision; model "" takes every model. Caller does not hold s.mu.
func (s *Store) Labelled(label string, since time.Time, model string) []LabelledRecord {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.labelled(label, since, model)
}

func (s *Store) labelled(label string, since time.Time, model string) []LabelledRecord {
	var out []LabelledRecord
	for k, o := range s.outcomes {
		y := o.Label(label)
		r := s.lastGood[k]
		if y == nil || r == nil || decidedAt(r).Before(since) || (model != "" && modelOf(r) != model) {
			continue
		}
		out = append(out, LabelledRecord{Record: *r, Y: *y})
	}
	sort.Slice(out, func(i, j int) bool {
		a, b := decidedAt(&out[i].Record), decidedAt(&out[j].Record)
		if !a.Equal(b) {
			return a.Before(b)
		}
		return out[i].Record.Repo+itoa(out[i].Record.PR) < out[j].Record.Repo+itoa(out[j].Record.PR)
	})
	return out
}

// slow is the slow drift signal for one model at now: AUC of each question's raw answer against
// engine.R8SlowLabel on the PRs decided in the trailing 7 days vs the 28 days before. nil when the gatelog
// has no labelled decision of this model in either window. Caller holds s.mu.
func (s *Store) slow(model string, now time.Time) *engine.SlowDrift {
	today := dayOf(now)
	curFrom, refFrom := today-engine.R8DriftCurrentDays+1, today-driftKeepDays+1
	cur, ref := map[string]engine.Labelled{}, map[string]engine.Labelled{}
	seen := false
	for k, o := range s.outcomes {
		y := o.Label(engine.R8SlowLabel)
		r := s.lastGood[k]
		if y == nil || r == nil || modelOf(r) != model {
			continue
		}
		day := dayOf(decidedAt(r))
		var side map[string]engine.Labelled
		switch {
		case day >= curFrom && day <= today:
			side = cur
		case day >= refFrom && day < curFrom:
			side = ref
		default:
			continue
		}
		seen = true
		for q, p := range r.Result.RawProbs {
			l := side[q]
			l.Add(*y, p)
			side[q] = l
		}
	}
	if !seen {
		return nil
	}
	sd := engine.SlowReport(cur, ref, engine.R8SlowLabel, "gatelog")
	return &sd
}

// modelDrift is the fast report plus the slow signal. Caller holds s.mu.
func (s *Store) modelDrift(model string, now time.Time) engine.ModelDrift {
	md := s.drift.model(model, now)
	md.Slow = s.slow(model, now)
	return md
}

// driftReport is the drift block: every model with raw answers in the ring. Caller holds s.mu.
func (s *Store) driftReport(now time.Time) map[string]engine.ModelDrift {
	out := map[string]engine.ModelDrift{}
	for m := range s.drift.days {
		out[m] = s.modelDrift(m, now)
	}
	return out
}
