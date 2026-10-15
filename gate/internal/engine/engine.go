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
	LatencyMS    float64            `json:"latency_ms"`
	CostUSD      float64            `json:"cost_usd"`
	InputSHA256  string             `json:"input_sha256"`
	Error        string             `json:"error,omitempty"`
	TS           time.Time          `json:"ts"`
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
	Version          int      `yaml:"version"`
	Model            string   `yaml:"model"`
	QuestionSet      string   `yaml:"question_set"`
	Calibration      string   `yaml:"calibration"`
	AlwaysHumanPaths []string `yaml:"always_human_paths"`
	Rules            []Rule   `yaml:"rules"`
	Default          Outcome  `yaml:"default"`
	OnError          Outcome  `yaml:"on_error"`
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
		p.QuestionSet = "v2"
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
	for i := range p.Rules {
		r := &p.Rules[i]
		keys := make([]string, 0, len(r.When))
		for k := range r.When {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, q := range keys {
			m := condRE.FindStringSubmatch(r.When[q])
			if m == nil {
				return nil, fmt.Errorf("rule %q: bad condition %q for %s", r.Name, r.When[q], q)
			}
			v, _ := strconv.ParseFloat(m[2], 64)
			r.conds = append(r.conds, cond{q, m[1], v})
		}
	}
	return &p, nil
}

// Calibrator is a per-question piecewise-linear map (isotonic fit from gatebench).
type Calibrator struct {
	maps map[string]struct {
		X []float64 `json:"x"`
		Y []float64 `json:"y"`
	}
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
	if err := json.Unmarshal(b, &c.maps); err != nil {
		return nil, fmt.Errorf("calibration %s: %w", path, err)
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

// Gate = policy + model + calibrator.
type Gate struct {
	Policy *Policy
	Model  Model
	Cal    *Calibrator
}

// Decide runs the full pipeline for one change. It never panics on model
// failure: on_error applies (fail closed).
func (g *Gate) Decide(state string, paths []string) Result {
	sum := sha256.Sum256([]byte(state))
	sha := hex.EncodeToString(sum[:])
	now := time.Now().UTC()
	for _, prefix := range g.Policy.AlwaysHumanPaths {
		for _, p := range paths {
			if strings.HasPrefix(p, prefix) {
				return Result{Verdict: "hold", Route: "human", Reason: "touches protected path " + prefix, Rule: "always_human_paths",
					Probs: map[string]float64{}, RawProbs: map[string]float64{}, InputSHA256: sha, TS: now}
			}
		}
	}
	if g.Model == nil {
		return Result{Verdict: g.Policy.OnError.Verdict, Route: g.Policy.OnError.Route, Reason: g.Policy.OnError.Reason + ": no model", Rule: "on_error",
			Probs: map[string]float64{}, RawProbs: map[string]float64{}, InputSHA256: sha, Error: "no model configured", TS: now}
	}
	d := g.Model.Decide(state, QuestionsV2)
	if d.Error != "" {
		return Result{Verdict: g.Policy.OnError.Verdict, Route: g.Policy.OnError.Route, Reason: g.Policy.OnError.Reason, Rule: "on_error",
			Probs: map[string]float64{}, RawProbs: map[string]float64{}, ModelVersion: d.ModelVersion, LatencyMS: d.LatencyMS, CostUSD: d.CostUSD,
			InputSHA256: sha, Error: d.Error, TS: now}
	}
	cal := g.Cal.Apply(d.Probs)
	rule, out := g.Match(cal)
	return Result{Verdict: out.Verdict, Route: out.Route, Reason: out.Reason, Rule: rule, Probs: cal, RawProbs: d.Probs,
		ModelVersion: d.ModelVersion, LatencyMS: d.LatencyMS, CostUSD: d.CostUSD, InputSHA256: sha, TS: now}
}

// Match applies the rules to (calibrated) probabilities. Exported for `gate replay`.
func (g *Gate) Match(probs map[string]float64) (string, Outcome) {
	for _, r := range g.Policy.Rules {
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
