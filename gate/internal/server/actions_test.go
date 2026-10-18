package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// ---- fake GitHub: records every request and keeps labels, comments and requested reviewers per PR.

type ghReq struct {
	Method, Path string
	Body         map[string]any
}

type fakeGH struct {
	mu        sync.Mutex
	reqs      []ghReq
	labels    map[int][]string
	comments  map[int][]string
	reviewers map[int][]string
	teams     map[int][]string
	statuses  []map[string]any
	failPath  string // any request whose path contains this returns 500
}

func newFakeGH(t *testing.T) (*fakeGH, *GitHub) {
	f := &fakeGH{labels: map[int][]string{}, comments: map[int][]string{}, reviewers: map[int][]string{}, teams: map[int][]string{}}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(srv.Close)
	g := NewGitHub("tok")
	g.API = srv.URL
	return f, g
}

func (f *fakeGH) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	var body map[string]any
	_ = json.NewDecoder(r.Body).Decode(&body)
	f.reqs = append(f.reqs, ghReq{r.Method, r.URL.Path, body})
	if r.Header.Get("Authorization") != "Bearer tok" {
		w.WriteHeader(401)
		return
	}
	if f.failPath != "" && strings.Contains(r.URL.Path, f.failPath) {
		w.WriteHeader(500)
		fmt.Fprint(w, `{"message":"boom"}`)
		return
	}
	parts := strings.Split(strings.TrimPrefix(r.URL.Path, "/repos/o/r/"), "/")
	pr := 0
	if len(parts) > 1 {
		fmt.Sscan(parts[1], &pr)
	}
	switch {
	case parts[0] == "statuses" && r.Method == "POST":
		f.statuses = append(f.statuses, body)
		w.WriteHeader(201)
		fmt.Fprint(w, `{}`)
	case parts[0] == "issues" && len(parts) == 3 && parts[2] == "labels" && r.Method == "GET":
		out := []map[string]string{}
		for _, l := range f.labels[pr] {
			out = append(out, map[string]string{"name": l})
		}
		_ = json.NewEncoder(w).Encode(out)
	case parts[0] == "issues" && len(parts) == 3 && parts[2] == "labels" && r.Method == "POST":
		for _, l := range body["labels"].([]any) {
			f.labels[pr] = append(f.labels[pr], l.(string))
		}
		fmt.Fprint(w, `[]`)
	case parts[0] == "issues" && len(parts) == 4 && parts[2] == "labels" && r.Method == "DELETE":
		name, _ := url.PathUnescape(parts[3])
		keep := []string{}
		for _, l := range f.labels[pr] {
			if l != name {
				keep = append(keep, l)
			}
		}
		f.labels[pr] = keep
		fmt.Fprint(w, `[]`)
	case parts[0] == "issues" && parts[2] == "comments" && r.Method == "GET":
		out := []map[string]string{}
		for _, c := range f.comments[pr] {
			out = append(out, map[string]string{"body": c})
		}
		_ = json.NewEncoder(w).Encode(out)
	case parts[0] == "issues" && parts[2] == "comments" && r.Method == "POST":
		f.comments[pr] = append(f.comments[pr], body["body"].(string))
		w.WriteHeader(201)
		fmt.Fprint(w, `{}`)
	case parts[0] == "pulls" && parts[2] == "requested_reviewers" && r.Method == "GET":
		users := []map[string]string{}
		for _, u := range f.reviewers[pr] {
			users = append(users, map[string]string{"login": u})
		}
		teams := []map[string]string{}
		for _, t := range f.teams[pr] {
			teams = append(teams, map[string]string{"slug": t})
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"users": users, "teams": teams})
	case parts[0] == "pulls" && parts[2] == "requested_reviewers" && r.Method == "POST":
		if rs, ok := body["reviewers"].([]any); ok {
			for _, u := range rs {
				f.reviewers[pr] = append(f.reviewers[pr], u.(string))
			}
		}
		if ts, ok := body["team_reviewers"].([]any); ok {
			for _, t := range ts {
				f.teams[pr] = append(f.teams[pr], t.(string))
			}
		}
		w.WriteHeader(201)
		fmt.Fprint(w, `{}`)
	default:
		w.WriteHeader(404)
	}
}

func (f *fakeGH) count(method, pathContains string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	n := 0
	for _, r := range f.reqs {
		if r.Method == method && strings.Contains(r.Path, pathContains) {
			n++
		}
	}
	return n
}

func (f *fakeGH) total() int { f.mu.Lock(); defer f.mu.Unlock(); return len(f.reqs) }

// ---- gate: a policy file + a model whose answers are keyed by the state text.

type keyedModel map[string]map[string]float64

func (m keyedModel) Name() string { return "keyed" }
func (m keyedModel) Decide(state string, qs []string) engine.Decision {
	return engine.Decision{Probs: m[state], ModelVersion: "keyed-1"}
}

var testModel = keyedModel{
	"pass":   {"is_logic_change": 0.1, "weakens_tests": 0.0, "message_matches_diff": 0.9, "touches_concurrency": 0.0, "needs_human_review": 0.1},
	"hold":   {"is_logic_change": 0.9, "weakens_tests": 0.0, "message_matches_diff": 0.9, "touches_concurrency": 0.0, "needs_human_review": 0.8},
	"domain": {"is_logic_change": 0.9, "weakens_tests": 0.0, "message_matches_diff": 0.9, "touches_concurrency": 0.9, "needs_human_review": 0.1},
	"block":  {"is_logic_change": 0.9, "weakens_tests": 0.9, "message_matches_diff": 0.9, "touches_concurrency": 0.0, "needs_human_review": 0.8},
}

const testRules = `version: 1
model: mock
rules:
  - name: weakened-tests
    when: { weakens_tests: ">= 0.6" }
    verdict: block
    route: human
    reason: "PR appears to remove, skip, or loosen tests"
  - name: concurrency-risk
    when: { touches_concurrency: ">= 0.6" }
    verdict: hold
    route: domain-review
    reason: "Behaviour change in concurrency-sensitive code"
  - name: maintainer-judgement
    when: { needs_human_review: ">= 0.5" }
    verdict: hold
    route: human
    reason: "Decision model thinks a maintainer should look"
default: { verdict: pass, route: auto-merge, reason: "No rule matched" }
`

const testActions = `actions:
  enabled: true
  on_hold: { status: failure, labels: ["mergeone:hold"], reviewers: ["alice"], team_reviewers: ["core"] }
`

func newTestGateway(t *testing.T, policyYAML string, gh *GitHub) (*Gateway, string) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "policy.yaml")
	if err := os.WriteFile(pp, []byte(policyYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	p, err := engine.LoadPolicy(pp)
	if err != nil {
		t.Fatal(err)
	}
	cal, _ := engine.LoadCalibration("")
	logPath := filepath.Join(dir, "gatelog.jsonl")
	st, err := store.Open(logPath)
	if err != nil {
		t.Fatal(err)
	}
	gw := New(&engine.Gate{Policy: p, Model: testModel, Cal: cal}, st, gh, 0, "https://gate.example")
	t.Cleanup(gw.Stop)
	return gw, logPath
}

// postDecide calls POST /decide synchronously (the CI path) and returns the decoded result.
func postDecide(t *testing.T, gw *Gateway, pr int, sha, state string) engine.Result {
	h := gw.Handler("")
	b, _ := json.Marshal(map[string]any{"repo": "o/r", "pr": pr, "head_sha": sha, "state": state})
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, httptest.NewRequest("POST", "/decide", bytes.NewReader(b)))
	if rr.Code != 200 {
		t.Fatalf("decide: %d %s", rr.Code, rr.Body.String())
	}
	var res engine.Result
	if err := json.Unmarshal(rr.Body.Bytes(), &res); err != nil {
		t.Fatal(err)
	}
	return res
}

// ---- tests

func TestActionsPolicyDefaults(t *testing.T) {
	gw, _ := newTestGateway(t, testRules, nil)
	a := gw.Gate.Policy.Actions
	if a.Enabled || a.StatusContext != "mergeone" || !a.CommentEnabled() {
		t.Fatalf("defaults: %+v", a)
	}
	if a.OnPass.Status != "success" || a.OnHold.Status != "failure" || a.OnBlock.Status != "failure" {
		t.Fatalf("default statuses: %+v", a)
	}
	if fmt.Sprint(a.OnPass.Labels, a.OnHold.Labels, a.OnBlock.Labels) != "[mergeone:pass] [mergeone:hold] [mergeone:block]" {
		t.Fatalf("default labels: %v %v %v", a.OnPass.Labels, a.OnHold.Labels, a.OnBlock.Labels)
	}
	dir := t.TempDir()
	for _, bad := range []string{"actions: { on_pass: { status: green } }", "actions: { on_pass: { reviewers: [bob] } }"} {
		pp := filepath.Join(dir, "p.yaml")
		_ = os.WriteFile(pp, []byte(testRules+bad+"\n"), 0o644)
		if _, err := engine.LoadPolicy(pp); err == nil {
			t.Fatalf("want a policy error for %q", bad)
		}
	}
	pp := filepath.Join(dir, "p.yaml")
	_ = os.WriteFile(pp, []byte(testRules+"actions: { comment: false, on_pass: { labels: [] } }\n"), 0o644)
	p, err := engine.LoadPolicy(pp)
	if err != nil || p.Actions.CommentEnabled() || len(p.Actions.OnPass.Labels) != 0 {
		t.Fatalf("explicit empty labels / comment false: %v %+v", err, p.Actions)
	}
}

func TestActionsDisabledByDefaultMakesNoCalls(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules, gh)
	res := postDecide(t, gw, 7, "s1", "block")
	if res.Verdict != "block" || res.Actions == nil || !strings.HasPrefix(res.Actions.Skipped, "disabled") {
		t.Fatalf("want block with actions skipped: %+v %+v", res, res.Actions)
	}
	if f.total() != 0 {
		t.Fatalf("disabled actions made %d GitHub calls", f.total())
	}
}

func TestActionsPassHoldBlock(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions, gh)
	cases := []struct {
		pr           int
		state, want  string
		status, labl string
	}{{1, "pass", "pass", "success", "mergeone:pass"}, {2, "hold", "hold", "failure", "mergeone:hold"}, {3, "block", "block", "failure", "mergeone:block"}}
	for i, c := range cases {
		sha := fmt.Sprintf("sha%d", c.pr)
		res := postDecide(t, gw, c.pr, sha, c.state)
		if res.Verdict != c.want || res.Actions == nil || len(res.Actions.Errors) != 0 || res.Actions.Skipped != "" {
			t.Fatalf("%s: %+v %+v", c.state, res, res.Actions)
		}
		st := f.statuses[i]
		if st["state"] != c.status || st["context"] != "mergeone" || st["target_url"] != "https://gate.example/verdict/o/r/"+sha {
			t.Fatalf("%s status body: %v", c.state, st)
		}
		if d := st["description"].(string); !strings.HasPrefix(d, c.want+" · ") || len([]rune(d)) > 140 {
			t.Fatalf("%s description %q", c.state, d)
		}
		if fmt.Sprint(f.labels[c.pr]) != "["+c.labl+"]" {
			t.Fatalf("%s labels %v", c.state, f.labels[c.pr])
		}
		if len(f.comments[c.pr]) != 1 || !strings.Contains(f.comments[c.pr][0], CommentMarker(sha)) ||
			!strings.Contains(f.comments[c.pr][0], strings.ToUpper(c.want)) || !strings.Contains(f.comments[c.pr][0], "| `is_logic_change` |") {
			t.Fatalf("%s comment %v", c.state, f.comments[c.pr])
		}
	}
	if f.count("POST", "/statuses/") != 3 || f.count("POST", "/merge") != 0 || f.count("PUT", "") != 0 {
		t.Fatalf("want 3 statuses and no merge: %+v", f.reqs)
	}
	// a repeat of the same decision is served from the gatelog and does not act again
	n := f.total()
	postDecide(t, gw, 2, "sha2", "hold")
	if f.total() != n {
		t.Fatalf("cached decision acted again: %d → %d calls", n, f.total())
	}
}

func TestActionsLabelsAreExclusive(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions, gh)
	f.labels[5] = []string{"bug", "mergeone:pass", "mergeone:block"}
	res := postDecide(t, gw, 5, "a1", "hold")
	if fmt.Sprint(f.labels[5]) != "[bug mergeone:hold]" {
		t.Fatalf("labels %v (applied %v)", f.labels[5], res.Actions.Applied)
	}
	if f.count("DELETE", "/labels/mergeone:pass") != 1 || f.count("DELETE", "/labels/mergeone:block") != 1 || f.count("DELETE", "/labels/bug") != 0 {
		t.Fatalf("deletes: %+v", f.reqs)
	}
	// new head turns it to pass: hold is removed, pass added; a label already present is not re-added
	postDecide(t, gw, 5, "a2", "pass")
	if fmt.Sprint(f.labels[5]) != "[bug mergeone:pass]" {
		t.Fatalf("labels after pass %v", f.labels[5])
	}
	before := f.count("POST", "/issues/5/labels")
	rep := ApplyActions(gh, gw.Gate.Policy.Actions, "o/r", 5, "a2", engine.Result{Verdict: "pass", Rule: "default"}, "")
	if f.count("POST", "/issues/5/labels") != before || len(rep.Errors) != 0 {
		t.Fatalf("label already present was posted again: %+v", rep)
	}
}

func TestActionsCommentIdempotentPerSHA(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions, gh)
	cfg := gw.Gate.Policy.Actions
	res := engine.Result{Verdict: "hold", Route: "domain-review", Rule: "concurrency-risk", Reason: "r", Probs: map[string]float64{"is_logic_change": 0.9}}
	for i := 0; i < 3; i++ {
		rep := ApplyActions(gh, cfg, "o/r", 9, "c0ffee", res, "")
		if len(rep.Errors) != 0 {
			t.Fatal(rep.Errors)
		}
	}
	if len(f.comments[9]) != 1 || f.count("POST", "/issues/9/comments") != 1 {
		t.Fatalf("same sha must comment once, got %d", len(f.comments[9]))
	}
	ApplyActions(gh, cfg, "o/r", 9, "beef01", res, "")
	if len(f.comments[9]) != 2 {
		t.Fatalf("a new head sha gets its own comment, got %d", len(f.comments[9]))
	}
	// memory line and docs link
	res.Probs["duplicate_of"] = 0.81
	res.Extra = map[string]any{"duplicate_candidate": 123}
	body := CommentBody("beef01", res)
	if !strings.Contains(body, "memory: candidate #123 (duplicate_of p=0.81)") || !strings.Contains(body, DocsURL) || !strings.Contains(body, "mergeone") {
		t.Fatalf("comment body:\n%s", body)
	}
}

func TestActionsReviewersOnlyOnHoldHuman(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions, gh)
	postDecide(t, gw, 1, "p", "pass")
	postDecide(t, gw, 2, "d", "domain") // hold, route domain-review
	postDecide(t, gw, 3, "b", "block")  // block, route human, but on_block has no reviewers
	if n := f.count("POST", "/requested_reviewers"); n != 0 {
		t.Fatalf("reviewers requested %d times outside hold+human", n)
	}
	res := postDecide(t, gw, 4, "h", "hold") // hold, route human
	if res.Route != "human" || f.count("POST", "/pulls/4/requested_reviewers") != 1 {
		t.Fatalf("hold+human must request reviewers: %+v", res.Actions)
	}
	var body map[string]any
	for _, r := range f.reqs {
		if r.Method == "POST" && strings.HasSuffix(r.Path, "/pulls/4/requested_reviewers") {
			body = r.Body
		}
	}
	if fmt.Sprint(body["reviewers"], body["team_reviewers"]) != "[alice] [core]" {
		t.Fatalf("reviewers body %v", body)
	}
	// already requested → no second POST; a partly requested set asks only for the missing one
	ApplyActions(gh, gw.Gate.Policy.Actions, "o/r", 4, "h2", engine.Result{Verdict: "hold", Route: "human", Rule: "x"}, "")
	if n := f.count("POST", "/pulls/4/requested_reviewers"); n != 1 {
		t.Fatalf("already-requested reviewers were requested again (%d POSTs)", n)
	}
	f.teams[4] = nil
	ApplyActions(gh, gw.Gate.Policy.Actions, "o/r", 4, "h3", engine.Result{Verdict: "hold", Route: "human", Rule: "x"}, "")
	last := f.reqs[len(f.reqs)-1]
	if _, hasUsers := last.Body["reviewers"]; hasUsers || fmt.Sprint(last.Body["team_reviewers"]) != "[core]" {
		t.Fatalf("want only the missing team requested: %v", last.Body)
	}
}

func TestActionsAPIErrorDoesNotChangeVerdict(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, logPath := newTestGateway(t, testRules+testActions, gh)
	f.failPath = "/statuses/"
	res := postDecide(t, gw, 8, "e1", "block")
	if res.Verdict != "block" || res.Route != "human" || res.Rule != "weakened-tests" {
		t.Fatalf("verdict changed by an action error: %+v", res)
	}
	if len(res.Actions.Errors) != 1 || !strings.Contains(res.Actions.Errors[0], "HTTP 500") {
		t.Fatalf("want the status error reported: %+v", res.Actions)
	}
	if fmt.Sprint(f.labels[8]) != "[mergeone:block]" || len(f.comments[8]) != 1 {
		t.Fatal("other actions must still run after one fails")
	}
	if f.count("POST", "/statuses/") != 1 {
		t.Fatal("no retries")
	}
	// the gatelog entry carries actions.errors
	fh, _ := os.Open(logPath)
	defer fh.Close()
	var last store.Record
	sc := bufio.NewScanner(fh)
	sc.Buffer(make([]byte, 1<<20), 1<<24)
	for sc.Scan() {
		_ = json.Unmarshal(sc.Bytes(), &last)
	}
	if last.HeadSHA != "e1" || last.Result.Actions == nil || len(last.Result.Actions.Errors) != 1 || last.Result.Verdict != "block" {
		t.Fatalf("gatelog entry: %+v", last.Result)
	}
	// a failed label read changes no labels (exclusivity cannot be proven)
	f.failPath = "/labels"
	rep := ApplyActions(gh, gw.Gate.Policy.Actions, "o/r", 8, "e2", engine.Result{Verdict: "pass", Rule: "default"}, "")
	if fmt.Sprint(f.labels[8]) != "[mergeone:block]" || len(rep.Errors) != 1 {
		t.Fatalf("labels %v errors %v", f.labels[8], rep.Errors)
	}
}

// The MCP tools decide through Gateway.Decide / DecideMeta, which must never write to GitHub.
func TestActionsNeverFromDecide(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions, gh)
	res := gw.Decide("o/r", 3, "m1", "block", nil, "mcp")
	if res.Actions != nil || f.total() != 0 {
		t.Fatalf("Decide acted: %+v, %d calls", res.Actions, f.total())
	}
}

func TestStatusDescriptionTrimmed(t *testing.T) {
	d := StatusDescription(engine.Result{Verdict: "hold", Rule: "r", Reason: strings.Repeat("é", 300)})
	if n := len([]rune(d)); n != 140 || !strings.HasPrefix(d, "hold · r · ") {
		t.Fatalf("len %d: %q", n, d)
	}
}

// The webhook worker acts too, and with actions enabled the legacy check-run / gate:* labels are not written.
func TestActionsWorkerPath(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions, gh)
	err := gw.run(&store.Job{Payload: map[string]any{"repo": "o/r", "pr": float64(6), "head_sha": "w1", "source": "webhook", "state": "hold"}})
	if err != nil {
		t.Fatal(err)
	}
	rec := gw.Store.Latest("o/r", "w1")
	if rec == nil || rec.Result.Actions == nil || len(rec.Result.Actions.Applied) == 0 || rec.Result.Verdict != "hold" {
		t.Fatalf("worker did not act: %+v", rec)
	}
	if f.count("POST", "/check-runs") != 0 || f.count("PUT", "/labels") != 0 {
		t.Fatal("legacy check-run/labels written with actions enabled")
	}
	// opted out ("actions": false on /decide async) → no calls
	n := f.total()
	_ = gw.run(&store.Job{Payload: map[string]any{"repo": "o/r", "pr": float64(6), "head_sha": "w2", "source": "api", "state": "pass", "actions": false}})
	if f.total() != n {
		t.Fatal("opted-out job wrote to GitHub")
	}
}
