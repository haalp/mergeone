package server

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// countingModel answers every state with the "hold" probabilities and counts its calls.
type countingModel struct{ n atomic.Int64 }

func (m *countingModel) Name() string { return "counting" }
func (m *countingModel) Decide(state string, qs []string) engine.Decision {
	m.n.Add(1)
	return engine.Decision{Probs: testModel["hold"], ModelVersion: "counting-1", CostUSD: 0.001}
}

func gatewayWith(t *testing.T, policyYAML string, m engine.Model, gh *GitHub, workers int) *Gateway {
	dir := t.TempDir()
	pp := filepath.Join(dir, "policy.yaml")
	if err := os.WriteFile(pp, []byte(policyYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	p, err := engine.LoadPolicy(pp)
	if err != nil {
		t.Fatal(err)
	}
	st, err := store.Open(filepath.Join(dir, "gatelog.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	gw := New(&engine.Gate{Policy: p, Model: m, Cal: &engine.Calibrator{}}, st, gh, workers, "")
	gw.AllowUnsignedWebhooks = true // tests call Handler directly without a secret
	t.Cleanup(gw.Stop)
	return gw
}

// renderedState is a render_state text over diff, as the CI path posts it to /decide.
func renderedState(diff string) string {
	return RenderState("fix sum", "", []FileStat{{"pkg/x.go", 2, 1}}, diff)
}

const diffBaseA = "diff --git a/pkg/x.go b/pkg/x.go\nindex 1111111..2222222 100644\n--- a/pkg/x.go\n+++ b/pkg/x.go\n" +
	"@@ -10,4 +10,5 @@ func Run() {\n \ta := 1\n-\treturn a\n+\tb := 2\n+\treturn a + b\n }\n"

// The same patch rebased: new blob ids and hunk position.
const diffBaseB = "diff --git a/pkg/x.go b/pkg/x.go\nindex 3333333..4444444 100644\n--- a/pkg/x.go\n+++ b/pkg/x.go\n" +
	"@@ -57,4 +57,5 @@ func Run() {\n \ta := 1\n-\treturn a\n+\tb := 2\n+\treturn a + b\n }\n"

func TestReuseVerdictOnSamePatch(t *testing.T) {
	m := &countingModel{}
	gw := gatewayWith(t, testRules, m, nil, 0)
	if !gw.Gate.Policy.ReuseVerdictOnSamePatch || gw.Gate.Policy.Debounce != engine.DefaultDebounce {
		t.Fatalf("defaults: reuse %v debounce %v", gw.Gate.Policy.ReuseVerdictOnSamePatch, gw.Gate.Policy.Debounce)
	}
	r1 := postDecide(t, gw, 9, "sha1", renderedState(diffBaseA))
	r2 := postDecide(t, gw, 9, "sha2", renderedState(diffBaseB))
	if n := m.n.Load(); n != 1 {
		t.Fatalf("model called %d times, want 1 (second head is a rebase)", n)
	}
	if r2.Verdict != r1.Verdict || r2.Rule != r1.Rule || r2.Route != r1.Route || r2.Probs["needs_human_review"] != r1.Probs["needs_human_review"] || r2.CostUSD != 0 {
		t.Fatalf("reused result %+v vs %+v", r2, r1)
	}
	a, b := gw.Store.Latest("o/r", "sha1"), gw.Store.Latest("o/r", "sha2")
	if a.PatchID == "" || a.PatchID != b.PatchID || a.ReusedFrom != "" || b.ReusedFrom != "sha1" || b.HeadSHA != "sha2" || a.PolicyHash == "" || a.PolicyHash != b.PolicyHash {
		t.Fatalf("records: %+v / %+v", a, b)
	}
	// a changed line is a new patch: the model is asked
	postDecide(t, gw, 9, "sha3", renderedState(strings.Replace(diffBaseB, "a + b", "a - b", 1)))
	if n := m.n.Load(); n != 2 {
		t.Fatalf("changed patch: model called %d times, want 2", n)
	}
	// the same patch on another PR is not reused
	postDecide(t, gw, 10, "sha4", renderedState(diffBaseA))
	if n := m.n.Load(); n != 3 {
		t.Fatalf("other PR: model called %d times, want 3", n)
	}
	st := gw.Store.Stats(time.Hour)
	if st["reused"] != 1 || st["decisions"] != 4 {
		t.Fatalf("stats reused %v decisions %v", st["reused"], st["decisions"])
	}
	// the drift ring counted the reused record's answers once (3 model decisions, not 4)
	drift := gw.Store.Drift(time.Now())
	if len(drift) != 1 {
		t.Fatalf("drift models %v", drift)
	}
	for _, md := range drift {
		if q := md.Questions["needs_human_review"]; q.N != 3 {
			t.Fatalf("drift n = %d, want 3", q.N)
		}
	}
}

func TestReuseVerdictOff(t *testing.T) {
	m := &countingModel{}
	gw := gatewayWith(t, testRules+"reuse_verdict_on_same_patch: false\n", m, nil, 0)
	postDecide(t, gw, 9, "sha1", renderedState(diffBaseA))
	postDecide(t, gw, 9, "sha2", renderedState(diffBaseB))
	if n := m.n.Load(); n != 2 {
		t.Fatalf("reuse off: model called %d times, want 2", n)
	}
	b := gw.Store.Latest("o/r", "sha2")
	if b.ReusedFrom != "" || b.PatchID == "" {
		t.Fatalf("record %+v", b)
	}
}

// A policy change (new hash) is not crossed by reuse.
func TestReuseNeedsSamePolicy(t *testing.T) {
	m := &countingModel{}
	gw := gatewayWith(t, testRules, m, nil, 0)
	postDecide(t, gw, 9, "sha1", renderedState(diffBaseA))
	gw.Gate.Policy.Rules[0].Reason = "changed"
	postDecide(t, gw, 9, "sha2", renderedState(diffBaseB))
	if n := m.n.Load(); n != 2 {
		t.Fatalf("policy changed: model called %d times, want 2", n)
	}
}

// ---- debounce

// fakePR serves one PR whose head can be moved, records commit statuses, and counts decisions.
type fakePR struct {
	mu       sync.Mutex
	head     string
	statuses []map[string]any
	paths    []string
}

func (f *fakePR) serve(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch {
	case r.Method == "POST" && strings.Contains(r.URL.Path, "/statuses/"):
		var body map[string]any
		_ = json.NewDecoder(r.Body).Decode(&body)
		body["sha"] = r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]
		f.statuses = append(f.statuses, body)
		w.WriteHeader(201)
		fmt.Fprint(w, `{}`)
	case strings.HasSuffix(r.URL.Path, "/pulls/7/files"):
		fmt.Fprint(w, `[{"filename":"pkg/x.go","additions":2,"deletions":1}]`)
	case strings.HasSuffix(r.URL.Path, "/pulls/7") && strings.Contains(r.Header.Get("Accept"), "diff"):
		fmt.Fprint(w, diffBaseA)
	case strings.HasSuffix(r.URL.Path, "/pulls/7"):
		fmt.Fprintf(w, `{"title":"fix sum","body":"","head":{"sha":%q},"base":{"ref":"main"},"user":{"login":"agent"}}`, f.head)
	default:
		w.WriteHeader(404)
	}
}

func (f *fakePR) statusesFor(sha string) []string {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []string
	for _, s := range f.statuses {
		if s["sha"] == sha {
			out = append(out, fmt.Sprint(s["state"]))
		}
	}
	return out
}

func pushWebhook(t *testing.T, h http.Handler, action string, pr int, sha string) map[string]any {
	body := fmt.Sprintf(`{"action":%q,"repository":{"full_name":"o/r"},"pull_request":{"number":%d,"head":{"sha":%q}}}`, action, pr, sha)
	req := httptest.NewRequest("POST", "/webhook/github", bytes.NewReader([]byte(body)))
	req.Header.Set("X-GitHub-Event", "pull_request")
	rr := httptest.NewRecorder()
	h.ServeHTTP(rr, req)
	var out map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &out)
	return out
}

func newDebounceGateway(t *testing.T, policyYAML string) (*Gateway, *fakePR, *countingModel) {
	f := &fakePR{}
	srv := httptest.NewServer(http.HandlerFunc(f.serve))
	t.Cleanup(srv.Close)
	gh := NewGitHub("tok")
	gh.API = srv.URL
	m := &countingModel{}
	return gatewayWith(t, policyYAML, m, gh, 1), f, m
}

func waitFor(t *testing.T, what string, ok func() bool) {
	t.Helper()
	for i := 0; i < 300; i++ {
		if ok() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

func TestDebounceCoalescesPushes(t *testing.T) {
	gw, f, m := newDebounceGateway(t, testRules+testActions+"debounce: 150ms\n")
	h := gw.Handler("")
	for _, sha := range []string{"c1", "c2", "c3"} {
		f.mu.Lock()
		f.head = sha
		f.mu.Unlock()
		if out := pushWebhook(t, h, "synchronize", 7, sha); out["debounced"] != true {
			t.Fatalf("push %s not debounced: %v", sha, out)
		}
		time.Sleep(30 * time.Millisecond) // well inside the window
	}
	waitFor(t, "the decision", func() bool { return gw.Store.LatestForPR("o/r", 7) != nil })
	time.Sleep(100 * time.Millisecond) // nothing else should arrive
	if n := m.n.Load(); n != 1 {
		t.Fatalf("model called %d times, want 1", n)
	}
	rec := gw.Store.LatestForPR("o/r", 7)
	if rec.HeadSHA != "c3" || strings.Join(rec.DebouncedSHAs, ",") != "c1,c2" || rec.PatchID == "" {
		t.Fatalf("record head %s debounced %v patch %q", rec.HeadSHA, rec.DebouncedSHAs, rec.PatchID)
	}
	if gw.Store.Latest("o/r", "c1") != nil || gw.Store.Latest("o/r", "c2") != nil {
		t.Fatal("a superseded head was decided")
	}
	// every pushed head was marked pending; the head that ran got the verdict's status (hold → failure)
	for _, sha := range []string{"c1", "c2"} {
		if s := f.statusesFor(sha); len(s) != 1 || s[0] != "pending" {
			t.Fatalf("%s statuses %v", sha, s)
		}
	}
	waitFor(t, "c3 statuses", func() bool { return len(f.statusesFor("c3")) == 2 })
	if s := f.statusesFor("c3"); s[0] != "pending" || s[1] != "failure" {
		t.Fatalf("c3 statuses %v", s)
	}
}

func TestDebounceOffDecidesEveryPush(t *testing.T) {
	gw, f, m := newDebounceGateway(t, testRules+"reuse_verdict_on_same_patch: false\ndebounce: 0\n")
	h := gw.Handler("")
	for _, sha := range []string{"d1", "d2"} {
		f.mu.Lock()
		f.head = sha
		f.mu.Unlock()
		if out := pushWebhook(t, h, "synchronize", 7, sha); out["queued"] != true {
			t.Fatalf("push %s: %v", sha, out)
		}
		waitFor(t, "decision "+sha, func() bool { return gw.Store.Latest("o/r", sha) != nil })
	}
	if n := m.n.Load(); n != 2 {
		t.Fatalf("model called %d times, want 2", n)
	}
	if len(f.statusesFor("d1")) != 0 {
		t.Fatal("pending status posted with debounce off")
	}
}
