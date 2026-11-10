package server

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// In-flight decisions and the daily budget (v0.6).
//
// One decision per pull request runs at a time. When a newer head of the same PR arrives — the webhook for the
// push, or the job that decides it — the decision in flight for the older head is cancelled: its model calls are
// abandoned, nothing is recorded for it and nothing is written to GitHub. A second job for the head already in
// flight is dropped; the first one records the verdict. Jobs without a PR number are not tracked.

type flight struct {
	sha    string
	cancel context.CancelFunc
}

type flights struct {
	mu sync.Mutex
	m  map[string]*flight
}

func flightKey(repo string, pr int) string { return fmt.Sprintf("%s#%d", repo, pr) }

// begin registers a decision for (repo, pr, sha). ok is false when that head is already being decided. A
// decision in flight for another head of the same PR is cancelled. The returned func ends the registration.
func (f *flights) begin(repo string, pr int, sha string) (ctx context.Context, end func(), ok bool, superseded string) {
	if pr <= 0 || sha == "" {
		return context.Background(), func() {}, true, ""
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.m == nil {
		f.m = map[string]*flight{}
	}
	k := flightKey(repo, pr)
	if cur := f.m[k]; cur != nil {
		if cur.sha == sha {
			return nil, nil, false, ""
		}
		cur.cancel()
		superseded = cur.sha
	}
	ctx, cancel := context.WithCancel(context.Background())
	fl := &flight{sha: sha, cancel: cancel}
	f.m[k] = fl
	return ctx, func() {
		cancel()
		f.mu.Lock()
		if f.m[k] == fl {
			delete(f.m, k)
		}
		f.mu.Unlock()
	}, true, superseded
}

// supersede cancels the decision in flight for (repo, pr) unless it is deciding sha; "" cancels whatever runs
// (the PR was closed). It returns the head whose decision was cancelled, or "".
func (f *flights) supersede(repo string, pr int, sha string) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	cur := f.m[flightKey(repo, pr)]
	if cur == nil || (sha != "" && cur.sha == sha) {
		return ""
	}
	cur.cancel()
	return cur.sha
}

// spend tracks model cost per UTC day for the budget block.
type spend struct {
	mu    sync.Mutex
	day   string
	total float64
	repo  map[string]float64
	now   func() time.Time
}

func newSpend(seed map[string]float64) *spend {
	s := &spend{repo: map[string]float64{}, now: time.Now}
	s.day = s.now().UTC().Format("2006-01-02")
	for r, c := range seed {
		s.repo[r] += c
		s.total += c
	}
	return s
}

func (s *spend) roll() {
	if d := s.now().UTC().Format("2006-01-02"); d != s.day {
		s.day, s.total, s.repo = d, 0, map[string]float64{}
	}
}

func (s *spend) add(repo string, usd float64) {
	if usd <= 0 {
		return
	}
	s.mu.Lock()
	s.roll()
	s.total += usd
	s.repo[repo] += usd
	s.mu.Unlock()
}

// exceeded reports which ceiling of b is reached for repo ("" = none), with the spend and the ceiling.
func (s *spend) exceeded(b engine.BudgetSpec, repo string) (which string, spent, limit float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.roll()
	if b.DailyUSD > 0 && s.total >= b.DailyUSD {
		return "daily_usd", s.total, b.DailyUSD
	}
	if b.PerRepoDailyUSD > 0 && s.repo[repo] >= b.PerRepoDailyUSD {
		return "per_repo_daily_usd", s.repo[repo], b.PerRepoDailyUSD
	}
	return "", 0, 0
}

func (s *spend) snapshot() map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.roll()
	return map[string]any{"day": s.day, "spent_usd": s.total}
}
