package engine

import (
	"fmt"
	"math"
	"path"
	"regexp"
	"strings"
)

// Path conditions on a rule ("intent rules", v0.7). They sit beside `when:` and narrow a rule to a part of the
// repository; they read the PR's changed-file list, never the model.
//
//	- name: migrations-need-a-human
//	  paths: ["migrations/**", "**/*.sql"]      # at least one changed file matches one of these
//	  when: { is_logic_change: ">= 0.3" }
//	  verdict: hold
//	  route: human
//	- name: docs-only
//	  paths_only: ["docs/**", "*.md"]           # every changed file matches one of these
//	  verdict: pass
//	  route: auto-merge
//
// Globs: `*` matches within one path segment, `**` across segments, `?` one character, a trailing `/` means the
// directory and everything under it, and a pattern without a slash (`*.md`) matches the file name at any depth.
// A rule with a path condition never matches when the changed files are unknown (an empty list): the gate does
// not guess where a change is.

// MaxChangedPaths bounds the changed-file list kept on a result for path rules, explain and replay.
const MaxChangedPaths = 300

type pathGlob struct {
	src string
	re  *regexp.Regexp
}

func compileGlob(pat string) (pathGlob, error) {
	p := strings.TrimSpace(pat)
	if p == "" {
		return pathGlob{}, fmt.Errorf("empty path pattern")
	}
	p = strings.TrimPrefix(p, "./")
	p = strings.TrimPrefix(p, "/")
	if strings.HasSuffix(p, "/") {
		p += "**"
	}
	anyDepth := !strings.Contains(p, "/")
	var b strings.Builder
	b.WriteString("^")
	if anyDepth {
		b.WriteString("(?:.*/)?")
	}
	for i := 0; i < len(p); i++ {
		switch c := p[i]; {
		case c == '*' && i+1 < len(p) && p[i+1] == '*':
			i++
			if i+1 < len(p) && p[i+1] == '/' { // "**/" also matches no directory at all
				i++
				b.WriteString("(?:.*/)?")
			} else {
				b.WriteString(".*")
			}
		case c == '*':
			b.WriteString("[^/]*")
		case c == '?':
			b.WriteString("[^/]")
		default:
			b.WriteString(regexp.QuoteMeta(string(c)))
		}
	}
	b.WriteString("$")
	re, err := regexp.Compile(b.String())
	if err != nil {
		return pathGlob{}, fmt.Errorf("path pattern %q: %w", pat, err)
	}
	return pathGlob{src: pat, re: re}, nil
}

func compileGlobs(pats []string) ([]pathGlob, error) {
	out := make([]pathGlob, 0, len(pats))
	for _, p := range pats {
		g, err := compileGlob(p)
		if err != nil {
			return nil, err
		}
		out = append(out, g)
	}
	return out, nil
}

func matchAny(gs []pathGlob, file string) bool {
	file = path.Clean(strings.TrimPrefix(file, "./"))
	for _, g := range gs {
		if g.re.MatchString(file) {
			return true
		}
	}
	return false
}

// pathsOK evaluates a rule's path conditions against the changed files. detail names the file that decided it.
func (r *Rule) pathsOK(files []string) (ok bool, detail string) {
	if len(r.pathAny) == 0 && len(r.pathOnly) == 0 {
		return true, ""
	}
	if len(files) == 0 {
		return false, "changed files unknown"
	}
	if len(r.pathAny) > 0 {
		hit := ""
		for _, f := range files {
			if matchAny(r.pathAny, f) {
				hit = f
				break
			}
		}
		if hit == "" {
			return false, "no changed file matches paths"
		}
		detail = "paths: " + hit
	}
	if len(r.pathOnly) > 0 {
		for _, f := range files {
			if !matchAny(r.pathOnly, f) {
				return false, "paths_only: " + f + " is outside"
			}
		}
		if detail != "" {
			detail += "; "
		}
		detail += fmt.Sprintf("paths_only: all %d files inside", len(files))
	}
	return true, detail
}

// HasPathRules reports whether any rule reads the changed-file list.
func (p *Policy) HasPathRules() bool {
	for i := range p.Rules {
		if len(p.Rules[i].Paths)+len(p.Rules[i].PathsOnly) > 0 {
			return true
		}
	}
	return false
}

// CondTrace is one `when` condition of a rule against the probabilities of a decision.
type CondTrace struct {
	Question string  `json:"question"`
	Op       string  `json:"op"`
	Value    float64 `json:"value"`
	P        float64 `json:"p"`      // the calibrated probability (NaN → absent)
	OK       bool    `json:"ok"`     // the condition holds
	Margin   float64 `json:"margin"` // how far p is from flipping the condition (always ≥ 0)
	Missing  bool    `json:"missing,omitempty"`
}

// RuleTrace is one rule as the gate evaluated it: every condition, the path conditions, and whether it fired.
type RuleTrace struct {
	Name     string      `json:"name"`
	Verdict  string      `json:"verdict"`
	Route    string      `json:"route"`
	Conds    []CondTrace `json:"conds,omitempty"`
	PathsOK  bool        `json:"paths_ok"`
	PathNote string      `json:"path_note,omitempty"`
	Matched  bool        `json:"matched"`
	Fired    bool        `json:"fired"`             // the first rule that matched
	Skipped  string      `json:"skipped,omitempty"` // "drift:<question>" when the drift monitor stopped evaluation here
	// Nearest is, for a rule that did not match on probabilities alone, the smallest change that would make its
	// `when` hold: the largest margin among its failing conditions (every failing condition has to flip).
	Nearest float64 `json:"nearest,omitempty"`
}

// Explain evaluates every rule in order against calibrated probabilities and changed files, without stopping at
// the first match, and marks the one that fires. The verdict it implies is exactly MatchPaths's.
func (g *Gate) Explain(probs map[string]float64, files []string) (trace []RuleTrace, rule string, out Outcome) {
	rule, out = g.MatchPaths(probs, files)
	degraded := g.degradedSet()
	fired := false
	for i := range g.Policy.Rules {
		r := &g.Policy.Rules[i]
		t := RuleTrace{Name: r.Name, Verdict: r.Verdict, Route: r.Route}
		if od := g.Policy.OnDrift; len(degraded) > 0 && !od.Ignore && !fired {
			if q := r.readsDegraded(degraded); q != "" {
				t.Skipped = "drift:" + q
				trace = append(trace, t)
				fired = true // evaluation stopped here: on_drift is the verdict
				continue
			}
		}
		all := true
		for _, c := range r.conds {
			p, has := probs[c.q]
			ct := CondTrace{Question: c.q, Op: c.op, Value: c.val, P: p}
			if !has || math.IsNaN(p) {
				ct.Missing, ct.P = true, 0
				all = false
				t.Nearest = math.Inf(1)
			} else {
				ct.OK = cmp(c.op, p, c.val)
				ct.Margin = math.Abs(p - c.val)
				if !ct.OK {
					all = false
					if ct.Margin > t.Nearest && !math.IsInf(t.Nearest, 1) {
						t.Nearest = ct.Margin
					}
				}
			}
			t.Conds = append(t.Conds, ct)
		}
		if math.IsInf(t.Nearest, 1) {
			t.Nearest = 0
		}
		t.PathsOK, t.PathNote = r.pathsOK(files)
		t.Matched = all && t.PathsOK
		if t.Matched && !fired && r.Name == rule {
			t.Fired, fired = true, true
		}
		trace = append(trace, t)
	}
	return trace, rule, out
}
