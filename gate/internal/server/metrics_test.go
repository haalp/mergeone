package server

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestMetricsExposition(t *testing.T) {
	gw, _ := newTestGateway(t, testRules, nil)
	gw.BoundLocal = true // /metrics is open on a loopback bind
	postDecide(t, gw, 1, "m1", "pass")
	postDecide(t, gw, 1, "m2", "hold")
	postDecide(t, gw, 2, "m3", "block")
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("GET", "/metrics", nil))
	if rr.Code != 200 || !strings.HasPrefix(rr.Header().Get("Content-Type"), "text/plain") {
		t.Fatalf("GET /metrics: %d %s", rr.Code, rr.Header().Get("Content-Type"))
	}
	body := rr.Body.String()
	for _, want := range []string{
		`mergeone_decisions_total{verdict="pass",rule="default",source="api"} 1`,
		`mergeone_decisions_total{verdict="hold",rule="maintainer-judgement",source="api"} 1`,
		`mergeone_decisions_total{verdict="block",rule="weakened-tests",source="api"} 1`,
		"# TYPE mergeone_decision_latency_seconds histogram",
		`mergeone_decision_latency_seconds_bucket{le="+Inf"}`,
		"mergeone_model_calls_total 3",
		"mergeone_mode_shadow 0",
		`mergeone_queue_jobs{`,
	} {
		if !strings.Contains(body, want) {
			t.Errorf("missing %q in:\n%s", want, body)
		}
	}
	if strings.Contains(body, "mergeone_breaker_open") {
		t.Error("no breaker lines without a resilience wrapper")
	}
}

func TestStatsModelBlockAndTokens(t *testing.T) {
	gw, _ := newTestGateway(t, testRules, nil)
	postDecide(t, gw, 1, "t1", "pass")
	st := gw.stats(time.Hour)
	m, ok := st["model"].(map[string]any)
	if !ok {
		t.Fatalf("stats lacks model block: %v", st)
	}
	if m["calls"] != 1 || m["retries"] != 0 {
		t.Errorf("model block: %+v", m)
	}
	if _, ok := m["cost_per_decision_usd"]; !ok {
		t.Errorf("cost_per_decision_usd missing: %+v", m)
	}
}

func TestWebhookDeliveryIDOnRecord(t *testing.T) {
	// no GitHub client: the worker cannot fetch the PR, so the decision is the fail-closed worker-error
	// record — which must still carry the delivery id.
	gw := gatewayWith(t, testRules+"debounce: 0\n", testModel, nil, 1)
	gw.BoundLocal = true
	ev := map[string]any{"action": "opened", "repository": map[string]any{"full_name": "o/r"},
		"pull_request": map[string]any{"number": 9, "head": map[string]any{"sha": "d1"}}}
	b, _ := json.Marshal(ev)
	req := httptest.NewRequest("POST", "/webhook/github", bytes.NewReader(b))
	req.Header.Set("X-GitHub-Event", "pull_request")
	req.Header.Set("X-GitHub-Delivery", "72d3162e-cc78-11e3-81ab-4c9367dc0958")
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, req)
	if rr.Code != 200 {
		t.Fatalf("webhook: %d %s", rr.Code, rr.Body.String())
	}
	deadline := time.Now().Add(5 * time.Second)
	for {
		if rec := gw.Store.Latest("o/r", "d1"); rec != nil {
			if rec.DeliveryID != "72d3162e-cc78-11e3-81ab-4c9367dc0958" {
				t.Fatalf("delivery id not on the record: %+v", rec)
			}
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("no record written")
		}
		time.Sleep(10 * time.Millisecond)
	}
	rr = httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("GET", "/metrics", nil))
	if !strings.Contains(rr.Body.String(), `mergeone_webhooks_total{event="pull_request",action="opened"} 1`) {
		t.Errorf("webhook counter missing:\n%s", rr.Body.String())
	}
}
