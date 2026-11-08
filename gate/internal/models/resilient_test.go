package models

import (
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// scripted returns the queued errors in order ("" = success), then succeeds forever.
type scripted struct {
	errs  []string
	calls int
}

func (s *scripted) Name() string { return "scripted" }
func (s *scripted) Decide(_ string, qs []string) engine.Decision {
	s.calls++
	e := ""
	if len(s.errs) > 0 {
		e, s.errs = s.errs[0], s.errs[1:]
	}
	if e != "" {
		return engine.Decision{Probs: nan(qs), Error: e}
	}
	return engine.Decision{Probs: map[string]float64{"q": 0.5}, ModelVersion: "m1"}
}

func newRes(inner engine.Model, o Resilience) (*Resilient, *[]time.Duration, *time.Time) {
	var slept []time.Duration
	now := time.Date(2026, 11, 8, 12, 0, 0, 0, time.UTC)
	r := WithResilience(inner, o)
	r.Sleep = func(d time.Duration) { slept = append(slept, d) }
	r.Now = func() time.Time { return now }
	return r, &slept, &now
}

func TestRetryableClassification(t *testing.T) {
	for e, want := range map[string]bool{
		"":                                    false,
		"ConnectError: dial tcp: i/o timeout": true,
		"HTTP 429: slow down":                 true,
		"HTTP 503: upstream":                  true,
		"HTTP 500: boom":                      true,
		"HTTP 400: bad request":               false,
		"HTTP 401: nope":                      false,
		"bad JSON: unexpected end":            false,
		"MockError: simulated":                false,
	} {
		if got := Retryable(e); got != want {
			t.Errorf("Retryable(%q) = %v, want %v", e, got, want)
		}
	}
}

func TestRetriesTransientThenSucceeds(t *testing.T) {
	in := &scripted{errs: []string{"HTTP 429: busy", "ConnectError: reset", ""}}
	r, slept, _ := newRes(in, Resilience{Retries: 2, Backoff: 100 * time.Millisecond, MaxBackoff: time.Second})
	d := r.Decide("s", []string{"q"})
	if d.Error != "" || d.Attempts != 3 || in.calls != 3 {
		t.Fatalf("want success on attempt 3, got err=%q attempts=%d calls=%d", d.Error, d.Attempts, in.calls)
	}
	if len(*slept) != 2 {
		t.Fatalf("want 2 waits, got %v", *slept)
	}
	// first wait ≈ 100ms ±25 %, second ≈ 200ms ±25 %
	if s := (*slept)[0]; s < 75*time.Millisecond || s > 125*time.Millisecond {
		t.Errorf("first backoff %v out of range", s)
	}
	if s := (*slept)[1]; s < 150*time.Millisecond || s > 250*time.Millisecond {
		t.Errorf("second backoff %v out of range", s)
	}
	if st := r.Stats(); st.Retries != 2 || st.Failures != 0 || st.State != "closed" {
		t.Errorf("stats %+v", st)
	}
}

func TestNonRetryableReturnsAtOnce(t *testing.T) {
	in := &scripted{errs: []string{"HTTP 400: bad request"}}
	r, slept, _ := newRes(in, Resilience{Retries: 3})
	d := r.Decide("s", []string{"q"})
	if d.Error == "" || in.calls != 1 || len(*slept) != 0 || d.Attempts != 1 {
		t.Fatalf("want one call and the error back, got err=%q calls=%d slept=%v", d.Error, in.calls, *slept)
	}
}

func TestRetriesExhausted(t *testing.T) {
	in := &scripted{errs: []string{"HTTP 503: a", "HTTP 503: b", "HTTP 503: c", "HTTP 503: d"}}
	r, _, _ := newRes(in, Resilience{Retries: 2})
	d := r.Decide("s", []string{"q"})
	if !strings.HasPrefix(d.Error, "HTTP 503: c") || in.calls != 3 || d.Attempts != 3 {
		t.Fatalf("want the last error after 3 calls, got err=%q calls=%d", d.Error, in.calls)
	}
	if st := r.Stats(); st.Failures != 1 || st.ConsecutiveFailures != 1 {
		t.Errorf("stats %+v", st)
	}
}

func TestBreakerOpensShortCircuitsAndRecovers(t *testing.T) {
	fail := make([]string, 0, 20)
	for i := 0; i < 6; i++ { // 3 decisions × (1 + 1 retry) all failing
		fail = append(fail, "ConnectError: down")
	}
	in := &scripted{errs: fail}
	r, _, now := newRes(in, Resilience{Retries: 1, BreakerFailures: 3, BreakerCooldown: 30 * time.Second})
	for i := 0; i < 3; i++ {
		if d := r.Decide("s", []string{"q"}); d.Error == "" {
			t.Fatalf("decision %d should fail", i)
		}
	}
	if st := r.Stats(); st.State != "open" || st.Opened != 1 || st.ConsecutiveFailures != 3 {
		t.Fatalf("breaker should be open after 3 failures: %+v", st)
	}
	callsBefore := in.calls
	d := r.Decide("s", []string{"q"})
	if !strings.HasPrefix(d.Error, ErrCircuitOpen) || in.calls != callsBefore {
		t.Fatalf("want short circuit without a call, got err=%q calls=%d", d.Error, in.calls)
	}
	if !strings.Contains(d.Error, "retrying at 2026-11-08T12:00:30Z") {
		t.Errorf("error should name the retry time: %q", d.Error)
	}
	// still within the cooldown
	*now = now.Add(29 * time.Second)
	if d := r.Decide("s", []string{"q"}); !strings.HasPrefix(d.Error, ErrCircuitOpen) {
		t.Fatalf("should still be open: %q", d.Error)
	}
	// cooldown over: one probe goes through and succeeds → closed
	*now = now.Add(2 * time.Second)
	if st := r.Stats(); st.State != "half_open" {
		t.Errorf("want half_open before the probe, got %s", st.State)
	}
	d = r.Decide("s", []string{"q"})
	if d.Error != "" || in.calls != callsBefore+1 {
		t.Fatalf("probe should succeed: err=%q calls=%d", d.Error, in.calls)
	}
	if st := r.Stats(); st.State != "closed" || st.ConsecutiveFailures != 0 || st.ShortCircuited != 2 {
		t.Errorf("want closed after the probe: %+v", st)
	}
}

func TestBreakerProbeFailureReopens(t *testing.T) {
	in := &scripted{errs: []string{"HTTP 500: a", "HTTP 500: b", "HTTP 500: probe"}}
	r, _, now := newRes(in, Resilience{Retries: 0, BreakerFailures: 2, BreakerCooldown: 10 * time.Second})
	r.Decide("s", []string{"q"})
	r.Decide("s", []string{"q"})
	*now = now.Add(11 * time.Second)
	if d := r.Decide("s", []string{"q"}); !strings.HasPrefix(d.Error, "HTTP 500: probe") {
		t.Fatalf("probe should reach the endpoint: %q", d.Error)
	}
	st := r.Stats()
	if st.State != "open" || st.RetryAt != "2026-11-08T12:00:21Z" || st.Opened != 2 {
		t.Errorf("failed probe should reopen for another cooldown: %+v", st)
	}
}

func TestBreakerDisabled(t *testing.T) {
	in := &scripted{errs: []string{"HTTP 500: a", "HTTP 500: b", "HTTP 500: c", "HTTP 500: d", "HTTP 500: e", "HTTP 500: f"}}
	r, _, _ := newRes(in, Resilience{Retries: 0, BreakerFailures: 0})
	for i := 0; i < 6; i++ {
		if d := r.Decide("s", []string{"q"}); strings.HasPrefix(d.Error, ErrCircuitOpen) {
			t.Fatal("breaker_failures 0 must never short-circuit")
		}
	}
	if in.calls != 6 {
		t.Errorf("want 6 calls, got %d", in.calls)
	}
}
