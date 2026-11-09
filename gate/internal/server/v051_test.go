package server

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// A delivery refused with 429 is not remembered: GitHub's redelivery of the same id is decided.
func TestRateLimitedDeliveryIsNotADuplicateOnRedelivery(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+"debounce: 0\n", nil)
	gw.SetWebhookRate(1)
	ev, _ := json.Marshal(map[string]any{"action": "opened", "repository": map[string]any{"full_name": "o/r"},
		"pull_request": map[string]any{"number": 3, "head": map[string]any{"sha": "w1"}}})
	post := func(id string) (int, string) {
		req := httptest.NewRequest("POST", "/webhook/github", bytes.NewReader(ev))
		req.Header.Set("X-GitHub-Event", "pull_request")
		req.Header.Set("X-GitHub-Delivery", id)
		rr := httptest.NewRecorder()
		gw.Handler("").ServeHTTP(rr, req)
		return rr.Code, rr.Body.String()
	}
	if c, _ := post("a"); c != 200 {
		t.Fatal(c)
	}
	if c, _ := post("b"); c != 429 {
		t.Fatalf("second should be rate limited, got %d", c)
	}
	gw.SetWebhookRate(100) // the bucket refills; GitHub redelivers "b"
	gw.guard.seen["a"] = gw.guard.now()
	c, body := post("b")
	if c != 200 || strings.Contains(body, "duplicate") {
		t.Fatalf("redelivery of a rate-limited delivery must be accepted: %d %s", c, body)
	}
}

func TestMetricsNeedsTokenOffLoopback(t *testing.T) {
	gw, _ := newTestGateway(t, testRules, nil)
	get := func(auth string) int {
		req := httptest.NewRequest("GET", "/metrics", nil)
		if auth != "" {
			req.Header.Set("Authorization", auth)
		}
		rr := httptest.NewRecorder()
		gw.Handler("").ServeHTTP(rr, req)
		return rr.Code
	}
	gw.BoundLocal = false
	if get("") != 403 {
		t.Fatal("open /metrics on a public bind")
	}
	t.Setenv("MERGEONE_METRICS_TOKEN", "s3cret")
	if get("Bearer nope") != 403 || get("Bearer s3cret") != 200 {
		t.Fatal("bearer check")
	}
	gw.BoundLocal = true
	if get("") != 200 {
		t.Fatal("loopback stays open")
	}
}

func TestLogStateHashKeepsOnlyTitleAndSummary(t *testing.T) {
	gw, logPath := newTestGateway(t, testRules+"log_state: hash\n", nil)
	state := "PULL REQUEST TITLE: fix the thing\nBODY: secret words\nSUMMARY: 2 files changed, +3 / -1 lines\nDIFF:\n+password = 1\npass"
	body, _ := json.Marshal(map[string]any{"repo": "o/r", "pr": 1, "head_sha": "h1", "state": state})
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("POST", "/decide", bytes.NewReader(body)))
	if rr.Code != 200 {
		t.Fatalf("%d %s", rr.Code, rr.Body)
	}
	b, _ := os.ReadFile(logPath)
	if strings.Contains(string(b), "password") || strings.Contains(string(b), "secret words") {
		t.Fatalf("log_state: hash leaked the body or diff:\n%s", b)
	}
	if !strings.Contains(string(b), "fix the thing") || !strings.Contains(string(b), "2 files changed") || !strings.Contains(string(b), "input_sha256") {
		t.Fatalf("title, summary and the input hash must stay:\n%s", b)
	}
}
