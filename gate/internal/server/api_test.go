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
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

const testPortal = `portal:
  api_token: s3cret
  origin: http://localhost:3000
`

// apiCall runs one /api request with the test token (tok "" = no Authorization header).
func apiCall(t *testing.T, gw *Gateway, method, path, tok string, body any) *httptest.ResponseRecorder {
	t.Helper()
	var rd *bytes.Reader
	if body != nil {
		b, _ := json.Marshal(body)
		rd = bytes.NewReader(b)
	} else {
		rd = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rd)
	if tok != "" {
		req.Header.Set("Authorization", "Bearer "+tok)
	}
	req.Header.Set("Origin", "http://localhost:3000")
	req.Header.Set(LoginHeader, "maria")
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, req)
	return rr
}

// seed: #1 hold→human, #2 block (closed later), #3 pass, #4 hold→dedup matching #1 (with context).
func seedQueue(t *testing.T, gw *Gateway) {
	gw.Decide("o/r", 1, "sha1", "hold", nil, "api")
	gw.Decide("o/r", 2, "sha2", "block", nil, "api")
	gw.Decide("o/r", 3, "sha3", "pass", nil, "api")
	res := engine.Result{Verdict: "hold", Route: "dedup", Rule: "duplicate-of", Reason: "Looks like the same change as an existing PR (#1)",
		Probs: map[string]float64{"duplicate_of": 0.91}, RawProbs: map[string]float64{"duplicate_of": 0.88}, ModelVersion: "keyed-1",
		InputSHA256: "h4", TS: time.Date(2026, 10, 25, 9, 0, 0, 0, time.UTC), Extra: map[string]any{"duplicate_candidate": 1}}
	ctx := PRContext(engine.PRMeta{Author: "swarm-07", Base: "main", Title: "Fix nil deref", CreatedAt: time.Date(2026, 10, 25, 8, 0, 0, 0, time.UTC)},
		[]FileStat{{"pkg/a.go", 14, 4}, {"pkg/b.go", 4, 0}})
	state := "PULL REQUEST TITLE: Fix nil deref\nMERGE MESSAGE BODY:\n\n\nSUMMARY: 2 files changed, +18 / -4 lines, 1 top-level areas touched.\n"
	if err := gw.Store.Record(store.Record{Repo: "o/r", PR: 4, HeadSHA: "sha4", Source: "webhook", Result: res, Context: ctx, State: state}); err != nil {
		t.Fatal(err)
	}
	gw.Store.ClosePR("o/r", 2)
}

func TestAPIAuthAndCORS(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+testPortal, nil)
	if rr := apiCall(t, gw, "GET", "/api/queue", "", nil); rr.Code != 401 {
		t.Fatalf("no token: %d", rr.Code)
	}
	if rr := apiCall(t, gw, "GET", "/api/queue", "wrong", nil); rr.Code != 401 {
		t.Fatalf("wrong token: %d", rr.Code)
	}
	rr := apiCall(t, gw, "GET", "/api/queue", "s3cret", nil)
	if rr.Code != 200 || rr.Header().Get("Access-Control-Allow-Origin") != "http://localhost:3000" || rr.Header().Get("Access-Control-Allow-Credentials") != "true" {
		t.Fatalf("token: %d %v", rr.Code, rr.Header())
	}
	pre := apiCall(t, gw, "OPTIONS", "/api/resolve", "", nil)
	if pre.Code != 204 || !strings.Contains(pre.Header().Get("Access-Control-Allow-Headers"), "Authorization") {
		t.Fatalf("preflight: %d %v", pre.Code, pre.Header())
	}

	open, _ := newTestGateway(t, testRules, nil) // no api_token
	if rr := apiCall(t, open, "GET", "/api/queue", "", nil); rr.Code != 403 {
		t.Fatalf("no api_token, not loopback: %d", rr.Code)
	}
	open.BoundLocal = true
	if rr := apiCall(t, open, "GET", "/api/queue", "", nil); rr.Code != 200 || rr.Header().Get("Access-Control-Allow-Origin") != "" {
		t.Fatalf("loopback without token: %d %v", rr.Code, rr.Header())
	}
	for addr, want := range map[string]bool{"127.0.0.1:8787": true, "localhost:1": true, "[::1]:80": true, ":8787": false, "0.0.0.0:80": false} {
		if isLoopback(addr) != want {
			t.Errorf("isLoopback(%q) != %v", addr, want)
		}
	}
}

func TestAPIQueueGraphVerdict(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+testPortal, nil)
	seedQueue(t, gw)
	rr := apiCall(t, gw, "GET", "/api/queue", "s3cret", nil)
	var q struct {
		Holds   []store.Record          `json:"holds"`
		Matches map[string]store.Record `json:"matches"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &q); err != nil {
		t.Fatal(err)
	}
	if len(q.Holds) != 2 || q.Holds[0].PR != 1 || q.Holds[1].PR != 4 { // #2 closed, #3 pass; oldest first
		t.Fatalf("holds %+v", q.Holds)
	}
	if m, ok := q.Matches["o/r#1"]; !ok || m.HeadSHA != "sha1" || len(q.Matches) != 1 {
		t.Fatalf("matches %+v", q.Matches)
	}
	if c := q.Holds[1].Context; c["author"] != "swarm-07" || c["base"] != "main" || c["created_at"] != "2026-10-25T08:00:00Z" || c["additions"] != 18.0 || c["files"] != 2.0 {
		t.Fatalf("context %+v", c)
	}
	if os.Getenv("MERGEONE_PRINT_HOLD") != "" {
		var raw struct {
			Holds []json.RawMessage `json:"holds"`
		}
		_ = json.Unmarshal(rr.Body.Bytes(), &raw)
		fmt.Println(string(raw.Holds[1]))
	}

	rr = apiCall(t, gw, "GET", "/api/graph?repo=o/r&since=7d", "s3cret", nil)
	var g struct {
		Nodes []GraphNode      `json:"nodes"`
		Edges []map[string]any `json:"edges"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &g); err != nil {
		t.Fatal(err)
	}
	if len(g.Nodes) != 3 || len(g.Edges) != 1 {
		t.Fatalf("graph %+v", g)
	}
	e := g.Edges[0]
	if e["source"] != "o/r#4" || e["target"] != "o/r#1" || e["relation"] != "duplicate_of" || e["p"] != 0.91 {
		t.Fatalf("edge %+v", e)
	}
	if _, typed := e["type"]; typed {
		t.Fatal("no relation records exist: an edge has no type")
	}
	for _, n := range g.Nodes {
		if n.PR == 4 && (n.Title != "Fix nil deref" || n.Additions != 18 || n.Deletions != 4 || n.Files != 2 || n.Author != "swarm-07") {
			t.Fatalf("node #4 %+v", n)
		}
	}
	if rr := apiCall(t, gw, "GET", "/api/graph?since=2999-01-01T00:00:00Z", "s3cret", nil); !strings.Contains(rr.Body.String(), `"nodes":[]`) {
		t.Fatalf("since filter: %s", rr.Body.String())
	}

	rr = apiCall(t, gw, "GET", "/api/verdict/sha4?repo=o/r", "s3cret", nil)
	var rec store.Record
	_ = json.Unmarshal(rr.Body.Bytes(), &rec)
	if rr.Code != 200 || rec.PR != 4 || !strings.HasPrefix(rec.State, "PULL REQUEST TITLE") {
		t.Fatalf("verdict: %d %+v", rr.Code, rec)
	}
	if rr := apiCall(t, gw, "GET", "/api/verdict/sha1", "s3cret", nil); rr.Code != 200 {
		t.Fatalf("verdict without repo: %d", rr.Code)
	}
	if rr := apiCall(t, gw, "GET", "/api/verdict/nope?repo=o/r", "s3cret", nil); rr.Code != 404 {
		t.Fatalf("unknown sha: %d", rr.Code)
	}
	if rr := apiCall(t, gw, "POST", "/api/calibrate", "s3cret", map[string]string{"since": "7d"}); rr.Code != 422 || !strings.Contains(rr.Body.String(), "need at least 200") {
		t.Fatalf("calibrate: %d %s", rr.Code, rr.Body.String())
	}
}

func TestAPIResolve(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, logPath := newTestGateway(t, testRules+testPortal+"actions: {enabled: true}\n", gh)
	seedQueue(t, gw)
	before := f.total()

	for _, bad := range []map[string]any{
		{"repo": "o/r", "pr": 4, "head_sha": "sha4", "action": "merge"},
		{"repo": "o/r", "pr": 4, "action": "approve"},
		{"repo": "o/r", "pr": 3, "head_sha": "sha3", "action": "link_duplicate"}, // no candidate, no target
	} {
		if rr := apiCall(t, gw, "POST", "/api/resolve", "s3cret", bad); rr.Code != 400 {
			t.Fatalf("%v: %d", bad, rr.Code)
		}
	}
	if rr := apiCall(t, gw, "POST", "/api/resolve", "s3cret", map[string]any{"repo": "o/r", "pr": 9, "head_sha": "sha4", "action": "approve"}); rr.Code != 404 {
		t.Fatalf("pr/sha mismatch: %d", rr.Code)
	}

	rr := apiCall(t, gw, "POST", "/api/resolve", "s3cret", map[string]any{"repo": "o/r", "pr": 4, "head_sha": "sha4", "action": "link_duplicate"})
	var out struct {
		OK         bool             `json:"ok"`
		LoggedAt   string           `json:"logged_at"`
		Resolution store.Resolution `json:"resolution"`
	}
	_ = json.Unmarshal(rr.Body.Bytes(), &out)
	r := out.Resolution
	if rr.Code != 200 || !out.OK || out.LoggedAt == "" || r.Login != "maria" || r.TargetPR != 1 || r.Rule != "duplicate-of" || r.Actions == nil {
		t.Fatalf("resolve: %d %s", rr.Code, rr.Body.String())
	}
	if got := f.labels[4]; len(got) != 1 || got[0] != "mergeone:duplicate" || len(f.comments[4]) != 1 || !strings.Contains(f.comments[4][0], ResolveMarker("sha4", "link_duplicate")) {
		t.Fatalf("mirror: labels %v comments %v", got, f.comments[4])
	}
	if f.count("POST", "/statuses/") != 0 || f.count("PUT", "/merge") != 0 || f.count("PATCH", "") != 0 {
		t.Fatalf("a resolution never touches status, merge or state: %+v", f.reqs[before:])
	}
	// same answer again: no second comment; approve then swaps the resolution label
	apiCall(t, gw, "POST", "/api/resolve", "s3cret", map[string]any{"repo": "o/r", "pr": 4, "head_sha": "sha4", "action": "link_duplicate"})
	apiCall(t, gw, "POST", "/api/resolve", "s3cret", map[string]any{"repo": "o/r", "pr": 1, "head_sha": "sha1", "action": "approve"})
	if len(f.comments[4]) != 1 || len(f.labels[1]) == 0 || !contains(f.labels[1], "mergeone:approved") {
		t.Fatalf("idempotent comment / approve label: %v %v", f.comments[4], f.labels[1])
	}

	var q struct {
		Holds []store.Record `json:"holds"`
	}
	_ = json.Unmarshal(apiCall(t, gw, "GET", "/api/queue", "s3cret", nil).Body.Bytes(), &q)
	if len(q.Holds) != 0 {
		t.Fatalf("resolved heads leave the queue: %+v", q.Holds)
	}
	var st struct {
		Resolutions struct {
			Total, Agreed, ApprovedAsIs, Linked int
			ByRule                              map[string]map[string]int `json:"by_rule"`
		} `json:"resolutions"`
		History struct {
			Daily []store.DailyRow `json:"daily"`
		} `json:"history"`
	}
	_ = json.Unmarshal(apiCall(t, gw, "GET", "/api/stats?window=7d", "s3cret", nil).Body.Bytes(), &st)
	res := st.Resolutions
	if res.Total != 3 || res.Agreed != 2 || res.ByRule["maintainer-judgement"]["overridden"] != 1 || res.ByRule["duplicate-of"]["agreed"] != 2 {
		t.Fatalf("resolutions block %+v", res)
	}
	if n := len(st.History.Daily); n == 0 || st.History.Daily[n-1].Decisions < 3 {
		t.Fatalf("history.daily %+v", st.History.Daily)
	}

	// resolutions and closures survive a restart
	s2, err := store.Open(logPath)
	if err != nil {
		t.Fatal(err)
	}
	if r := s2.ResolutionFor("o/r", "sha4"); r == nil || r.Login != "maria" || len(s2.Queue("")) != 0 {
		t.Fatalf("replay: %+v queue %d", r, len(s2.Queue("")))
	}
	if recs := s2.PRRecords("o/r", time.Time{}); len(recs) != 3 {
		t.Fatalf("closed #2 stays closed after replay: %d records", len(recs))
	}
}

func contains(xs []string, x string) bool {
	for _, v := range xs {
		if v == x {
			return true
		}
	}
	return false
}

// The worker fills Record.Context from the PR it fetched.
func TestWorkerRecordsPRContext(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case strings.HasSuffix(r.URL.Path, "/pulls/5/files"):
			fmt.Fprint(w, `[{"filename":"a.go","additions":7,"deletions":2},{"filename":"b.go","additions":1,"deletions":0}]`)
		case strings.HasSuffix(r.URL.Path, "/pulls/5") && strings.Contains(r.Header.Get("Accept"), "diff"):
			fmt.Fprint(w, "diff --git a/a.go b/a.go\n")
		case strings.HasSuffix(r.URL.Path, "/pulls/5"):
			fmt.Fprint(w, `{"title":"hold","body":"","head":{"sha":"h5"},"base":{"ref":"release-1.2"},"user":{"login":"agent-3"},"created_at":"2026-10-20T10:00:00Z"}`)
		default:
			w.WriteHeader(404)
		}
	}))
	t.Cleanup(srv.Close)
	gh := NewGitHub("tok")
	gh.API = srv.URL
	dir := t.TempDir()
	pp := filepath.Join(dir, "p.yaml")
	_ = os.WriteFile(pp, []byte(testRules), 0o644)
	p, _ := engine.LoadPolicy(pp)
	st, _ := store.Open(filepath.Join(dir, "g.jsonl"))
	gw := New(&engine.Gate{Policy: p, Model: testModel, Cal: &engine.Calibrator{}}, st, gh, 1, "")
	t.Cleanup(gw.Stop)
	gw.Submit("o/r", 5, "h5", "webhook", nil)
	var rec *store.Record
	for i := 0; i < 200 && rec == nil; i++ {
		time.Sleep(10 * time.Millisecond)
		rec = st.LatestForPR("o/r", 5)
	}
	if rec == nil {
		t.Fatal("no record")
	}
	c := rec.Context
	if c["author"] != "agent-3" || c["base"] != "release-1.2" || c["created_at"] != "2026-10-20T10:00:00Z" || c["additions"] != 8 || c["deletions"] != 2 || c["files"] != 2 || c["title"] != "hold" {
		t.Fatalf("context %+v", c)
	}
}

// With portal: {url, token} every gatelog record is also posted to <url>/ingest.
func TestPortalIngest(t *testing.T) {
	var mu sync.Mutex
	var got []store.Record
	var auth string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var body struct {
			Records []store.Record `json:"records"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		mu.Lock()
		got = append(got, body.Records...)
		auth = r.Header.Get("Authorization")
		mu.Unlock()
		w.WriteHeader(202)
	}))
	t.Cleanup(srv.Close)
	t.Setenv("TEST_PORTAL_TOKEN", "ptok")
	gw, _ := newTestGateway(t, testRules+"portal: {url: \""+srv.URL+"/\", token: \"${TEST_PORTAL_TOKEN}\"}\n", nil)
	if gw.Portal == nil {
		t.Fatal("portal block set: want a sink")
	}
	gw.Decide("o/r", 1, "a", "hold", nil, "api")
	gw.Decide("o/r", 2, "b", "pass", nil, "api")
	gw.Portal.Close(5 * time.Second)
	mu.Lock()
	defer mu.Unlock()
	if len(got) != 2 || got[0].HeadSHA != "a" || got[1].Result.Verdict != "pass" || auth != "Bearer ptok" {
		t.Fatalf("ingest got %d records, auth %q", len(got), auth)
	}
	var stats map[string]any
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("GET", "/stats", nil))
	_ = json.Unmarshal(rr.Body.Bytes(), &stats)
	if ps, ok := stats["portal"].(map[string]any); !ok || ps["sent"] != 2.0 || ps["dropped"] != 0.0 {
		t.Fatalf("/stats portal block %v", stats["portal"])
	}
}
