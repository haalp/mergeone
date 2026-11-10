package server

import (
	"strings"
	"testing"
	"time"

	"github.com/haalp/mergeone/gate/internal/engine"
)

func TestFlightsOnePerPR(t *testing.T) {
	var f flights
	ctx1, end1, ok, old := f.begin("o/r", 1, "a")
	if !ok || old != "" {
		t.Fatal("first")
	}
	if _, _, ok, _ := f.begin("o/r", 1, "a"); ok {
		t.Fatal("the same head twice must be refused")
	}
	ctx2, end2, ok, old := f.begin("o/r", 1, "b")
	if !ok || old != "a" || ctx1.Err() == nil {
		t.Fatalf("a newer head cancels the older decision: ok=%v old=%q err=%v", ok, old, ctx1.Err())
	}
	end1() // the cancelled one ending must not unregister the new one
	if _, _, ok, _ := f.begin("o/r", 1, "b"); ok {
		t.Fatal("b is still in flight")
	}
	if f.supersede("o/r", 1, "b") != "" || ctx2.Err() != nil {
		t.Fatal("a push event for the head in flight cancels nothing")
	}
	if f.supersede("o/r", 1, "c") != "b" || ctx2.Err() == nil {
		t.Fatal("a push event for a newer head cancels the one in flight")
	}
	end2()
	if _, _, ok, _ := f.begin("o/r", 2, "a"); !ok {
		t.Fatal("another PR is independent")
	}
	if ctx, _, ok, _ := f.begin("o/r", 0, "x"); !ok || ctx == nil {
		t.Fatal("no PR number: not tracked, always allowed")
	}
}

func TestSpendCeilingsAndDayRoll(t *testing.T) {
	now := time.Date(2026, 11, 10, 23, 0, 0, 0, time.UTC)
	s := newSpend(map[string]float64{"o/a": 0.4})
	s.now = func() time.Time { return now }
	s.day = "2026-11-10"
	b := engine.BudgetSpec{DailyUSD: 1, PerRepoDailyUSD: 0.5}
	if w, _, _ := s.exceeded(b, "o/a"); w != "" {
		t.Fatal(w)
	}
	s.add("o/a", 0.1)
	if w, spent, limit := s.exceeded(b, "o/a"); w != "per_repo_daily_usd" || spent != 0.5 || limit != 0.5 {
		t.Fatalf("%s %v %v", w, spent, limit)
	}
	if w, _, _ := s.exceeded(b, "o/b"); w != "" {
		t.Fatal("another repository is under its ceiling")
	}
	s.add("o/b", 0.5)
	if w, _, _ := s.exceeded(b, "o/c"); w != "daily_usd" {
		t.Fatalf("the gateway ceiling holds every repository: %q", w)
	}
	now = now.Add(2 * time.Hour) // past 00:00 UTC
	if w, _, _ := s.exceeded(b, "o/a"); w != "" {
		t.Fatal("ceilings reset at 00:00 UTC")
	}
}

func TestBudgetHoldsWithoutCallingTheModelAndIsNotCached(t *testing.T) {
	gw, _ := newTestGateway(t, testRules+"budget: { daily_usd: 0.5 }\n", nil)
	gw.spend.add("o/r", 0.6)
	res := postDecide(t, gw, 4, "b1", "pass")
	if res.Verdict != "hold" || res.Route != "human" || res.Rule != "budget" || !strings.HasPrefix(res.Error, "BudgetExceeded: budget.daily_usd") {
		t.Fatalf("over budget must hold: %+v", res)
	}
	// the next day the same head is decided normally (the budget record is not served as a cached verdict)
	gw.spend = newSpend(nil)
	res = postDecide(t, gw, 4, "b1", "pass")
	if res.Verdict != "pass" || res.Error != "" {
		t.Fatalf("after the reset the head must be decided: %+v", res)
	}
	st := gw.stats(time.Hour)
	if _, ok := st["budget"]; !ok {
		t.Fatal("stats should carry the budget block when a ceiling is set")
	}
}
