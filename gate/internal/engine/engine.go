// Package engine turns typed decisions into a verdict and a route via a policy.
//
// It reads the same .mergeone.yaml and calibration_<model>_<qset>.json files
// the Python reference implementation (gatebench) produces, so a policy tuned
// with `gate replay` on history applies unchanged to the Go gateway.
package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/goccy/go-yaml"
)

// QuestionsV2 is the run-2 typed-triage question set (PROTOCOL.md R2.2).
var QuestionsV2 = []string{"is_logic_change", "weakens_tests", "message_matches_diff", "touches_concurrency", "needs_human_review"}

// Decision is what a model returns: P(true) per question plus cost/latency.
type Decision struct {
	Probs        map[string]float64 `json:"probs"`
	Confidence   map[string]float64 `json:"confidence,omitempty"`
	LatencyMS    float64            `json:"latency_ms"`
	InputTokens  int                `json:"input_tokens"`
	OutputTokens int                `json:"output_tokens"`
	CostUSD      float64            `json:"cost_usd"`
	ModelVersion string             `json:"model_version"`
	Error        string             `json:"error,omitempty"`
}

// Model is the one interface every decision backend implements.
type Model interface {
	Name() string
	Decide(state string, questions []string) Decision
}

// Result is a gate verdict. It is what the gatelog stores and the API returns.
type Result struct {
	Verdict      string             `json:"verdict"` // pass | hold | block
	Route        string             `json:"route"`
	Reason       string             `json:"reason"`
	Rule         string             `json:"rule"`
	Probs        map[string]float64 `json:"probs"`
	RawProbs     map[string]float64 `json:"raw_probs"`
	ModelVersion string             `json:"model_version"`
	LatencyMS    float64            `json:"latency_ms"` // DecideFor with a memory: wall clock of the whole decision
	// ModelLatencySumMS is the sum of every counted model call's latency (main + pair calls) — what
	// LatencyMS recorded before pair calls ran concurrently with each other and with the main call.
	ModelLatencySumMS float64        `json:"model_latency_sum_ms,omitempty"`
	CostUSD           float64        `json:"cost_usd"`
	InputSHA256       string         `json:"input_sha256"`
	Error             string         `json:"error,omitempty"`
	TS                time.Time      `json:"ts"`
	Extra             map[string]any `json:"extra,omitempty"` // run 4: duplicate candidates, stale days
	// Drift lists the questions the drift monitor had degraded at decision time (drift.enabled only).
	Drift []string `json:"drift,omitempty"`
	// Rounds is the number of non-pass verdicts on this PR including this one (max_rounds on and PR known).
	Rounds int `json:"rounds,omitempty"`
	// CappedRule is the rule that matched before max-rounds replaced it.
	CappedRule string `json:"capped_rule,omitempty"`
	// Actions is what the verdict-action layer wrote back to GitHub (webhook/CI mode only; nil elsewhere).
	Actions *ActionsReport `json:"actions,omitempty"`
}

// ActionsReport records the GitHub writes made for one verdict. Errors never change the verdict.
type ActionsReport struct {
	Applied []string `json:"applied"`           // writes performed, e.g. "status:failure", "label+:mergeone:hold"
	Skipped string   `json:"skipped,omitempty"` // why the whole layer did nothing (disabled, no token, no PR number)
	Errors  []string `json:"errors"`
}

// PRMeta is what the PR memory needs about the change under decision (all optional).
type PRMeta struct {
	Repo      string
	Number    int
	Title     string
	Body      string
	Author    string
	Base      string // base branch the PR targets
	Paths     []string
	CreatedAt time.Time
	UpdatedAt time.Time
}

// Candidate is a memory hit as the engine sees it (decoupled from the memory package).
type Candidate struct {
	Number    int     `json:"number"`
	Score     float64 `json:"score"`
	State     string  `json:"state"`
	Title     string  `json:"title"`
	PairState string  `json:"-"`
}

// Memory is the optional PR-memory hook (run 4). Returns top-k candidates with rendered pair states.
type Memory interface {
	Candidates(meta PRMeta, aState string, k int) []Candidate
}

// Outcome is a (verdict, route, reason) triple used by rules, default and on_error.
type Outcome struct {
	Verdict string `yaml:"verdict" json:"verdict"`
	Route   string `yaml:"route" json:"route"`
	Reason  string `yaml:"reason" json:"reason"`
}

// Rule is one policy rule: all conditions must hold (AND); first match wins.
type Rule struct {
	Name    string            `yaml:"name"`
	When    map[string]string `yaml:"when"`
	Verdict string            `yaml:"verdict"`
	Route   string            `yaml:"route"`
	Reason  string            `yaml:"reason"`
	conds   []cond
}

type cond struct {
	q   string
	op  string
	val float64
}

// Policy mirrors mergeone/gate/policy.yaml.
type Policy struct {
	Version int    `yaml:"version"`
	Model   string `yaml:"model"`    // key in prompts/models.yaml, or mock
	ModelID string `yaml:"model_id"` // optional pin, e.g. pplx-decider-flash-v1.2
	// Provider overrides or replaces the registry row for Model: a private endpoint, a local llama.cpp
	// server, a model that is not in models.yaml yet. Same keys as a models.yaml entry.
	Provider         map[string]any `yaml:"provider"`
	QuestionSet      string         `yaml:"question_set"`
	Calibration      string         `yaml:"calibration"`
	AlwaysHumanPaths []string       `yaml:"always_human_paths"`
	Rules            []Rule         `yaml:"rules"`
	Default          Outcome        `yaml:"default"`
	OnError          Outcome        `yaml:"on_error"`
	Memory           struct {
		Enabled bool `yaml:"enabled"`
		TopK    int  `yaml:"top_k"`
	} `yaml:"memory"`
	Actions Actions `yaml:"actions"`
	// MaxRounds caps review loops: after this many non-pass verdicts on one PR, route to a human (0 = off).
	MaxRounds int `yaml:"max_rounds"`
	// Drift monitor (docs/drift-monitor.md): off by default. When on, rules reading a degraded question
	// are not evaluated and the PR gets OnDrift (default hold → human; `ignore` keeps the rules).
	Drift struct {
		Enabled bool `yaml:"enabled"`
	} `yaml:"drift"`
	OnDrift DriftOutcome `yaml:"on_drift"`
	// Portal (docs/portal.md): outbound ingest to the hosted portal (URL + Token) and the gateway's own
	// /api/* read API (APIToken, Origin for CORS). Values may name environment variables (${VAR}).
	Portal Portal `yaml:"portal"`
}

// Portal is the policy's `portal:` block. All fields are optional; an empty block changes nothing.
type Portal struct {
	URL      string `yaml:"url"`       // outbound: every gatelog record is POSTed to <url>/ingest
	Token    string `yaml:"token"`     // outbound bearer token, issued by the portal per repository
	APIToken string `yaml:"api_token"` // inbound: bearer token required on /api/* ("" = only on a localhost bind)
	Origin   string `yaml:"origin"`    // CORS origin allowed on /api/* (default: the origin of url)
}

// CORSOrigin is the origin /api/* answers CORS requests for: Origin, else scheme://host of URL.
func (p Portal) CORSOrigin() string {
	if p.Origin != "" {
		return strings.TrimRight(p.Origin, "/")
	}
	if i := strings.Index(p.URL, "://"); i > 0 {
		rest := p.URL[i+3:]
		if j := strings.Index(rest, "/"); j >= 0 {
			rest = rest[:j]
		}
		return p.URL[:i+3] + rest
	}
	return ""
}

// Actions is the policy's `actions:` block: deterministic GitHub write-back of a verdict (commit status,
// exclusive labels, one comment per head sha, reviewers). Off by default; used only in webhook/CI mode.
type Actions struct {
	Enabled       bool          `yaml:"enabled"`
	StatusContext string        `yaml:"status_context"` // default "mergeone"
	Comment       *bool         `yaml:"comment"`        // default true
	OnPass        VerdictAction `yaml:"on_pass"`
	OnHold        VerdictAction `yaml:"on_hold"`
	OnBlock       VerdictAction `yaml:"on_block"`
}

// VerdictAction is what one verdict writes. A missing labels key takes the default label; `labels: []` sets none.
type VerdictAction struct {
	Status        string   `yaml:"status"` // success | failure | pending | error
	Labels        []string `yaml:"labels"`
	Reviewers     []string `yaml:"reviewers"`      // hold/block only, and only when route == "human"
	TeamReviewers []string `yaml:"team_reviewers"` // team slugs, same rule
}

// For returns the action for a verdict (false for an unknown verdict).
func (a Actions) For(verdict string) (VerdictAction, bool) {
	switch verdict {
	case "pass":
		return a.OnPass, true
	case "hold":
		return a.OnHold, true
	case "block":
		return a.OnBlock, true
	}
	return VerdictAction{}, false
}

// CommentEnabled reports whether the per-sha comment is posted (default true).
func (a Actions) CommentEnabled() bool { return a.Comment == nil || *a.Comment }

// Normalize fills defaults and validates statuses (LoadPolicy calls it; idempotent).
func (a *Actions) Normalize() error {
	if a.StatusContext == "" {
		a.StatusContext = "mergeone"
	}
	defaults := []struct {
		name   string
		va     *VerdictAction
		status string
	}{{"on_pass", &a.OnPass, "success"}, {"on_hold", &a.OnHold, "failure"}, {"on_block", &a.OnBlock, "failure"}}
	for _, d := range defaults {
		if d.va.Status == "" {
			d.va.Status = d.status
		}
		switch d.va.Status {
		case "success", "failure", "pending", "error":
		default:
			return fmt.Errorf("actions.%s.status %q: want success|failure|pending|error", d.name, d.va.Status)
		}
		if d.va.Labels == nil {
			d.va.Labels = []string{"mergeone:" + strings.TrimPrefix(d.name, "on_")}
		}
	}
	if len(a.OnPass.Reviewers)+len(a.OnPass.TeamReviewers) > 0 {
		return fmt.Errorf("actions.on_pass: reviewers are only requested on hold/block")
	}
	return nil
}

var condRE = regexp.MustCompile(`^\s*(>=|<=|>|<|==)\s*([0-9.]+)\s*$`)

// LoadPolicy parses a policy file and compiles its rule conditions.
func LoadPolicy(path string) (*Policy, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var p Policy
	if err := yaml.Unmarshal(b, &p); err != nil {
		return nil, fmt.Errorf("policy %s: %w", path, err)
	}
	if p.QuestionSet == "" {
		p.QuestionSet = "v2" // v0.3: v4 (once calibration_<model>_v4.json exists)
	}
	if p.Model == "" {
		p.Model = "jev"
	}
	if p.Default.Verdict == "" {
		p.Default = Outcome{"pass", "auto-merge", ""}
	}
	if p.OnError.Verdict == "" {
		p.OnError = Outcome{"hold", "human", "Decision model unavailable"}
	}
	if !p.OnDrift.Ignore && p.OnDrift.Verdict == "" {
		p.OnDrift.Outcome = Outcome{Verdict: "hold", Route: "human"}
	}
	p.Portal.URL = strings.TrimRight(os.ExpandEnv(p.Portal.URL), "/")
	p.Portal.Token = os.ExpandEnv(p.Portal.Token)
	p.Portal.APIToken = os.ExpandEnv(p.Portal.APIToken)
	p.Portal.Origin = os.ExpandEnv(p.Portal.Origin)
	if p.MaxRounds < 0 {
		return nil, fmt.Errorf("policy %s: max_rounds %d: want >= 0", path, p.MaxRounds)
	}
	if err := p.compile(); err != nil {
		return nil, err
	}
	if err := p.Actions.Normalize(); err != nil {
		return nil, fmt.Errorf("policy %s: %w", path, err)
	}
	return &p, nil
}

// compile parses rule conditions; exported behaviour is unchanged.
func (p *Policy) compile() error {
	for i := range p.Rules {
		r := &p.Rules[i]
		r.conds = r.conds[:0]
		keys := make([]string, 0, len(r.When))
		for k := range r.When {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, q := range keys {
			m := condRE.FindStringSubmatch(r.When[q])
			if m == nil {
				return fmt.Errorf("rule %q: bad condition %q for %s", r.Name, r.When[q], q)
			}
			v, _ := strconv.ParseFloat(m[2], 64)
			r.conds = append(r.conds, cond{q, m[1], v})
		}
	}
	return nil
}

// Calibrator is a per-question piecewise-linear map (isotonic fit from gatebench).
type Calibrator struct {
	maps map[string]calMap
	// FitStats is the optional `fit_stats` block of the calibration file: the raw answers the map was fit
	// on, per question. The drift monitor's reference while the gatelog is younger than 35 days.
	FitStats map[string]QStats
	// Path is the file the map came from ("" = identity map); FitAt is its modification time.
	Path  string
	FitAt time.Time
}

type calMap struct {
	X []float64 `json:"x"`
	Y []float64 `json:"y"`
}

// LoadCalibration reads calibration_<model>_<qset>.json; a missing file is the identity map.
func LoadCalibration(path string) (*Calibrator, error) {
	c := &Calibrator{}
	if path == "" {
		return c, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return c, nil
		}
		return nil, err
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(b, &raw); err != nil {
		return nil, fmt.Errorf("calibration %s: %w", path, err)
	}
	c.Path = path
	if fi, err := os.Stat(path); err == nil {
		c.FitAt = fi.ModTime().UTC()
	}
	if fs, ok := raw["fit_stats"]; ok {
		if err := json.Unmarshal(fs, &c.FitStats); err != nil {
			return nil, fmt.Errorf("calibration %s: fit_stats: %w", path, err)
		}
		delete(raw, "fit_stats")
	}
	c.maps = make(map[string]calMap, len(raw))
	for q, m := range raw {
		var cm calMap
		if err := json.Unmarshal(m, &cm); err != nil {
			return nil, fmt.Errorf("calibration %s: %s: %w", path, q, err)
		}
		c.maps[q] = cm
	}
	return c, nil
}

// Apply maps raw probabilities through the calibration tables.
func (c *Calibrator) Apply(raw map[string]float64) map[string]float64 {
	out := make(map[string]float64, len(raw))
	for q, p := range raw {
		m, ok := c.maps[q]
		if !ok || len(m.X) == 0 || math.IsNaN(p) {
			out[q] = p
			continue
		}
		out[q] = interp(p, m.X, m.Y)
	}
	return out
}

func interp(x float64, xs, ys []float64) float64 {
	if x <= xs[0] {
		return ys[0]
	}
	if x >= xs[len(xs)-1] {
		return ys[len(ys)-1]
	}
	i := sort.SearchFloat64s(xs, x)
	if xs[i] == x {
		return ys[i]
	}
	x0, x1, y0, y1 := xs[i-1], xs[i], ys[i-1], ys[i]
	return y0 + (y1-y0)*(x-x0)/(x1-x0)
}

// Gate = policy + model + calibrator (+ optional PR memory for duplicate_of).
type Gate struct {
	Policy *Policy
	Model  Model
	Cal    *Calibrator
	Memory Memory // nil → duplicate_of is never set and dedup rules never fire
	PairK  int    // candidates asked per PR (default 5)
	// PairConcurrency bounds the same_change calls in flight for one decision (default 8; 1 = sequential).
	// It changes latency only: aggregation is in candidate order whatever the completion order.
	PairConcurrency int
	// Degraded returns the questions the drift monitor currently flags (nil = none). Read only when
	// policy drift.enabled is true.
	Degraded func() map[string]bool
}

// precheck returns the result for a change that never reaches the model (protected path, no model).
func (g *Gate) precheck(sha string, now time.Time, paths []string) *Result {
	for _, prefix := range g.Policy.AlwaysHumanPaths {
		for _, p := range paths {
			if strings.HasPrefix(p, prefix) {
				return &Result{Verdict: "hold", Route: "human", Reason: "touches protected path " + prefix, Rule: "always_human_paths",
					Probs: map[string]float64{}, RawProbs: map[string]float64{}, InputSHA256: sha, TS: now}
			}
		}
	}
	if g.Model == nil {
		return &Result{Verdict: g.Policy.OnError.Verdict, Route: g.Policy.OnError.Route, Reason: g.Policy.OnError.Reason + ": no model", Rule: "on_error",
			Probs: map[string]float64{}, RawProbs: map[string]float64{}, InputSHA256: sha, Error: "no model configured", TS: now}
	}
	return nil
}

// finish turns the main QuestionsV2 decision into a result (on_error on model failure).
func (g *Gate) finish(d Decision, sha string, now time.Time) Result {
	if d.Error != "" {
		return Result{Verdict: g.Policy.OnError.Verdict, Route: g.Policy.OnError.Route, Reason: g.Policy.OnError.Reason, Rule: "on_error",
			Probs: map[string]float64{}, RawProbs: map[string]float64{}, ModelVersion: d.ModelVersion, LatencyMS: d.LatencyMS, ModelLatencySumMS: d.LatencyMS,
			CostUSD: d.CostUSD, InputSHA256: sha, Error: d.Error, TS: now}
	}
	cal := g.Cal.Apply(d.Probs)
	rule, out := g.Match(cal)
	res := Result{Verdict: out.Verdict, Route: out.Route, Reason: out.Reason, Rule: rule, Probs: cal, RawProbs: d.Probs,
		ModelVersion: d.ModelVersion, LatencyMS: d.LatencyMS, ModelLatencySumMS: d.LatencyMS, CostUSD: d.CostUSD, InputSHA256: sha, TS: now}
	if deg := sortedKeys(g.degradedSet()); len(deg) > 0 {
		res.Drift = deg
	}
	return res
}

func inputSHA(state string) string {
	sum := sha256.Sum256([]byte(state))
	return hex.EncodeToString(sum[:])
}

// Decide runs the full pipeline for one change. It never panics on model
// failure: on_error applies (fail closed).
func (g *Gate) Decide(state string, paths []string) Result {
	sha := inputSHA(state)
	now := time.Now().UTC()
	if r := g.precheck(sha, now, paths); r != nil {
		return *r
	}
	return g.finish(g.Model.Decide(state, QuestionsV2), sha, now)
}

// pairStage is the memory half of DecideFor: retrieval + one same_change call per candidate.
type pairStage struct {
	cands []Candidate
	ds    []Decision // ds[i] answers cands[i]
}

// pairs retrieves the top-k candidates and asks same_change for each, at most PairConcurrency at a time.
// Results are indexed by candidate, so the caller aggregates in candidate order.
func (g *Gate) pairs(state string, meta PRMeta) pairStage {
	k := g.PairK
	if k <= 0 {
		k = 5
	}
	cands := g.Memory.Candidates(meta, state, k)
	ds := make([]Decision, len(cands))
	conc := g.PairConcurrency
	if conc <= 0 {
		conc = 8
	}
	if conc == 1 || len(cands) <= 1 {
		for i, c := range cands {
			ds[i] = g.Model.Decide(c.PairState, []string{"same_change"})
		}
		return pairStage{cands, ds}
	}
	sem := make(chan struct{}, conc)
	var wg sync.WaitGroup
	for i := range cands {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int) {
			defer func() { <-sem; wg.Done() }()
			ds[i] = g.Model.Decide(cands[i].PairState, []string{"same_change"})
		}(i)
	}
	wg.Wait()
	return pairStage{cands, ds}
}

// DecideFor is Decide plus the run-4 signals when a PR memory is configured:
// duplicate_of = max over top-k candidates of same_change, silent_days from UpdatedAt.
// Both are added to the probability map so policy rules can reference them.
//
// With a memory, the main QuestionsV2 call and the pair stage (retrieval + same_change calls, which need
// only state and meta) run at the same time and are joined before policy matching; pair calls run
// PairConcurrency at a time. Result.LatencyMS is then the wall clock of the whole decision and
// Result.ModelLatencySumMS the sum over every model call counted (the pre-parallel LatencyMS).
func (g *Gate) DecideFor(state string, meta PRMeta) Result {
	if g.Memory == nil || meta.Repo == "" {
		return g.decideForNoMemory(state, meta)
	}
	t0 := time.Now()
	sha := inputSHA(state)
	now := time.Now().UTC()
	if r := g.precheck(sha, now, meta.Paths); r != nil {
		return *r
	}
	mainCh := make(chan Decision, 1)
	go func() { mainCh <- g.Model.Decide(state, QuestionsV2) }()
	ps := g.pairs(state, meta)
	res := g.finish(<-mainCh, sha, now)
	if res.Error != "" {
		res.LatencyMS = wallMS(t0)
		return res // pair answers are discarded, as when they were never asked
	}
	extra := map[string]any{}
	g.silentDays(&res, meta, extra)
	best, bestN := 0.0, 0
	var seen []map[string]any
	for i, c := range ps.cands {
		d := ps.ds[i]
		res.ModelLatencySumMS += d.LatencyMS
		res.CostUSD += d.CostUSD
		p := d.Probs["same_change"]
		if d.Error != "" || math.IsNaN(p) {
			p = 0
		}
		seen = append(seen, map[string]any{"number": c.Number, "state": c.State, "title": c.Title, "retrieval": c.Score, "duplicate_of": math.Round(p*1000) / 1000})
		if p > best {
			best, bestN = p, c.Number
		}
	}
	res.Probs["duplicate_of"] = math.Round(best*1000) / 1000
	extra["candidates"] = seen
	if bestN != 0 {
		extra["duplicate_candidate"] = bestN
	}
	g.rematch(&res, extra)
	res.LatencyMS = wallMS(t0)
	return res
}

// decideForNoMemory is DecideFor without a memory (or without a repo): unchanged from before the pair stage
// was parallelised, LatencyMS is the main call's latency.
func (g *Gate) decideForNoMemory(state string, meta PRMeta) Result {
	res := g.Decide(state, meta.Paths)
	if res.Error != "" || res.Rule == "always_human_paths" || g.Model == nil {
		return res
	}
	extra := map[string]any{}
	g.silentDays(&res, meta, extra)
	g.rematch(&res, extra)
	return res
}

func (g *Gate) silentDays(res *Result, meta PRMeta, extra map[string]any) {
	if !meta.UpdatedAt.IsZero() {
		days := time.Since(meta.UpdatedAt).Hours() / 24
		res.Probs["silent_days"] = math.Round(days*10) / 10
		extra["silent_days"] = res.Probs["silent_days"]
	}
}

func (g *Gate) rematch(res *Result, extra map[string]any) {
	if len(extra) == 0 {
		return
	}
	res.Extra = extra
	rule, out := g.Match(res.Probs)
	res.Rule, res.Verdict, res.Route, res.Reason = rule, out.Verdict, out.Route, out.Reason
	if n, ok := extra["duplicate_candidate"].(int); ok && rule == "duplicate-of" {
		res.Reason = fmt.Sprintf("%s (#%d)", out.Reason, n)
	}
}

// ApplyMaxRounds is the convergence cap. prior is the PR's earlier non-pass verdicts (the caller counts them;
// see store.NonPassRounds). A pass, an error result or max_rounds 0 is left alone; otherwise Rounds is set and,
// once prior >= max_rounds, the route becomes human under rule max-rounds (verdict and probs unchanged).
func (g *Gate) ApplyMaxRounds(res *Result, prior int) {
	n := g.Policy.MaxRounds
	if n <= 0 || res.Verdict == "pass" || res.Error != "" {
		return
	}
	res.Rounds = prior + 1
	if prior >= n {
		res.CappedRule = res.Rule
		res.Route, res.Rule = "human", "max-rounds"
		res.Reason = fmt.Sprintf("%d verdicts without pass on this PR; routed to a human (max_rounds; last rule: %s)", res.Rounds, res.CappedRule)
	}
}

func wallMS(t0 time.Time) float64 { return float64(time.Since(t0).Microseconds()) / 1000 }

// Match applies the rules to (calibrated) probabilities. Exported for `gate replay`.
// With the drift monitor on, the first rule (in order) that reads a degraded question is not evaluated:
// the PR gets on_drift with reason drift:<question>. Rules above it ran as usual; with on_drift: ignore
// every rule runs.
func (g *Gate) Match(probs map[string]float64) (string, Outcome) {
	degraded := g.degradedSet()
	for i := range g.Policy.Rules {
		r := &g.Policy.Rules[i]
		if od := g.Policy.OnDrift; len(degraded) > 0 && !od.Ignore {
			if q := r.readsDegraded(degraded); q != "" {
				reason := "drift:" + q
				if od.Reason != "" {
					reason += ": " + od.Reason
				}
				return "on_drift", Outcome{od.Verdict, od.Route, reason}
			}
		}
		ok := true
		for _, c := range r.conds {
			p, has := probs[c.q]
			if !has || math.IsNaN(p) || !cmp(c.op, p, c.val) {
				ok = false
				break
			}
		}
		if ok {
			return r.Name, Outcome{r.Verdict, r.Route, r.Reason}
		}
	}
	return "default", g.Policy.Default
}

func cmp(op string, a, b float64) bool {
	switch op {
	case ">=":
		return a >= b
	case "<=":
		return a <= b
	case ">":
		return a > b
	case "<":
		return a < b
	case "==":
		return a == b
	}
	return false
}
