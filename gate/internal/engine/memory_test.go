package engine

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

type fakeMem struct{}

func (fakeMem) Candidates(meta PRMeta, aState string, k int) []Candidate {
	return []Candidate{{Number: 41, Score: 0.9, State: "MERGED", Title: "same thing", PairState: "PAIR:41"}, {Number: 42, Score: 0.3, PairState: "PAIR:42"}}
}

type fakeModel struct{}

func (fakeModel) Name() string { return "fake" }
func (fakeModel) Decide(state string, qs []string) Decision {
	if len(qs) == 1 && qs[0] == "same_change" {
		if state == "PAIR:41" {
			return Decision{Probs: map[string]float64{"same_change": 0.85}}
		}
		return Decision{Probs: map[string]float64{"same_change": 0.1}}
	}
	return Decision{Probs: map[string]float64{"is_logic_change": 0.1, "weakens_tests": 0.01, "message_matches_diff": 0.9, "touches_concurrency": 0.02, "needs_human_review": 0.1}}
}

func testPolicy(t *testing.T) *Policy {
	p := &Policy{Rules: []Rule{
		{Name: "duplicate-of", When: map[string]string{"duplicate_of": ">= 0.7"}, Verdict: "hold", Route: "dedup", Reason: "dup"},
		{Name: "stale", When: map[string]string{"silent_days": ">= 14"}, Verdict: "hold", Route: "stale", Reason: "stale"},
		{Name: "trivial", When: map[string]string{"is_logic_change": "<= 0.2"}, Verdict: "pass", Route: "auto-merge", Reason: "trivial"},
	}, Default: Outcome{"pass", "auto", ""}, OnError: Outcome{"hold", "human", "err"}}
	if err := p.compile(); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestDecideForDuplicateAndStale(t *testing.T) {
	g := &Gate{Policy: testPolicy(t), Model: fakeModel{}, Cal: &Calibrator{}, Memory: fakeMem{}, PairK: 5}
	res := g.DecideFor("state", PRMeta{Repo: "o/n", Number: 50, UpdatedAt: time.Now().Add(-time.Hour)})
	if res.Rule != "duplicate-of" || res.Verdict != "hold" || res.Route != "dedup" {
		t.Fatalf("expected dedup hold, got %+v", res)
	}
	if res.Probs["duplicate_of"] != 0.85 || res.Extra["duplicate_candidate"] != 41 {
		t.Fatalf("duplicate_of/candidate not recorded: %v %v", res.Probs, res.Extra)
	}
	g2 := &Gate{Policy: testPolicy(t), Model: fakeModel{}, Cal: &Calibrator{}}
	res = g2.DecideFor("state", PRMeta{Repo: "o/n", UpdatedAt: time.Now().AddDate(0, 0, -20)})
	if res.Rule != "stale" {
		t.Fatalf("expected stale, got %s", res.Rule)
	}
	res = g2.DecideFor("state", PRMeta{Repo: "o/n", UpdatedAt: time.Now()})
	if res.Rule != "trivial" || res.Verdict != "pass" {
		t.Fatalf("expected trivial pass without memory, got %s/%s", res.Rule, res.Verdict)
	}
}

// fixtureMem returns one candidate whose pair state is the Python run4.pair_state from the golden fixture.
type fixtureMem struct{ pair string }

func (m fixtureMem) Candidates(PRMeta, string, int) []Candidate {
	return []Candidate{{Number: 7, Score: 0.9, State: "MERGED", PairState: m.pair}}
}

type pairRecorder struct {
	mu   sync.Mutex
	sent []string
}

func (*pairRecorder) Name() string { return "rec" }
func (r *pairRecorder) Decide(state string, qs []string) Decision {
	if len(qs) == 1 && qs[0] == "same_change" {
		r.mu.Lock()
		r.sent = append(r.sent, state)
		r.mu.Unlock()
		return Decision{Probs: map[string]float64{"same_change": 0.2}}
	}
	return Decision{Probs: map[string]float64{"is_logic_change": 0.1}}
}

// memory.pair_state: the pair call gets the C1 compact rendering by default (identical to the Python
// compact_pair_state, golden fixture), the full one under `full`; the main call always gets the full state.
func TestPairCallCompactByDefault(t *testing.T) {
	b, err := os.ReadFile(filepath.Join("..", "..", "testdata", "compact_fixture.json"))
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Cases []struct {
			AState           string `json:"a_state"`
			PairState        string `json:"pair_state"`
			CompactPairState string `json:"compact_pair_state"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(b, &f); err != nil {
		t.Fatal(err)
	}
	c := f.Cases[1]
	for _, mode := range []string{"", PairStateCompact, PairStateFull} {
		p := testPolicy(t)
		p.Memory.PairState = mode
		rec := &pairRecorder{}
		g := &Gate{Policy: p, Model: rec, Cal: &Calibrator{}, Memory: fixtureMem{c.PairState}}
		res := g.DecideFor(c.AState, PRMeta{Repo: "o/n", Number: 9})
		want, wantRender := c.CompactPairState, "compact"
		if mode == PairStateFull {
			want, wantRender = c.PairState, "full"
		}
		if len(rec.sent) != 1 || rec.sent[0] != want {
			t.Fatalf("mode %q: pair call got the wrong rendering", mode)
		}
		if res.Extra["render"] != wantRender || res.Extra["render_fallback"] != nil {
			t.Fatalf("mode %q: extra %v", mode, res.Extra)
		}
	}
	// A state the compact rules cannot parse is sent full and counted.
	g := &Gate{Policy: testPolicy(t), Model: fakeModel{}, Cal: &Calibrator{}, Memory: fakeMem{}, PairK: 5}
	res := g.DecideFor("state", PRMeta{Repo: "o/n", Number: 50})
	if res.Extra["render"] != "compact" || res.Extra["render_fallback"] != 2 || res.Rule != "duplicate-of" {
		t.Fatalf("fallback: %v %s", res.Extra, res.Rule)
	}
}

func TestPolicyPairStateKey(t *testing.T) {
	dir := t.TempDir()
	write := func(body string) string {
		p := filepath.Join(dir, "p.yaml")
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		return p
	}
	p, err := LoadPolicy(write("memory: {enabled: true}\n"))
	if err != nil || p.Memory.PairState != PairStateCompact {
		t.Fatalf("default: %v %v", err, p)
	}
	if p, err = LoadPolicy(write("memory: {pair_state: full}\n")); err != nil || p.Memory.PairState != PairStateFull {
		t.Fatalf("full: %v", err)
	}
	if _, err = LoadPolicy(write("memory: {pair_state: short}\n")); err == nil {
		t.Fatal("bad pair_state accepted")
	}
}
