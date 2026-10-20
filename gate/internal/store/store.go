// Package store is the gateway's durable state with zero external dependencies:
// an append-only JSONL gatelog on disk plus in-memory indexes rebuilt on open.
//
// Why not SQLite: the single-binary goal and a toolchain with no CGO. At
// 30,000 decisions/day the gatelog grows ~15 MB/day and replays in well under
// a second per month of history; rotate it with `gate log rotate` when it gets
// large. Multi-node deployments should put a real database behind this
// interface instead.
package store

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"sync"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// Record is one gatelog line.
type Record struct {
	TS      time.Time      `json:"ts"`
	Repo    string         `json:"repo"`
	PR      int            `json:"pr,omitempty"`
	HeadSHA string         `json:"head_sha"`
	Source  string         `json:"source"`
	Result  engine.Result  `json:"result"`
	Context map[string]any `json:"context,omitempty"`
	State   string         `json:"state,omitempty"` // the exact input; the audit trail
}

// OpenPR is what the planner sees.
type OpenPR struct {
	PR      int       `json:"pr"`
	HeadSHA string    `json:"head_sha"`
	Paths   []string  `json:"paths"`
	Updated time.Time `json:"updated"`
}

// Store is safe for concurrent use from one process.
type Store struct {
	mu      sync.Mutex
	path    string
	f       *os.File
	bySHA   map[string]*Record // repo + "\x00" + head_sha → latest
	byPR    map[string]*Record // repo + "\x00" + pr
	recent  []*Record          // ring for stats (last 100k)
	open    map[string]map[int]OpenPR
	jobs    map[string]*Job
	queue   []string
	cond    *sync.Cond
	nextJob int64
	shasPR  map[string]map[string]string // repo + "\x00" + pr → head_sha → latest non-error verdict (max_rounds)
}

// Job is an idempotent unit of work keyed by repo:pr:head_sha.
type Job struct {
	ID       int64          `json:"id"`
	Key      string         `json:"key"`
	Status   string         `json:"status"` // queued | running | done | failed
	Payload  map[string]any `json:"payload"`
	Attempts int            `json:"attempts"`
	LastErr  string         `json:"last_error,omitempty"`
	TS       time.Time      `json:"ts"`
}

const maxRecent = 100_000

// Open loads (or creates) the gatelog at path and rebuilds the indexes.
func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	s := &Store{path: path, bySHA: map[string]*Record{}, byPR: map[string]*Record{}, shasPR: map[string]map[string]string{}, open: map[string]map[int]OpenPR{}, jobs: map[string]*Job{}}
	s.cond = sync.NewCond(&s.mu)
	if f, err := os.Open(path); err == nil {
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 1<<20), 64<<20)
		for sc.Scan() {
			var r Record
			if json.Unmarshal(sc.Bytes(), &r) == nil {
				s.index(&r)
			}
		}
		f.Close()
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return nil, err
	}
	s.f = f
	return s, nil
}

func (s *Store) index(r *Record) {
	s.bySHA[r.Repo+"\x00"+r.HeadSHA] = r
	if r.PR != 0 {
		s.byPR[r.Repo+"\x00"+itoa(r.PR)] = r
		if r.HeadSHA != "" && r.Result.Error == "" {
			k := r.Repo + "\x00" + itoa(r.PR)
			if s.shasPR[k] == nil {
				s.shasPR[k] = map[string]string{}
			}
			s.shasPR[k][r.HeadSHA] = r.Result.Verdict
		}
	}
	s.recent = append(s.recent, r)
	if len(s.recent) > maxRecent {
		s.recent = s.recent[len(s.recent)-maxRecent:]
	}
}

// Record appends a decision to the gatelog (fsync'd per write: a verdict must
// not be lost on crash) and indexes it.
func (s *Store) Record(r Record) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if r.TS.IsZero() {
		r.TS = time.Now().UTC()
	}
	b, err := json.Marshal(r)
	if err != nil {
		return err
	}
	if _, err := s.f.Write(append(b, '\n')); err != nil {
		return err
	}
	if err := s.f.Sync(); err != nil {
		return err
	}
	s.index(&r)
	return nil
}

// Latest returns the newest decision for a head sha, or nil.
func (s *Store) Latest(repo, sha string) *Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.bySHA[repo+"\x00"+sha]
}

// LatestForPR returns the newest decision for a PR number, or nil.
func (s *Store) LatestForPR(repo string, pr int) *Record {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.byPR[repo+"\x00"+itoa(pr)]
}

// NonPassRounds counts a PR's earlier rounds for max_rounds: distinct head shas whose latest non-error
// verdict is not pass, excluding exceptSHA (the head being decided). Counting shas, not records, keeps
// re-decisions, re-acts and worker retries of one head from inflating the count; errors are not rounds.
func (s *Store) NonPassRounds(repo string, pr int, exceptSHA string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	n := 0
	for sha, v := range s.shasPR[repo+"\x00"+itoa(pr)] {
		if sha != exceptSHA && v != "pass" {
			n++
		}
	}
	return n
}

// Stats summarises decisions in the trailing window.
func (s *Store) Stats(window time.Duration) map[string]any {
	s.mu.Lock()
	defer s.mu.Unlock()
	t0 := time.Now().Add(-window)
	verdicts, routes, rules := map[string]int{}, map[string]int{}, map[string]int{}
	var lat []float64
	var cost float64
	n, errs := 0, 0
	for _, r := range s.recent {
		if r.TS.Before(t0) {
			continue
		}
		n++
		verdicts[r.Result.Verdict]++
		routes[r.Result.Route]++
		rules[r.Result.Rule]++
		cost += r.Result.CostUSD
		if r.Result.Error != "" {
			errs++
		} else if r.Result.LatencyMS > 0 {
			lat = append(lat, r.Result.LatencyMS)
		}
	}
	sort.Float64s(lat)
	out := map[string]any{"window_s": window.Seconds(), "decisions": n, "per_hour": float64(n) / window.Hours(),
		"verdicts": verdicts, "routes": routes, "rules": rules, "cost_usd": cost, "error_rate": 0.0, "p50_ms": nil, "p99_ms": nil}
	if n > 0 {
		out["error_rate"] = float64(errs) / float64(n)
	}
	if len(lat) > 0 {
		out["p50_ms"] = lat[len(lat)/2]
		out["p99_ms"] = lat[min(int(float64(len(lat))*0.99), len(lat)-1)]
	}
	return out
}

// ---- jobs

// Enqueue adds a job unless its key already exists. Returns false on duplicate.
func (s *Store) Enqueue(key string, payload map[string]any) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, dup := s.jobs[key]; dup {
		return false
	}
	s.nextJob++
	s.jobs[key] = &Job{ID: s.nextJob, Key: key, Status: "queued", Payload: payload, TS: time.Now().UTC()}
	s.queue = append(s.queue, key)
	s.cond.Signal()
	return true
}

// Claim blocks until a queued job is available (or stop is closed) and marks it running.
func (s *Store) Claim(stop <-chan struct{}) *Job {
	s.mu.Lock()
	defer s.mu.Unlock()
	for len(s.queue) == 0 {
		select {
		case <-stop:
			return nil
		default:
		}
		s.cond.Wait()
	}
	key := s.queue[0]
	s.queue = s.queue[1:]
	j := s.jobs[key]
	j.Status = "running"
	j.Attempts++
	return j
}

// Finish marks a job done, or re-queues it up to maxAttempts.
func (s *Store) Finish(j *Job, ok bool, errMsg string, maxAttempts int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if ok {
		j.Status = "done"
		return
	}
	j.LastErr = errMsg
	if j.Attempts >= maxAttempts {
		j.Status = "failed"
		return
	}
	j.Status = "queued"
	s.queue = append(s.queue, j.Key)
	s.cond.Signal()
}

// Wake lets workers exit when the gateway stops.
func (s *Store) Wake() { s.mu.Lock(); s.cond.Broadcast(); s.mu.Unlock() }

// QueueDepth counts jobs by status.
func (s *Store) QueueDepth() map[string]int {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := map[string]int{}
	for _, j := range s.jobs {
		out[j.Status]++
	}
	return out
}

// ---- swarm

func (s *Store) UpsertOpenPR(repo string, pr int, sha string, paths []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.open[repo] == nil {
		s.open[repo] = map[int]OpenPR{}
	}
	s.open[repo][pr] = OpenPR{PR: pr, HeadSHA: sha, Paths: paths, Updated: time.Now().UTC()}
}

func (s *Store) ClosePR(repo string, pr int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.open[repo], pr)
}

// OpenPRs lists a repo's open PRs oldest first.
func (s *Store) OpenPRs(repo string) []OpenPR {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]OpenPR, 0, len(s.open[repo]))
	for _, p := range s.open[repo] {
		out = append(out, p)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Updated.Before(out[j].Updated) })
	return out
}

func itoa(i int) string { return strconv.Itoa(i) }
