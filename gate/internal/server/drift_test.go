package server

import (
	"encoding/json"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
	"github.com/haalp/mergeone/gate/internal/store"
)

// The gateway wires the store's degraded set into the gate: after 200 decisions whose raw
// needs_human_review sits far from the calibration's fit_stats, rules reading it give on_drift.
func TestGatewayDriftMonitor(t *testing.T) {
	dir := t.TempDir()
	pp := filepath.Join(dir, "policy.yaml")
	_ = os.WriteFile(pp, []byte(testRules+"drift: {enabled: true}\n"), 0o644)
	p, err := engine.LoadPolicy(pp)
	if err != nil {
		t.Fatal(err)
	}
	cp := filepath.Join(dir, "cal.json")
	_ = os.WriteFile(cp, []byte(`{"fit_stats": {"needs_human_review": {"mean": 0.1, "yes_rate": 0.05, "spread": 0.1, "n": 1000},
		"is_logic_change": {"mean": 0.9, "yes_rate": 1, "spread": 0, "n": 1000}}}`), 0o644)
	cal, err := engine.LoadCalibration(cp)
	if err != nil {
		t.Fatal(err)
	}
	st, _ := store.Open(filepath.Join(dir, "gatelog.jsonl"))
	gw := New(&engine.Gate{Policy: p, Model: testModel, Cal: cal}, st, nil, 0, "")
	t.Cleanup(gw.Stop)

	if r := gw.Decide("o/r", 1, "s0", "hold", nil, "api"); r.Rule != "maintainer-judgement" || r.Drift != nil {
		t.Fatalf("before drift: %+v", r)
	}
	for i := 1; i < engine.R8DriftMinN; i++ {
		gw.Decide("o/r", 1, fmt.Sprintf("s%d", i), "hold", nil, "api")
	}
	r := gw.Decide("o/r", 1, "last", "hold", nil, "api")
	if r.Rule != "on_drift" || r.Reason != "drift:needs_human_review" || r.Verdict != "hold" || r.Route != "human" {
		t.Fatalf("after drift: %+v", r)
	}
	if d := StatusDescription(r); !strings.HasSuffix(d, " · drift: needs_human_review") {
		t.Fatalf("status description %q", d)
	}
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("GET", "/stats", nil))
	var stats struct {
		Drift map[string]engine.ModelDrift `json:"drift"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &stats); err != nil {
		t.Fatal(err)
	}
	md := stats.Drift["keyed-1"]
	if md.Reference != "fit_stats" || len(md.Degraded) != 1 || md.Questions["needs_human_review"].N != engine.R8DriftMinN+1 {
		t.Fatalf("/stats drift block %+v", md)
	}
}

func TestStatusDescriptionKeepsDriftSuffix(t *testing.T) {
	d := StatusDescription(engine.Result{Verdict: "hold", Rule: "r", Reason: strings.Repeat("é", 300), Drift: []string{"a", "b"}})
	if n := len([]rune(d)); n != 140 || !strings.HasSuffix(d, "… · drift: a,b") {
		t.Fatalf("len %d: %q", n, d)
	}
}

func webhookClosed(t *testing.T, gw *Gateway, pr int, merged bool) {
	t.Helper()
	body := fmt.Sprintf(`{"action": "closed", "repository": {"full_name": "o/r"}, "pull_request": {"number": %d, "merged": %v, "head": {"sha": "x"}}}`, pr, merged)
	req := httptest.NewRequest("POST", "/webhook/github", strings.NewReader(body))
	req.Header.Set("X-GitHub-Event", "pull_request")
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("webhook: %d %s", rr.Code, rr.Body)
	}
}

// Outcomes: the webhook's closed event records merged and the iterated proxy (>= 3 decided heads); CI adds
// reverted through POST /api/outcome, merged field by field.
func TestOutcomeRecorder(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+testPortal, nil)
	for i, sha := range []string{"a", "b", "c"} {
		gw.Decide("o/r", 1, sha, fmt.Sprintf("hold %d", i), nil, "api")
	}
	gw.Decide("o/r", 2, "d", "pass", nil, "api")
	webhookClosed(t, gw, 1, true)
	webhookClosed(t, gw, 2, false)
	o1, o2 := gw.Store.OutcomeFor("o/r", 1), gw.Store.OutcomeFor("o/r", 2)
	if o1 == nil || !*o1.Merged || !*o1.Iterated || o1.Reverted != nil || o2 == nil || *o2.Merged || *o2.Iterated {
		t.Fatalf("webhook outcomes %+v %+v", o1, o2)
	}
	if rr := apiCall(t, gw, "POST", "/api/outcome", "s3cret", map[string]any{"repo": "o/r", "pr": 1}); rr.Code != 400 {
		t.Fatalf("no label: %d", rr.Code)
	}
	rr := apiCall(t, gw, "POST", "/api/outcome", "s3cret", map[string]any{"repo": "o/r", "pr": 1, "reverted": true})
	var got struct {
		OK      bool            `json:"ok"`
		Outcome store.PROutcome `json:"outcome"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &got); err != nil || rr.Code != 200 || !got.OK || !*got.Outcome.Reverted || !*got.Outcome.Merged || !*got.Outcome.Iterated {
		t.Fatalf("api outcome %d %s", rr.Code, rr.Body)
	}
	if rr := apiCall(t, gw, "POST", "/api/outcome", "", map[string]any{"repo": "o/r", "pr": 1, "reverted": true}); rr.Code != 401 {
		t.Fatalf("outcome without token: %d", rr.Code)
	}
}

// POST /api/calibrate refits from the gatelog, writes a timestamped file beside it and loads nothing.
func TestAPICalibrate(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+testPortal, nil)
	now := time.Now().UTC()
	for i := 1; i <= 220; i++ {
		y := i%4 == 0
		p := 0.3
		if y {
			p = 0.7
		}
		r := store.Record{Repo: "o/r", PR: i, HeadSHA: fmt.Sprintf("c%d", i), Result: engine.Result{Verdict: "pass",
			RawProbs: map[string]float64{"needs_human_review": p}, ModelVersion: "keyed-1", InputSHA256: fmt.Sprint(i), TS: now.Add(-time.Hour)}}
		_ = gw.Store.Record(r)
		_, _ = gw.Store.RecordOutcome(store.PROutcome{Repo: "o/r", PR: i, Iterated: &y})
	}
	before := gw.Gate.Cal
	rr := apiCall(t, gw, "POST", "/api/calibrate", "s3cret", nil)
	var got struct {
		OK      bool   `json:"ok"`
		File    string `json:"file"`
		Summary struct {
			N             int      `json:"n"`
			ModelVersions []string `json:"model_versions"`
		} `json:"summary"`
	}
	if err := json.Unmarshal(rr.Body.Bytes(), &got); err != nil || rr.Code != 200 || !got.OK || got.Summary.N != 220 {
		t.Fatalf("calibrate %d %s", rr.Code, rr.Body)
	}
	c, err := engine.LoadCalibration(got.File)
	if err != nil || c.Meta == nil || c.FitStats["needs_human_review"].N != 220 || !strings.Contains(filepath.Base(got.File), ".gatelog-") {
		t.Fatalf("written map %v %+v", err, c)
	}
	if gw.Gate.Cal != before {
		t.Fatal("the running gateway's map must not change")
	}
}
