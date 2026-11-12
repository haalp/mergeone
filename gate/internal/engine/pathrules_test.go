package engine_test

import (
	"strings"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
)

const pathPolicy = `version: 1
model: mock
question_set: v2
calibration: none
rules:
  - name: migrations
    paths: ["migrations/", "**/*.sql"]
    when: { is_logic_change: ">= 0.3" }
    verdict: hold
    route: human
    reason: schema change
  - name: docs-only
    paths_only: ["docs/**", "*.md"]
    verdict: pass
    route: auto-merge
    reason: documentation
  - name: needs-human
    when: { needs_human_review: ">= 0.5" }
    verdict: hold
    route: human
    reason: h
default: { verdict: pass, route: auto-merge }
`

func gateFor(t *testing.T, body string) *engine.Gate {
	t.Helper()
	p, err := engine.LoadPolicy(writePolicy(t, body))
	if err != nil {
		t.Fatal(err)
	}
	return &engine.Gate{Policy: p, Cal: &engine.Calibrator{}}
}

func TestPathRules(t *testing.T) {
	g := gateFor(t, pathPolicy)
	hi := map[string]float64{"is_logic_change": 0.9, "needs_human_review": 0.9}
	lo := map[string]float64{"is_logic_change": 0.1, "needs_human_review": 0.1}
	for _, c := range []struct {
		name  string
		probs map[string]float64
		files []string
		rule  string
	}{
		{"dir prefix", hi, []string{"migrations/0007.sql", "app/x.go"}, "migrations"},
		{"** glob anywhere", hi, []string{"db/schema/users.sql"}, "migrations"},
		{"path matches but when does not", lo, []string{"migrations/0007.sql"}, "default"},
		{"docs only passes even when the model wants a human", hi, []string{"docs/a/b.md", "README.md"}, "docs-only"},
		{"basename glob at depth", hi, []string{"pkg/deep/NOTES.md"}, "docs-only"},
		{"one file outside paths_only", hi, []string{"docs/a.md", "main.go"}, "needs-human"},
		{"unknown files: path rules never match", hi, nil, "needs-human"},
		{"* does not cross a slash", hi, []string{"docsx/a.txt"}, "needs-human"},
	} {
		rule, _ := g.MatchPaths(c.probs, c.files)
		if rule != c.rule {
			t.Errorf("%s: rule %q, want %q", c.name, rule, c.rule)
		}
	}
	if rule, _ := g.Match(hi); rule != "needs-human" {
		t.Errorf("Match without files skips path rules, got %q", rule)
	}
}

func TestPathRulesHashAndValidation(t *testing.T) {
	plain := "version: 1\nmodel: mock\nquestion_set: v2\ncalibration: none\nrules:\n  - { name: a, when: { needs_human_review: \">= 0.5\" }, verdict: hold, route: human, reason: h }\ndefault: { verdict: pass, route: auto-merge }\n"
	withPaths := strings.Replace(plain, "name: a,", "name: a, paths: [\"src/**\"],", 1)
	h1 := gateFor(t, plain).Policy.Hash()
	h2 := gateFor(t, withPaths).Policy.Hash()
	if h1 == h2 {
		t.Fatal("a path condition must change the policy hash")
	}
	if !gateFor(t, withPaths).Policy.HasPathRules() || gateFor(t, plain).Policy.HasPathRules() {
		t.Fatal("HasPathRules")
	}
	if _, err := engine.LoadPolicy(writePolicy(t, strings.Replace(plain, "name: a,", "name: a, paths: [\"\"],", 1))); err == nil || !strings.Contains(err.Error(), `rule "a": paths`) {
		t.Fatalf("empty pattern must be a named error, got %v", err)
	}
}

func TestExplainAgreesWithMatchAndMeasuresDistance(t *testing.T) {
	g := gateFor(t, pathPolicy)
	probs := map[string]float64{"is_logic_change": 0.25, "needs_human_review": 0.62}
	files := []string{"migrations/0001.sql"}
	trace, rule, out := g.Explain(probs, files)
	wantRule, wantOut := g.MatchPaths(probs, files)
	if rule != wantRule || out != wantOut || rule != "needs-human" {
		t.Fatalf("explain %q/%+v, match %q/%+v", rule, out, wantRule, wantOut)
	}
	if len(trace) != 3 {
		t.Fatalf("every rule is traced: %d", len(trace))
	}
	m := trace[0] // migrations: path ok, when fails by 0.05
	if m.Matched || !m.PathsOK || len(m.Conds) != 1 || m.Conds[0].OK || m.Nearest < 0.049 || m.Nearest > 0.051 {
		t.Fatalf("migrations trace: %+v", m)
	}
	d := trace[1] // docs-only: a .sql file is outside
	if d.Matched || d.PathsOK || !strings.Contains(d.PathNote, "outside") {
		t.Fatalf("docs-only trace: %+v", d)
	}
	n := trace[2]
	if !n.Matched || !n.Fired || n.Conds[0].Margin < 0.119 || n.Conds[0].Margin > 0.121 {
		t.Fatalf("needs-human trace: %+v", n)
	}
	fired := 0
	for _, r := range trace {
		if r.Fired {
			fired++
		}
	}
	if fired != 1 {
		t.Fatalf("exactly one rule fires, got %d", fired)
	}
}
