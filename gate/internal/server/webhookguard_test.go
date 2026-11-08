package server

import (
	"bytes"
	"encoding/json"
	"net/http/httptest"
	"testing"
	"time"
)

func TestGuardDuplicateDelivery(t *testing.T) {
	g := newWebhookGuard(0)
	now := time.Date(2026, 11, 8, 12, 0, 0, 0, time.UTC)
	g.now = func() time.Time { return now }
	if g.duplicate("") || g.duplicate("") {
		t.Fatal("an empty id is never a duplicate")
	}
	if g.duplicate("d1") {
		t.Fatal("first delivery flagged")
	}
	if !g.duplicate("d1") {
		t.Fatal("second delivery not flagged")
	}
	now = now.Add(DeliveryTTL + time.Minute)
	if g.duplicate("d1") {
		t.Fatal("an id older than the TTL is forgotten")
	}
}

func TestGuardRateLimitPerRepo(t *testing.T) {
	g := newWebhookGuard(60) // 1/s, burst 60
	now := time.Date(2026, 11, 8, 12, 0, 0, 0, time.UTC)
	g.now = func() time.Time { return now }
	for i := 0; i < 60; i++ {
		if ok, _ := g.allow("o/a"); !ok {
			t.Fatalf("burst exhausted at %d", i)
		}
	}
	ok, wait := g.allow("o/a")
	if ok || wait <= 0 || wait > time.Second {
		t.Fatalf("61st should wait ≈1s, got ok=%v wait=%v", ok, wait)
	}
	if ok, _ := g.allow("o/b"); !ok {
		t.Fatal("another repository has its own bucket")
	}
	now = now.Add(2 * time.Second)
	if ok, _ := g.allow("o/a"); !ok {
		t.Fatal("tokens refill with time")
	}
}

func webhookReq(delivery, action string) *httptest.ResponseRecorder {
	ev := map[string]any{"action": action, "repository": map[string]any{"full_name": "o/r"},
		"pull_request": map[string]any{"number": 3, "head": map[string]any{"sha": "w1"}}}
	b, _ := json.Marshal(ev)
	req := httptest.NewRequest("POST", "/webhook/github", bytes.NewReader(b))
	req.Header.Set("X-GitHub-Event", "pull_request")
	if delivery != "" {
		req.Header.Set("X-GitHub-Delivery", delivery)
	}
	return httptest.NewRecorder()
}

func TestWebhookUnsignedPolicy(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+"debounce: 0\n", nil)
	gw.AllowUnsignedWebhooks = false
	ev, _ := json.Marshal(map[string]any{"action": "opened", "repository": map[string]any{"full_name": "o/r"},
		"pull_request": map[string]any{"number": 3, "head": map[string]any{"sha": "w1"}}})
	post := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "/webhook/github", bytes.NewReader(ev))
		req.Header.Set("X-GitHub-Event", "pull_request")
		req.Header.Set("X-GitHub-Delivery", "u1")
		rr := httptest.NewRecorder()
		gw.Handler("").ServeHTTP(rr, req)
		return rr
	}
	if rr := post(); rr.Code != 401 {
		t.Fatalf("no secret + public bind must refuse: %d %s", rr.Code, rr.Body)
	}
	gw.BoundLocal = true
	if rr := post(); rr.Code != 200 {
		t.Fatalf("loopback bind accepts unsigned: %d %s", rr.Code, rr.Body)
	}
	// the same delivery again is a duplicate (and nothing is enqueued twice)
	rr := post()
	var out map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &out)
	if rr.Code != 200 || out["duplicate"] != true {
		t.Fatalf("redelivery should be flagged: %d %s", rr.Code, rr.Body)
	}
}

func TestWebhookRateLimited(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+"debounce: 0\n", nil)
	gw.SetWebhookRate(2)
	ev, _ := json.Marshal(map[string]any{"action": "opened", "repository": map[string]any{"full_name": "o/r"},
		"pull_request": map[string]any{"number": 3, "head": map[string]any{"sha": "w1"}}})
	codes := []int{}
	for i := 0; i < 3; i++ {
		req := httptest.NewRequest("POST", "/webhook/github", bytes.NewReader(ev))
		req.Header.Set("X-GitHub-Event", "pull_request")
		rr := httptest.NewRecorder()
		gw.Handler("").ServeHTTP(rr, req)
		codes = append(codes, rr.Code)
		if rr.Code == 429 && rr.Header().Get("Retry-After") == "" {
			t.Fatal("429 without Retry-After")
		}
	}
	if codes[0] != 200 || codes[1] != 200 || codes[2] != 429 {
		t.Fatalf("want 200 200 429, got %v", codes)
	}
}
