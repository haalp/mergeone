package engine_test

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func writePolicy(t *testing.T, body string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "policy.yaml")
	if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	return p
}

const minimalPolicy = "version: 1\nmodel: mock\nquestion_set: v2\nrules: []\n"

func TestResilienceBlockParsed(t *testing.T) {
	p, err := engine.LoadPolicy(writePolicy(t, minimalPolicy+"resilience:\n  retries: 4\n  backoff: 250ms\n  breaker_failures: 0\n  breaker_cooldown: 1m\n"))
	if err != nil {
		t.Fatal(err)
	}
	if p.Resilience.Retries == nil || *p.Resilience.Retries != 4 || p.Resilience.BreakerFailures == nil || *p.Resilience.BreakerFailures != 0 {
		t.Fatalf("retries/breaker_failures not read: %+v", p.Resilience)
	}
	b, mb, cd, err := p.Resilience.Durations()
	if err != nil || b != 250*time.Millisecond || mb != 0 || cd != time.Minute {
		t.Fatalf("durations: %v %v %v %v", b, mb, cd, err)
	}
}

func TestResilienceBlockAbsentLeavesDefaultsToCaller(t *testing.T) {
	p, err := engine.LoadPolicy(writePolicy(t, minimalPolicy))
	if err != nil {
		t.Fatal(err)
	}
	if p.Resilience.Retries != nil || p.Resilience.BreakerFailures != nil || p.Resilience.Backoff != "" {
		t.Fatalf("absent block should stay nil/empty so the wrapper applies its defaults: %+v", p.Resilience)
	}
}

func TestResilienceBadDurationRejected(t *testing.T) {
	_, err := engine.LoadPolicy(writePolicy(t, minimalPolicy+"resilience:\n  backoff: soon\n"))
	if err == nil || !strings.Contains(err.Error(), "resilience.backoff") {
		t.Fatalf("want a named error, got %v", err)
	}
	_, err = engine.LoadPolicy(writePolicy(t, minimalPolicy+"resilience:\n  retries: -1\n"))
	if err == nil || !strings.Contains(err.Error(), "want >= 0") {
		t.Fatalf("want a range error, got %v", err)
	}
}

type failing struct{}

func (failing) Name() string { return "failing" }
func (failing) Decide(_ string, qs []string) engine.Decision {
	return engine.Decision{Probs: map[string]float64{}, Error: "CircuitOpen: 5 consecutive failures, retrying at 2026-11-08T12:00:30Z", Attempts: 1}
}

type flaky struct{}

func (flaky) Name() string { return "flaky" }
func (flaky) Decide(_ string, qs []string) engine.Decision {
	pr := map[string]float64{}
	for _, q := range qs {
		pr[q] = 0.1
	}
	return engine.Decision{Probs: pr, Attempts: 3, ModelVersion: "m"}
}

func TestOnErrorCarriesTheModelErrorAndRetries(t *testing.T) {
	pol, err := engine.LoadPolicy(writePolicy(t, minimalPolicy))
	if err != nil {
		t.Fatal(err)
	}
	g := &engine.Gate{Policy: pol, Model: failing{}, Cal: &engine.Calibrator{}}
	res := g.Decide("state", nil)
	if res.Verdict != "hold" || res.Route != "human" || res.Rule != "on_error" || res.Reason != "Decision model unavailable" {
		t.Fatalf("open breaker must land on on_error: %+v", res)
	}
	if !strings.HasPrefix(res.Error, "CircuitOpen") {
		t.Errorf("error should be kept on the record: %q", res.Error)
	}
	g.Model = flaky{}
	res = g.Decide("state", nil)
	if res.Error != "" || res.Retries != 2 {
		t.Fatalf("a decision that needed 3 attempts records retries=2: %+v", res)
	}
}
