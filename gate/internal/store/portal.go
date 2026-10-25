package store

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// Resolution is a person's answer to a hold or block, made in the portal (POST /api/resolve).
// It is keyed on the head SHA it answers; a new push is a new head and needs a new answer.
// Recording one never merges or closes anything.
type Resolution struct {
	TS       time.Time `json:"ts"`
	Repo     string    `json:"repo"`
	PR       int       `json:"pr"`
	HeadSHA  string    `json:"head_sha"`
	Action   string    `json:"action"` // approve | link_duplicate | request_rebase
	TargetPR int       `json:"target_pr,omitempty"`
	Login    string    `json:"login"`
	// The decision it answers (for agreed/overridden per rule).
	Verdict string                `json:"verdict"`
	Route   string                `json:"route"`
	Rule    string                `json:"rule"`
	Actions *engine.ActionsReport `json:"actions,omitempty"`
}

// ResolveActions are the accepted Resolution.Action values.
var ResolveActions = map[string]bool{"approve": true, "link_duplicate": true, "request_rebase": true}

// Agreed reports whether the person kept the gateway's call: linking a duplicate or asking for a
// rebase keeps the PR held; approving it as is overrides the hold.
func (r Resolution) Agreed() bool { return r.Action != "approve" }

// event is one line of the sidecar log (<gatelog>.events.jsonl): resolutions and PR closures, which
// are not decisions and so stay out of the gatelog.
type event struct {
	Kind       string      `json:"kind"` // resolution | closed
	Resolution *Resolution `json:"resolution,omitempty"`
	Repo       string      `json:"repo,omitempty"`
	PR         int         `json:"pr,omitempty"`
	TS         time.Time   `json:"ts"`
}

func eventsPath(gatelog string) string {
	return strings.TrimSuffix(gatelog, filepath.Ext(gatelog)) + ".events.jsonl"
}

func (s *Store) openEvents() error {
	p := eventsPath(s.path)
	if f, err := os.Open(p); err == nil {
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 16<<20)
		for sc.Scan() {
			var e event
			if json.Unmarshal(sc.Bytes(), &e) == nil {
				s.indexEvent(e)
			}
		}
		f.Close()
	}
	f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	s.ef = f
	return nil
}

func (s *Store) indexEvent(e event) {
	switch e.Kind {
	case "resolution":
		if r := e.Resolution; r != nil {
			s.resolutions = append(s.resolutions, *r)
			s.resolved[r.Repo+"\x00"+r.HeadSHA] = len(s.resolutions) - 1
		}
	case "closed":
		s.closed[e.Repo+"\x00"+itoa(e.PR)] = e.TS
	}
}

// appendEvent writes and indexes one sidecar line. Caller holds s.mu.
func (s *Store) appendEvent(e event) error {
	b, err := json.Marshal(e)
	if err != nil {
		return err
	}
	if _, err := s.ef.Write(append(b, '\n')); err != nil {
		return err
	}
	if err := s.ef.Sync(); err != nil {
		return err
	}
	s.indexEvent(e)
	return nil
}

// Resolve records a resolution (fsync'd, like a verdict).
func (s *Store) Resolve(r Resolution) (Resolution, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if r.TS.IsZero() {
		r.TS = time.Now().UTC()
	}
	return r, s.appendEvent(event{Kind: "resolution", Resolution: &r, TS: r.TS})
}

// ResolutionFor returns the latest resolution of a head SHA, or nil.
func (s *Store) ResolutionFor(repo, sha string) *Resolution {
	s.mu.Lock()
	defer s.mu.Unlock()
	if i, ok := s.resolved[repo+"\x00"+sha]; ok {
		r := s.resolutions[i]
		return &r
	}
	return nil
}

// SetOnRecord installs a hook called (outside the lock) with every record after it is written,
// e.g. the outbound portal sink. It must not block.
func (s *Store) SetOnRecord(f func(Record)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.onRecord = f
}

// isOpen: a PR is open unless it was closed after its latest decision. Caller holds s.mu.
func (s *Store) isOpen(r *Record) bool {
	t, ok := s.closed[r.Repo+"\x00"+itoa(r.PR)]
	return !ok || r.TS.After(t)
}

// NeedsPerson is the queue filter: a hold routed to dedup or human, or any block.
func NeedsPerson(res engine.Result) bool {
	return res.Verdict == "block" || (res.Verdict == "hold" && (res.Route == "dedup" || res.Route == "human"))
}

// Queue is the latest record of every open PR that needs a person (NeedsPerson) and whose head has
// no resolution yet, oldest first, optionally for one repository.
func (s *Store) Queue(repo string) []Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Record
	for _, r := range s.byPR {
		if (repo != "" && r.Repo != repo) || !NeedsPerson(r.Result) || !s.isOpen(r) {
			continue
		}
		if _, done := s.resolved[r.Repo+"\x00"+r.HeadSHA]; done {
			continue
		}
		out = append(out, *r)
	}
	sortOldest(out)
	return out
}

// PRRecords is the latest record of every open PR decided at or after since, optionally for one repo.
func (s *Store) PRRecords(repo string, since time.Time) []Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	var out []Record
	for _, r := range s.byPR {
		if (repo != "" && r.Repo != repo) || r.TS.Before(since) || !s.isOpen(r) {
			continue
		}
		out = append(out, *r)
	}
	sortOldest(out)
	return out
}

func sortOldest(rs []Record) {
	sort.Slice(rs, func(i, j int) bool {
		if !rs[i].TS.Equal(rs[j].TS) {
			return rs[i].TS.Before(rs[j].TS)
		}
		if rs[i].Repo != rs[j].Repo {
			return rs[i].Repo < rs[j].Repo
		}
		return rs[i].PR < rs[j].PR
	})
}

// FindSHA is Latest without a repository: the newest record for sha in any repository.
func (s *Store) FindSHA(sha string) *Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	var best *Record
	for k, r := range s.bySHA {
		if strings.HasSuffix(k, "\x00"+sha) && (best == nil || r.TS.After(best.TS)) {
			best = r
		}
	}
	return best
}

// resolutionStats is the `resolutions` block of Stats for the window, or nil when there are none.
// Caller holds s.mu.
func (s *Store) resolutionStats(t0 time.Time) map[string]any {
	type ag struct {
		Agreed     int `json:"agreed"`
		Overridden int `json:"overridden"`
	}
	total, agreed, approved, linked, rebase := 0, 0, 0, 0, 0
	byRule := map[string]*ag{}
	for _, r := range s.resolutions {
		if r.TS.Before(t0) {
			continue
		}
		total++
		switch r.Action {
		case "approve":
			approved++
		case "link_duplicate":
			linked++
		case "request_rebase":
			rebase++
		}
		a := byRule[r.Rule]
		if a == nil {
			a = &ag{}
			byRule[r.Rule] = a
		}
		if r.Agreed() {
			agreed++
			a.Agreed++
		} else {
			a.Overridden++
		}
	}
	if total == 0 {
		return nil
	}
	return map[string]any{"total": total, "agreed": agreed, "approved_as_is": approved, "linked": linked,
		"rebase_requested": rebase, "by_rule": byRule}
}

// DailyRow is one day of history.daily in Stats.
type DailyRow struct {
	Day       string  `json:"day"`
	Decisions int     `json:"decisions"`
	Pass      int     `json:"pass"`
	Hold      int     `json:"hold"`
	Block     int     `json:"block"`
	CostUSD   float64 `json:"cost_usd"`
}

type dailyAcc map[string]*DailyRow

func (d dailyAcc) add(r *Record) {
	day := r.TS.UTC().Format("2006-01-02")
	row := d[day]
	if row == nil {
		row = &DailyRow{Day: day}
		d[day] = row
	}
	row.Decisions++
	row.CostUSD += r.Result.CostUSD
	switch r.Result.Verdict {
	case "pass":
		row.Pass++
	case "hold":
		row.Hold++
	case "block":
		row.Block++
	}
}

func (d dailyAcc) rows() []DailyRow {
	out := make([]DailyRow, 0, len(d))
	for _, r := range d {
		out = append(out, *r)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Day < out[j].Day })
	return out
}
