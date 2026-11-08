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
	"strings"
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
	// PatchID (v0.4) is the stable id of the PR's patch (internal/patchid): equal across rebases of the same
	// change. "" when the gateway saw no diff. "same patch_id, different head_sha" is a rebase, measurable here.
	PatchID string `json:"patch_id,omitempty"`
	// PolicyHash (v0.4) is engine.Gate.PolicyHash at decision time: verdict reuse copies only within one hash.
	PolicyHash string `json:"policy_hash,omitempty"`
	// ReusedFrom (v0.4) is the head sha whose verdict this record copies (same patch_id, policy_hash and
	// served model version; no model call was made). Reused records are not counted by the drift monitor.
	ReusedFrom string `json:"reused_from,omitempty"`
	// DebouncedSHAs (v0.4) are the heads of this PR that were pushed during the debounce window and never
	// decided, because a later push superseded them before the timer fired.
	DebouncedSHAs []string `json:"debounced_shas,omitempty"`
	// Shadow (v0.5) marks a decision made under `mode: shadow`: recorded, nothing written to GitHub.
	Shadow bool `json:"shadow,omitempty"`
	// DeliveryID (v0.5) is the X-GitHub-Delivery header of the webhook that caused this decision, so a record
	// can be traced back to GitHub's delivery log (and a redelivery recognised).
	DeliveryID string `json:"delivery_id,omitempty"`
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
	drift   driftLog
	// portal (portal.go): sidecar log of resolutions and closures, and the outbound hook
	ef          *os.File
	resolutions []Resolution
	resolved    map[string]int       // repo + "\x00" + head_sha → index of its latest resolution
	closed      map[string]time.Time // repo + "\x00" + pr → when it was closed
	onRecord    func(Record)
	onEvent     func(Event)
	// outcomes (outcome.go): repo + "\x00" + pr → merged outcome; lastGood: its last error-free decision
	outcomes map[string]*PROutcome
	lastGood map[string]*Record
	// byPatch (v0.4): repo + "\x00" + pr + "\x00" + patch_id → latest error-free decision with model answers
	byPatch map[string]*Record
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
	s := &Store{path: path, bySHA: map[string]*Record{}, byPR: map[string]*Record{}, shasPR: map[string]map[string]string{}, open: map[string]map[int]OpenPR{}, jobs: map[string]*Job{},
		drift: newDriftLog(), resolved: map[string]int{}, closed: map[string]time.Time{},
		outcomes: map[string]*PROutcome{}, lastGood: map[string]*Record{}, byPatch: map[string]*Record{}}
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
	if err := s.openEvents(); err != nil {
		f.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) index(r *Record) {
	// A cached verdict recorded again (re-act) carries the same decision time: count its answers once.
	// A reused verdict (reused_from) repeats answers already counted: never counted again.
	if prev := s.bySHA[r.Repo+"\x00"+r.HeadSHA]; r.ReusedFrom == "" && (prev == nil || prev.Result.InputSHA256 != r.Result.InputSHA256 || !prev.Result.TS.Equal(r.Result.TS)) {
		s.drift.add(r)
	}
	s.bySHA[r.Repo+"\x00"+r.HeadSHA] = r
	if r.PR != 0 {
		s.byPR[r.Repo+"\x00"+itoa(r.PR)] = r
		if r.Result.Error == "" && len(r.Result.RawProbs) > 0 {
			s.lastGood[r.Repo+"\x00"+itoa(r.PR)] = r
		}
		if r.HeadSHA != "" && r.Result.Error == "" {
			k := r.Repo + "\x00" + itoa(r.PR)
			if s.shasPR[k] == nil {
				s.shasPR[k] = map[string]string{}
			}
			s.shasPR[k][r.HeadSHA] = r.Result.Verdict
		}
		if r.PatchID != "" && r.Result.Error == "" && len(r.Result.RawProbs) > 0 {
			s.byPatch[r.Repo+"\x00"+itoa(r.PR)+"\x00"+r.PatchID] = r
		}
	}
	s.recent = append(s.recent, r)
	if len(s.recent) > maxRecent {
		s.recent = s.recent[len(s.recent)-maxRecent:]
	}
}

// Record appends a decision to the gatelog (fsync'd per write: a verdict must
// not be lost on crash) and indexes it.
// A hook set with SetOnRecord sees it afterwards, outside the lock.
func (s *Store) Record(r Record) error {
	hook, err := s.record(&r)
	if err == nil && hook != nil {
		hook(r)
	}
	return err
}

func (s *Store) record(r *Record) (func(Record), error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if r.TS.IsZero() {
		r.TS = time.Now().UTC()
	}
	b, err := json.Marshal(r)
	if err != nil {
		return nil, err
	}
	if _, err := s.f.Write(append(b, '\n')); err != nil {
		return nil, err
	}
	if err := s.f.Sync(); err != nil {
		return nil, err
	}
	cp := *r
	s.index(&cp)
	return s.onRecord, nil
}

// Path is the gatelog file.
func (s *Store) Path() string { return s.path }

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

// ReusableForPatch returns the latest error-free decision on repo#pr with this patch id whose head is not
// exceptSHA, whose policy hash is policyHash and whose model version is the one being served (the model of
// the newest decision the drift monitor counted), or nil. That record's verdict may be copied to the new head
// instead of asking the model again (policy reuse_verdict_on_same_patch).
func (s *Store) ReusableForPatch(repo string, pr int, patchID, policyHash, exceptSHA string) *Record {
	if pr <= 0 || patchID == "" || policyHash == "" {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	r := s.byPatch[repo+"\x00"+itoa(pr)+"\x00"+patchID]
	if r == nil || r.HeadSHA == exceptSHA || r.PolicyHash != policyHash || r.Result.ModelVersion == "" || r.Result.ModelVersion != s.drift.last {
		return nil
	}
	return r
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
	var lat, tokIn, mlat []float64
	var cost float64
	n, errs, reused, debounced, shadow, retries, calls, pairErrs, tokOut := 0, 0, 0, 0, 0, 0, 0, 0, 0
	errKinds := map[string]int{}
	daily := dailyAcc{}
	for _, r := range s.recent {
		if r.TS.Before(t0) {
			continue
		}
		n++
		daily.add(r)
		verdicts[r.Result.Verdict]++
		routes[r.Result.Route]++
		rules[r.Result.Rule]++
		cost += r.Result.CostUSD
		if r.ReusedFrom != "" {
			reused++
		}
		debounced += len(r.DebouncedSHAs)
		if r.Shadow {
			shadow++
		}
		retries += r.Result.Retries
		calls += r.Result.ModelCalls
		pairErrs += r.Result.PairErrors
		tokOut += r.Result.OutputTokens
		if r.Result.Error != "" {
			errs++
			errKinds[ErrorKind(r.Result.Error)]++
		} else if r.Result.LatencyMS > 0 {
			lat = append(lat, r.Result.LatencyMS)
			if r.ReusedFrom == "" { // a reused verdict made no call: no tokens, no model latency
				if r.Result.InputTokens > 0 {
					tokIn = append(tokIn, float64(r.Result.InputTokens))
				}
				if r.Result.ModelLatencySumMS > 0 {
					mlat = append(mlat, r.Result.ModelLatencySumMS)
				}
			}
		}
	}
	sort.Float64s(lat)
	sort.Float64s(tokIn)
	sort.Float64s(mlat)
	out := map[string]any{"window_s": window.Seconds(), "decisions": n, "per_hour": float64(n) / window.Hours(),
		"verdicts": verdicts, "routes": routes, "rules": rules, "cost_usd": cost, "reused": reused, "debounced": debounced, "shadow": shadow, "error_rate": 0.0, "p50_ms": nil, "p99_ms": nil}
	if n > 0 {
		out["error_rate"] = float64(errs) / float64(n)
	}
	if len(lat) > 0 {
		out["p50_ms"] = lat[len(lat)/2]
		out["p99_ms"] = lat[min(int(float64(len(lat))*0.99), len(lat)-1)]
	}
	model := map[string]any{"calls": calls, "retries": retries, "pair_errors": pairErrs, "errors_by_kind": errKinds,
		"output_tokens": tokOut, "input_tokens_p50": nil, "input_tokens_p99": nil, "model_latency_p50_ms": nil, "model_latency_p99_ms": nil}
	if len(tokIn) > 0 {
		model["input_tokens_p50"] = tokIn[len(tokIn)/2]
		model["input_tokens_p99"] = tokIn[min(int(float64(len(tokIn))*0.99), len(tokIn)-1)]
		sum := 0.0
		for _, t := range tokIn {
			sum += t
		}
		model["input_tokens"] = int(sum)
	}
	if len(mlat) > 0 {
		model["model_latency_p50_ms"] = mlat[len(mlat)/2]
		model["model_latency_p99_ms"] = mlat[min(int(float64(len(mlat))*0.99), len(mlat)-1)]
	}
	if n > 0 {
		model["cost_per_decision_usd"] = cost / float64(n)
	}
	out["model"] = model
	out["drift"] = s.driftReport(time.Now())
	out["history"] = map[string]any{"daily": daily.rows()}
	if rs := s.resolutionStats(t0); rs != nil {
		out["resolutions"] = rs
	}
	return out
}

// ErrorKind buckets a Result.Error for counting: connect (incl. timeouts), http_429, http_5xx, http_4xx,
// circuit_open, bad_reply, other.
func ErrorKind(e string) string {
	switch {
	case e == "":
		return ""
	case strings.HasPrefix(e, "ConnectError"):
		return "connect"
	case strings.HasPrefix(e, "CircuitOpen"):
		return "circuit_open"
	case strings.HasPrefix(e, "HTTP 429"):
		return "http_429"
	case strings.HasPrefix(e, "HTTP 5"):
		return "http_5xx"
	case strings.HasPrefix(e, "HTTP 4"):
		return "http_4xx"
	case strings.HasPrefix(e, "bad JSON"), strings.HasPrefix(e, "bad model JSON"):
		return "bad_reply"
	}
	return "other"
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

// ClosePR forgets an open PR and records the closure (merged or not), so it leaves the portal queue and graph,
// here and, through the event hook, in the hosted portal.
func (s *Store) ClosePR(repo string, pr int, merged bool) {
	s.mu.Lock()
	delete(s.open[repo], pr)
	s.mu.Unlock()
	_ = s.emit(Event{Kind: "closed", Repo: repo, PR: pr, Merged: &merged, TS: time.Now().UTC()})
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
