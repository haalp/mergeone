package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

const humanPolicy = "version: 1\nmodel: mock\nquestion_set: v2\ncalibration: none\nrules:\n" +
	"  - { name: migrations, paths: [\"migrations/\"], when: { is_logic_change: \">= 0.3\" }, verdict: hold, route: human, reason: schema change }\n" +
	"  - { name: weak-tests, when: { weakens_tests: \">= 0.6\" }, verdict: block, route: human, reason: tests weakened }\n" +
	"  - { name: needs-human, when: { needs_human_review: \">= 0.5\" }, verdict: hold, route: human, reason: judgement }\n" +
	"default: { verdict: pass, route: auto-merge }\n"

func writeFile(t *testing.T, dir, name, body string) string {
	t.Helper()
	p := filepath.Join(dir, name)
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

func logWith(t *testing.T, dir string, recs ...store.Record) string {
	t.Helper()
	var sb strings.Builder
	for _, r := range recs {
		b, _ := json.Marshal(r)
		sb.Write(b)
		sb.WriteByte('\n')
	}
	return writeFile(t, dir, "gatelog.jsonl", sb.String())
}

func TestExplainTracesEveryRuleAndTheNearestMiss(t *testing.T) {
	dir := t.TempDir()
	pol := writeFile(t, dir, "policy.yaml", humanPolicy)
	probs := map[string]float64{"is_logic_change": 0.25, "weakens_tests": 0.55, "needs_human_review": 0.62, "touches_concurrency": 0.02}
	log := logWith(t, dir,
		store.Record{TS: time.Now().UTC(), Repo: "o/r", PR: 4, HeadSHA: "aaaa1111", Result: engine.Result{Verdict: "pass", Route: "auto-merge", Rule: "default", RawProbs: map[string]float64{"needs_human_review": 0.1}, Probs: map[string]float64{"needs_human_review": 0.1}}},
		store.Record{TS: time.Now().UTC(), Repo: "o/r", PR: 7, HeadSHA: "bbbb2222cccc", Shadow: true,
			Result: engine.Result{Verdict: "hold", Route: "human", Rule: "needs-human", Reason: "judgement", RawProbs: probs, Probs: probs,
				ChangedPaths: []string{"migrations/0003.sql"}, ModelVersion: "m1", LatencyMS: 300, CostUSD: 0.00004, ModelCalls: 1}})
	code, out := capture(t, "explain", "--policy", pol, "--log", log, "#7")
	if code != 0 {
		t.Fatalf("exit %d\n%s", code, out)
	}
	for _, want := range []string{
		"HOLD → human", "o/r#7 @ bbbb2222cc", "rule     needs-human  (judgement)",
		"because  needs_human_review 0.62 >= 0.50 (by 0.12)",
		"nearest  migrations → hold/human: is_logic_change 0.25 >= 0.30 is false (0.05 away)",
		"shadow mode", "▶  3. needs-human", "weakens_tests 0.55 >= 0.60 ✗", "paths: migrations/0003.sql ✓", "never merges",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in:\n%s", want, out)
		}
	}
	// by sha prefix, and the last record when no ref is given
	if code, out := capture(t, "explain", "--policy", pol, "--log", log, "aaaa"); code != 0 || !strings.Contains(out, "PASS → auto-merge") || !strings.Contains(out, "▶     default") {
		t.Fatalf("sha prefix: %d\n%s", code, out)
	}
	if _, out := capture(t, "explain", "--policy", pol, "--log", log); !strings.Contains(out, "o/r#7") {
		t.Fatalf("no ref = last record:\n%s", out)
	}
	// JSON carries the same trace
	_, out = capture(t, "explain", "--policy", pol, "--log", log, "--json", "o/r#7")
	var j struct {
		Changed bool               `json:"changed"`
		Rules   []engine.RuleTrace `json:"rules"`
		Now     map[string]string  `json:"now"`
	}
	if err := json.Unmarshal([]byte(out), &j); err != nil || len(j.Rules) != 3 || j.Changed || j.Now["rule"] != "needs-human" {
		t.Fatalf("json: %v\n%s", err, out)
	}
	if code, _ := capture(t, "explain", "--policy", pol, "--log", log, "#99"); code == 0 {
		t.Fatal("unknown ref must fail")
	}
}

func TestExplainSaysWhenThePolicyNowDisagrees(t *testing.T) {
	dir := t.TempDir()
	pol := writeFile(t, dir, "policy.yaml", strings.Replace(humanPolicy, `needs_human_review: ">= 0.5"`, `needs_human_review: ">= 0.9"`, 1))
	probs := map[string]float64{"needs_human_review": 0.62}
	log := logWith(t, dir, store.Record{TS: time.Now().UTC(), Repo: "o/r", PR: 7, HeadSHA: "bbbb",
		Result: engine.Result{Verdict: "hold", Route: "human", Rule: "needs-human", RawProbs: probs, Probs: probs}})
	_, out := capture(t, "explain", "--policy", pol, "--log", log)
	if !strings.Contains(out, "under the policy as it is now this decision would be pass/auto-merge by rule default; it was recorded as hold by needs-human") {
		t.Fatalf("missing the changed note:\n%s", out)
	}
}

func TestPRHumanAndJSONOutput(t *testing.T) {
	dir := t.TempDir()
	pol := writeFile(t, dir, "policy.yaml", humanPolicy)
	res := engine.Result{Verdict: "block", Route: "human", Rule: "weak-tests", Reason: "tests weakened", Probs: map[string]float64{"weakens_tests": 0.8}, RawProbs: map[string]float64{"weakens_tests": 0.8}, ModelVersion: "m", ModelCalls: 3, PairCacheHits: 8, LatencyMS: 120}
	g, err := buildGateOpts(pol, true, "", false)
	if err != nil {
		t.Fatal(err)
	}
	trace, _, _ := g.Explain(effectiveProbs(g, res), nil)
	var sb strings.Builder
	printVerdict(&sb, res, trace, "o/r", 3, "deadbeefcafe")
	out := sb.String()
	for _, want := range []string{"BLOCK → human", "o/r#3 @ deadbeefca", "because  weakens_tests 0.80 >= 0.60 (by 0.20)", "3 calls · 8 from the pair cache"} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in:\n%s", want, out)
		}
	}
	if exitCodeOf(res) != 2 || exitCodeOf(engine.Result{Verdict: "pass"}) != 0 || exitCodeOf(engine.Result{Verdict: "hold"}) != 1 {
		t.Fatal("exit codes")
	}
	if stdoutIsTTY() {
		t.Fatal("a test's stdout is not a terminal: JSON must stay the default for pipes and CI")
	}
}

func TestDoctorOfflineAndFailures(t *testing.T) {
	dir := t.TempDir()
	pol := writeFile(t, dir, "policy.yaml", humanPolicy)
	t.Setenv("GITHUB_TOKEN", "")
	t.Setenv("MERGEONE_GITHUB_TOKEN", "")
	t.Setenv("MERGEONE_WEBHOOK_SECRET", "")
	code, out := capture(t, "doctor", "--policy", pol, "--log", filepath.Join(dir, "g", "gatelog.jsonl"), "--offline")
	if code != 0 || !strings.Contains(out, "ok    policy") || !strings.Contains(out, "model: mock") || !strings.Contains(out, "warn  github") || !strings.Contains(out, "ready, with") {
		t.Fatalf("offline doctor: %d\n%s", code, out)
	}
	code, out = capture(t, "doctor", "--policy", filepath.Join(dir, "nope.yaml"), "--offline")
	if code != 1 || !strings.Contains(out, "FAIL  policy") || !strings.Contains(out, "mergeone init") {
		t.Fatalf("missing policy: %d\n%s", code, out)
	}
	code, out = capture(t, "doctor", "--policy", pol, "--offline", "--json")
	var j struct {
		Failed int     `json:"failed"`
		Checks []check `json:"checks"`
	}
	if err := json.Unmarshal([]byte(out), &j); err != nil || code != 0 || j.Failed != 0 || len(j.Checks) < 5 {
		t.Fatalf("json: %v %d\n%s", err, code, out)
	}
}

func TestDoctorGitHubAndGatewayChecks(t *testing.T) {
	dir := t.TempDir()
	pol := writeFile(t, dir, "policy.yaml", humanPolicy)
	gh := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer good" {
			w.WriteHeader(401)
			return
		}
		switch {
		case r.URL.Path == "/repos/o/r":
			w.Write([]byte(`{"private":true,"permissions":{"pull":true,"push":false}}`))
		case r.URL.Path == "/repos/o/r/pulls":
			w.Write([]byte(`[]`))
		default:
			w.WriteHeader(404)
		}
	}))
	defer gh.Close()
	t.Setenv("MERGEONE_GITHUB_API", gh.URL)
	t.Setenv("MERGEONE_GITHUB_TOKEN", "")
	t.Setenv("GITHUB_TOKEN", "good")
	t.Setenv("MERGEONE_WEBHOOK_SECRET", "0123456789abcdef0123")
	_, out := capture(t, "doctor", "--policy", pol, "--log", filepath.Join(dir, "gatelog.jsonl"), "--repo", "o/r")
	if !strings.Contains(out, "ok    github     token can read o/r and its pull requests") {
		t.Fatalf("github ok:\n%s", out)
	}
	if strings.Contains(out, "0123456789abcdef0123") || strings.Contains(out, "Bearer") {
		t.Fatalf("doctor printed a secret:\n%s", out)
	}
	_, out = capture(t, "doctor", "--policy", pol, "--repo", "o/hidden", "--log", filepath.Join(dir, "gatelog.jsonl"))
	if !strings.Contains(out, "FAIL  github     the token cannot see o/hidden") {
		t.Fatalf("hidden repo:\n%s", out)
	}
	t.Setenv("GITHUB_TOKEN", "bad")
	code, out := capture(t, "doctor", "--policy", pol, "--repo", "o/r", "--log", filepath.Join(dir, "gatelog.jsonl"))
	if code != 1 || !strings.Contains(out, "GitHub rejected the token (401)") {
		t.Fatalf("bad token: %d\n%s", code, out)
	}
	t.Setenv("GITHUB_TOKEN", "good")

	// a gateway that verifies with the same secret, one with another secret, one that does not verify
	gateway := func(secret string) *httptest.Server {
		return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.URL.Path == "/healthz" {
				w.Write([]byte(`{"ok":true,"mode":"shadow","model":"jev","breaker":{"state":"closed"}}`))
				return
			}
			if secret == "" {
				w.Write([]byte(`{"ignored":"ping"}`))
				return
			}
			body := make([]byte, 0, 64)
			buf := make([]byte, 256)
			n, _ := r.Body.Read(buf)
			body = append(body, buf[:n]...)
			if r.Header.Get("X-Hub-Signature-256") != signFor(secret, body) {
				w.WriteHeader(401)
				return
			}
			w.Write([]byte(`{"ignored":"ping"}`))
		}))
	}
	same := gateway("0123456789abcdef0123")
	defer same.Close()
	_, out = capture(t, "doctor", "--policy", pol, "--repo", "o/r", "--log", filepath.Join(dir, "gatelog.jsonl"), "--url", same.URL)
	if !strings.Contains(out, "is up: mode shadow, model jev") || !strings.Contains(out, "ok    webhook    the gateway verifies signatures and the secret in this environment matches") {
		t.Fatalf("matching secret:\n%s", out)
	}
	other := gateway("another-secret-entirely")
	defer other.Close()
	code, out = capture(t, "doctor", "--policy", pol, "--repo", "o/r", "--log", filepath.Join(dir, "gatelog.jsonl"), "--url", other.URL)
	if code != 1 || !strings.Contains(out, "the two secrets differ") {
		t.Fatalf("different secret: %d\n%s", code, out)
	}
	open := gateway("")
	defer open.Close()
	code, out = capture(t, "doctor", "--policy", pol, "--repo", "o/r", "--log", filepath.Join(dir, "gatelog.jsonl"), "--url", open.URL)
	if code != 1 || !strings.Contains(out, "it is not verifying deliveries") {
		t.Fatalf("unverified gateway: %d\n%s", code, out)
	}
}
