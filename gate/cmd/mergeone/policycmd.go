package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"math"
	"os"
	"sort"
	"strings"
	"time"

	"github.com/goccy/go-yaml"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// mergeone policy <check|test|scaffold>: the policy file as a thing you can test without a model.
//
//	mergeone policy check    [--policy .mergeone.yaml]                      # load, validate, print what it will do
//	mergeone policy test     [--policy .mergeone.yaml] [--tests .mergeone-tests.yaml] [--json]
//	mergeone policy scaffold [--policy .mergeone.yaml] [--tests .mergeone-tests.yaml]  # write a starter test file
//
// A test file is YAML:
//
//	cases:
//	  - name: weakened tests are blocked
//	    probs: { weakens_tests: 0.8, needs_human_review: 0.2 }   # raw probabilities, run through the calibration map
//	    expect: { verdict: block, rule: weakens-tests }            # route is checked when given
//	  - name: migrations always need a human
//	    paths: [migrations/0007_users.sql]
//	    expect: { verdict: hold, rule: always_human_paths }
//
// Questions not named in probs are 0 (no rule reading them fires); `calibrated: true` on a case skips the map.
func cmdPolicy(args []string) int {
	if len(args) == 0 || args[0] == "-h" || args[0] == "--help" {
		fmt.Fprintln(stderr, "usage: mergeone policy <check|test|scaffold> [flags]")
		if len(args) > 0 {
			return 0
		}
		return 2
	}
	fs := flag.NewFlagSet("policy "+args[0], flag.ContinueOnError)
	policy := fs.String("policy", ".mergeone.yaml", "policy file")
	tests := fs.String("tests", ".mergeone-tests.yaml", "test cases file")
	assets := fs.String("assets", "", "assets dir (calibration maps; default: policy dir)")
	asJSON := fs.Bool("json", false, "machine-readable output")
	if code, ok := parseFlags(fs, args[1:], 2); !ok {
		return code
	}
	switch args[0] {
	case "check":
		return policyCheck(*policy, *assets, *asJSON)
	case "test":
		return policyTest(*policy, *tests, *assets, *asJSON)
	case "scaffold":
		return policyScaffold(*policy, *tests, *assets)
	}
	fmt.Fprintf(stderr, "policy: unknown subcommand %q (check, test, scaffold)\n", args[0])
	return 2
}

func policyCheck(policyPath, assets string, asJSON bool) int {
	g, err := buildGateOpts(policyPath, true, assets, false)
	if err != nil {
		return fail(err)
	}
	p := g.Policy
	out := map[string]any{"policy": policyPath, "policy_hash": g.PolicyHash(), "mode": p.Mode, "model": p.Model,
		"question_set": p.QuestionSet, "rules": len(p.Rules), "always_human_paths": p.AlwaysHumanPaths,
		"default": p.Default, "on_error": p.OnError, "memory": p.Memory.Enabled, "max_rounds": p.MaxRounds,
		"drift": p.Drift.Enabled, "debounce_s": p.Debounce.Seconds(), "reuse_verdict_on_same_patch": p.ReuseVerdictOnSamePatch,
		"actions": p.Actions.Enabled, "comment_mode": p.Actions.CommentMode}
	cal := "none"
	if g.Cal != nil && g.Cal.Path != "" {
		cal = g.Cal.Path
	}
	out["calibration"] = cal
	if asJSON {
		b, _ := json.MarshalIndent(out, "", "  ")
		fmt.Fprintln(stdout, string(b))
		return 0
	}
	fmt.Fprintf(stdout, "%s: ok (policy_hash %.12s)\n", policyPath, g.PolicyHash())
	fmt.Fprintf(stdout, "mode %s · model %s · question_set %s · calibration %s\n", p.Mode, p.Model, p.QuestionSet, cal)
	fmt.Fprintf(stdout, "%d rules, first match wins (on calibrated probabilities):\n", len(p.Rules))
	for i, r := range p.Rules {
		conds := make([]string, 0, len(r.When))
		for _, q := range sortedKeysStr(r.When) {
			conds = append(conds, q+" "+r.When[q])
		}
		fmt.Fprintf(stdout, "  %2d. %-24s when %-40s → %s/%s\n", i+1, r.Name, strings.Join(conds, " and "), r.Verdict, r.Route)
	}
	fmt.Fprintf(stdout, "default %s/%s · on_error %s/%s · always_human_paths %v\n", p.Default.Verdict, p.Default.Route, p.OnError.Verdict, p.OnError.Route, p.AlwaysHumanPaths)
	fmt.Fprintf(stdout, "memory %v · max_rounds %d · drift %v · debounce %s · reuse %v · actions %v (comments: %s)\n",
		p.Memory.Enabled, p.MaxRounds, p.Drift.Enabled, p.Debounce, p.ReuseVerdictOnSamePatch, p.Actions.Enabled, p.Actions.CommentMode)
	return 0
}

type policyCase struct {
	Name       string             `yaml:"name" json:"name"`
	Probs      map[string]float64 `yaml:"probs" json:"probs,omitempty"`
	Calibrated bool               `yaml:"calibrated" json:"calibrated,omitempty"`
	Paths      []string           `yaml:"paths" json:"paths,omitempty"`
	Expect     struct {
		Verdict string `yaml:"verdict" json:"verdict"`
		Route   string `yaml:"route" json:"route,omitempty"`
		Rule    string `yaml:"rule" json:"rule,omitempty"`
	} `yaml:"expect" json:"expect"`
}

type policyTests struct {
	Cases []policyCase `yaml:"cases"`
}

type caseResult struct {
	Name   string `json:"name"`
	OK     bool   `json:"ok"`
	Got    string `json:"got"`
	Want   string `json:"want"`
	Detail string `json:"detail,omitempty"`
}

// runPolicyCase decides one case the way the gateway would: protected paths first, then calibration and rules.
func runPolicyCase(g *engine.Gate, c policyCase) caseResult {
	var verdict, route, rule string
	if r := g.Decide("", c.Paths); r.Rule == "always_human_paths" {
		verdict, route, rule = r.Verdict, r.Route, r.Rule
	} else {
		probs := map[string]float64{}
		for _, q := range engine.Questions(g.Policy.QuestionSet) {
			probs[q] = 0
		}
		for q, p := range c.Probs {
			probs[q] = p
		}
		if !c.Calibrated {
			probs = g.Cal.Apply(probs)
		}
		var out engine.Outcome
		rule, out = g.Match(probs)
		verdict, route = out.Verdict, out.Route
	}
	got := verdict + "/" + route + "/" + rule
	want := c.Expect.Verdict
	ok := verdict == c.Expect.Verdict
	if c.Expect.Route != "" {
		want += "/" + c.Expect.Route
		ok = ok && route == c.Expect.Route
	} else {
		want += "/*"
	}
	if c.Expect.Rule != "" {
		want += "/" + c.Expect.Rule
		ok = ok && rule == c.Expect.Rule
	} else {
		want += "/*"
	}
	return caseResult{Name: c.Name, OK: ok, Got: got, Want: want}
}

func policyTest(policyPath, testsPath, assets string, asJSON bool) int {
	g, err := buildGateOpts(policyPath, true, assets, false)
	if err != nil {
		return fail(err)
	}
	b, err := os.ReadFile(testsPath)
	if err != nil {
		return fail(fmt.Errorf("%w (mergeone policy scaffold writes a starter)", err))
	}
	var tf policyTests
	if err := yaml.Unmarshal(b, &tf); err != nil {
		return fail(fmt.Errorf("%s: %w", testsPath, err))
	}
	if len(tf.Cases) == 0 {
		return fail(fmt.Errorf("%s: no cases", testsPath))
	}
	results := make([]caseResult, 0, len(tf.Cases))
	failed := 0
	for i, c := range tf.Cases {
		if c.Name == "" {
			c.Name = fmt.Sprintf("case %d", i+1)
		}
		if c.Expect.Verdict == "" {
			results = append(results, caseResult{Name: c.Name, OK: false, Want: "?", Detail: "expect.verdict is required"})
			failed++
			continue
		}
		r := runPolicyCase(g, c)
		if !r.OK {
			failed++
		}
		results = append(results, r)
	}
	if asJSON {
		b, _ := json.MarshalIndent(map[string]any{"policy": policyPath, "policy_hash": g.PolicyHash(), "tests": testsPath,
			"cases": len(results), "failed": failed, "results": results}, "", "  ")
		fmt.Fprintln(stdout, string(b))
	} else {
		for _, r := range results {
			mark := "ok  "
			if !r.OK {
				mark = "FAIL"
			}
			line := fmt.Sprintf("%s  %-40s %s", mark, r.Name, r.Got)
			if !r.OK {
				line += "  (want " + r.Want + ")"
				if r.Detail != "" {
					line += "  " + r.Detail
				}
			}
			fmt.Fprintln(stdout, line)
		}
		fmt.Fprintf(stdout, "%d cases, %d failed · %s (policy_hash %.12s)\n", len(results), failed, policyPath, g.PolicyHash())
	}
	if failed > 0 {
		return 1
	}
	return 0
}

// policyScaffold writes a starter test file from the policy: one case per rule at a probability that fires
// it (every earlier rule's questions at 0), one for the default, one per protected path. The expectations
// are what the policy does today; the file is the regression baseline to edit from.
func policyScaffold(policyPath, testsPath, assets string) int {
	g, err := buildGateOpts(policyPath, true, assets, false)
	if err != nil {
		return fail(err)
	}
	if _, err := os.Stat(testsPath); err == nil {
		return fail(fmt.Errorf("%s exists; delete it to scaffold again", testsPath))
	}
	p := g.Policy
	var sb strings.Builder
	fmt.Fprintf(&sb, "# mergeone policy tests — generated from %s on %s; edit freely.\n", policyPath, time.Now().UTC().Format("2006-01-02"))
	sb.WriteString("# `mergeone policy test` runs them; probabilities are raw (through the calibration map) unless `calibrated: true`.\n")
	sb.WriteString("cases:\n")
	for _, r := range p.Rules {
		probs := map[string]float64{}
		for q, cond := range r.When {
			op, val := parseCond(cond)
			probs[q] = firingValue(op, val)
		}
		fmt.Fprintf(&sb, "  - name: %s\n    calibrated: true\n    probs: {%s}\n    expect: { verdict: %s, route: %s, rule: %s }\n",
			yamlStr("rule "+r.Name+" fires"), probsYAML(probs), r.Verdict, r.Route, r.Name)
	}
	fmt.Fprintf(&sb, "  - name: nothing matches\n    calibrated: true\n    probs: {}\n    expect: { verdict: %s, route: %s, rule: default }\n", p.Default.Verdict, p.Default.Route)
	for _, ph := range p.AlwaysHumanPaths {
		fmt.Fprintf(&sb, "  - name: %s\n    paths: [%s]\n    expect: { verdict: hold, route: human, rule: always_human_paths }\n",
			yamlStr("protected path "+ph), yamlStr(strings.TrimSuffix(ph, "/")+"/example"))
	}
	if err := os.WriteFile(testsPath, []byte(sb.String()), 0o644); err != nil {
		return fail(err)
	}
	fmt.Fprintf(stdout, "wrote %s (%d rules, 1 default, %d protected paths); run: mergeone policy test --policy %s --tests %s\n",
		testsPath, len(p.Rules), len(p.AlwaysHumanPaths), policyPath, testsPath)
	return 0
}

func parseCond(c string) (op string, val float64) {
	c = strings.TrimSpace(c)
	for _, o := range []string{">=", "<=", "==", ">", "<"} {
		if strings.HasPrefix(c, o) {
			fmt.Sscan(strings.TrimSpace(c[len(o):]), &val)
			return o, val
		}
	}
	return ">=", 0.5
}

// firingValue is a probability that satisfies op val with a margin, inside [0, 1].
func firingValue(op string, v float64) float64 {
	switch op {
	case ">=", "==":
		return math.Min(1, math.Round((v+0.05)*100)/100)
	case ">":
		return math.Min(1, math.Round((v+0.05)*100)/100)
	case "<=", "<":
		return math.Max(0, math.Round((v-0.05)*100)/100)
	}
	return v
}

func probsYAML(m map[string]float64) string {
	parts := make([]string, 0, len(m))
	for _, q := range sortedKeysF(m) {
		parts = append(parts, fmt.Sprintf("%s: %.2f", q, m[q]))
	}
	return strings.Join(parts, ", ")
}

func yamlStr(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func sortedKeysStr(m map[string]string) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}

func sortedKeysF(m map[string]float64) []string {
	ks := make([]string, 0, len(m))
	for k := range m {
		ks = append(ks, k)
	}
	sort.Strings(ks)
	return ks
}
