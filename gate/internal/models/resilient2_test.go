package models

import (
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func TestPairFailuresDoNotOpenTheBreaker(t *testing.T) {
	errs := make([]string, 12)
	for i := range errs {
		errs[i] = "HTTP 500: x"
	}
	in := &scripted{errs: errs}
	r, _, _ := newRes(in, Resilience{Retries: 0, BreakerFailures: 3, BreakerCooldown: time.Minute})
	for i := 0; i < 10; i++ { // one PR's ten pair calls all fail
		if d := r.DecidePair("s", []string{"same_change"}); d.Error == "" || strings.HasPrefix(d.Error, ErrCircuitOpen) {
			t.Fatalf("pair %d: %q", i, d.Error)
		}
	}
	if st := r.Stats(); st.State != "closed" || st.ConsecutiveFailures != 0 || st.Failures != 10 {
		t.Fatalf("ten failed pair calls must leave the breaker closed: %+v", st)
	}
	r.Decide("s", []string{"q"})
	r.Decide("s", []string{"q"})
	if st := r.Stats(); st.State != "closed" || st.ConsecutiveFailures != 2 {
		t.Fatalf("main-call failures count: %+v", st)
	}
}

type retryAfterModel struct{ n int }

func (m *retryAfterModel) Name() string { return "ra" }
func (m *retryAfterModel) Decide(_ string, qs []string) engine.Decision {
	m.n++
	if m.n == 1 {
		return engine.Decision{Probs: nan(qs), Error: "HTTP 429: slow", RetryAfter: 5 * time.Second}
	}
	if m.n == 2 {
		return engine.Decision{Probs: nan(qs), Error: "HTTP 429: slow", RetryAfter: 10 * time.Minute}
	}
	return engine.Decision{Probs: map[string]float64{"q": 0.5}}
}

func TestRetryAfterIsHonouredAndCapped(t *testing.T) {
	r, slept, _ := newRes(&retryAfterModel{}, Resilience{Retries: 2, Backoff: 100 * time.Millisecond, MaxBackoff: time.Second})
	if d := r.Decide("s", []string{"q"}); d.Error != "" {
		t.Fatal(d.Error)
	}
	if len(*slept) != 2 || (*slept)[0] != 5*time.Second || (*slept)[1] != MaxRetryAfter {
		t.Fatalf("waits %v, want [5s 30s]", *slept)
	}
}

func TestNoSharedModelKeyOverride(t *testing.T) {
	t.Setenv("MERGEONE_MODEL_KEY", "wrong-provider-key")
	t.Setenv("X_TEST_KEY", "")
	if KeyFromEnv("X_TEST_KEY") != "" {
		t.Fatal("a shared override must not stand in for a provider's own key")
	}
}
