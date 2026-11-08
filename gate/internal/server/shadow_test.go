package server

import (
	"encoding/json"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

// TestShadowModeWritesNothingToGitHub: with mode: shadow the decision is made and recorded exactly as in
// enforce mode (verdict, rule, probabilities, patch id) and the fake GitHub sees no request at all — no
// status, label, comment, or pending status.
func TestShadowModeWritesNothingToGitHub(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, logPath := newTestGateway(t, testRules+testActions+"mode: shadow\n", gh)
	if !gw.Gate.Policy.Shadow() || gw.Gate.Policy.Mode != "shadow" {
		t.Fatalf("mode not read: %q", gw.Gate.Policy.Mode)
	}
	for _, c := range []struct{ sha, state, verdict, rule string }{
		{"s1", "block", "block", "weakened-tests"},
		{"s2", "hold", "hold", "maintainer-judgement"},
		{"s3", "pass", "pass", "default"},
	} {
		res := postDecide(t, gw, 7, c.sha, c.state)
		if res.Verdict != c.verdict || res.Rule != c.rule {
			t.Fatalf("%s: verdict %s/%s, want %s/%s", c.sha, res.Verdict, res.Rule, c.verdict, c.rule)
		}
		if res.Actions != nil {
			t.Fatalf("%s: shadow decision must carry no actions report: %+v", c.sha, res.Actions)
		}
	}
	if n := f.total(); n != 0 {
		t.Fatalf("shadow mode made %d GitHub requests: %+v", n, f.reqs)
	}
	// every record is marked shadow and the stats count them
	b, _ := os.ReadFile(logPath)
	lines := strings.Split(strings.TrimSpace(string(b)), "\n")
	if len(lines) != 3 {
		t.Fatalf("want 3 records, got %d", len(lines))
	}
	for _, l := range lines {
		var rec map[string]any
		_ = json.Unmarshal([]byte(l), &rec)
		if rec["shadow"] != true {
			t.Fatalf("record not marked shadow: %s", l)
		}
	}
	st := gw.stats(time.Hour)
	if st["shadow"] != 3 || st["mode"] != "shadow" {
		t.Fatalf("stats: shadow=%v mode=%v", st["shadow"], st["mode"])
	}
	rr := httptest.NewRecorder()
	gw.Handler("").ServeHTTP(rr, httptest.NewRequest("GET", "/healthz", nil))
	if !strings.Contains(rr.Body.String(), `"mode":"shadow"`) {
		t.Fatalf("healthz should name the mode: %s", rr.Body.String())
	}
}

// TestShadowThenEnforceAppliesActions: switching the policy to enforce and re-submitting the same head applies
// the actions from the recorded decision without another model call.
func TestShadowThenEnforceAppliesActions(t *testing.T) {
	f, gh := newFakeGH(t)
	gw, _ := newTestGateway(t, testRules+testActions+"mode: shadow\n", gh)
	postDecide(t, gw, 7, "s1", "block")
	if f.total() != 0 {
		t.Fatal("shadow wrote to GitHub")
	}
	gw.Gate.Policy.Mode = "enforce"
	res := postDecide(t, gw, 7, "s1", "block")
	if res.Actions == nil || len(res.Actions.Applied) == 0 {
		t.Fatalf("enforce should apply actions on the recorded decision: %+v", res.Actions)
	}
	if f.total() == 0 {
		t.Fatal("enforce made no GitHub request")
	}
}

func TestModeValidated(t *testing.T) {
	dir := t.TempDir()
	pp := dir + "/policy.yaml"
	_ = os.WriteFile(pp, []byte(testRules+"mode: observe\n"), 0o644)
	if _, err := engine.LoadPolicy(pp); err == nil || !strings.Contains(err.Error(), "want enforce or shadow") {
		t.Fatalf("want a mode error, got %v", err)
	}
}
